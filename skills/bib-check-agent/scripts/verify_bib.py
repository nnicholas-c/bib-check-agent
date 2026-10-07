#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["requests", "rapidfuzz", "bibtexparser<2"]
# ///
"""
verify_bib.py - check every entry of a .bib file against real paper databases.
Part of Bib Check Agent (https://github.com/nnicholas-c/bib-check-agent), MIT licensed.

For each reference the script looks the paper up in Semantic Scholar, DBLP, Crossref, OpenAlex,
Europe PMC and arXiv, plus DataCite, Open Library, OpenReview and CORE for entries the others
leave short of two confirmations, decides whether the entry is real and
correct, and writes two files next to your .bib:

  <name>.verified.bib  your file, where every confirmed entry is replaced by clean BibTeX
                       from the source. Citation keys never change, so \\cite{...} still works.
                       Flagged entries keep your original text plus a comment saying why.
  <name>.report.md     what was checked, what looks wrong, suggested fixes, and links
                       (including a Google Scholar link) for checking by hand.

Install:  pip install requests rapidfuzz "bibtexparser<2"   (or skip this and use: uv run verify_bib.py ...)
Run:      python verify_bib.py reference.bib
Review:   python verify_bib.py reference.bib -i     (walk through flagged entries one by one,
                                                    accept the fix, paste BibTeX you copied from
                                                    Google Scholar, keep yours, or mark as fake)
Fetch:    python verify_bib.py --bibtex 10.18653/v1/N19-1423 --key devlin2019bert
                                                   (print the official BibTeX for a DOI, an arXiv
                                                    ID, or a URL that serves a .bib export, cleaned
                                                    the same way and under the key you give)
Search:   python verify_bib.py --search "dropout prevents co-adaptation Srivastava"
                                                   (list candidate papers with abstracts from
                                                    OpenAlex, Semantic Scholar, Crossref, arXiv,
                                                    Europe PMC, DataCite, OpenReview and Open Library)

Optional environment variables
  S2_API_KEY        free Semantic Scholar key, makes runs faster and steadier
  OPENALEX_API_KEY  free OpenAlex key, for more than OpenAlex's small daily allowance
  CORE_API_KEY      free CORE key, for more than 100 CORE searches a day
  VERIFY_BIB_EMAIL  your email, sent to Crossref and OpenAlex for their faster "polite" rate limits

Your original .bib file is never modified. Lookups are cached in .verify_bib_cache.json, so a
second run is fast.
"""

import argparse
import atexit
import html
import json
import os
import re
import sys
import time
import unicodedata
import webbrowser
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import quote, quote_plus, urlparse

# A missing package is reported after argument parsing, so --help works before installing anything.
try:
    import requests
    from rapidfuzz import fuzz
    import bibtexparser
    from bibtexparser.bparser import BibTexParser
    MISSING = None if bibtexparser.__version__.startswith("1.") else 'This script needs bibtexparser 1.x. Run:  pip install "bibtexparser<2"'
except ImportError:
    MISSING = 'Missing packages. Run:  pip install requests rapidfuzz "bibtexparser<2"'

S2_KEY = os.environ.get("S2_API_KEY")
OPENALEX_KEY = os.environ.get("OPENALEX_API_KEY")
CORE_KEY = os.environ.get("CORE_API_KEY")

TITLE_OK = 95     # title similarity (0 to 100) needed to call a title correct
TITLE_CLOSE = 80  # below this, a search result is treated as a different paper

# Seconds between requests to each host. These follow each service's published limits.
MIN_INTERVAL = {
    "api.semanticscholar.org": 1.1 if S2_KEY else 2.5,
    "api.crossref.org": 1.0,
    "doi.org": 0.5,
    "export.arxiv.org": 3.0,
    "arxiv.org": 3.0,
    "sparql.dblp.org": 10.0,  # dblp robots.txt asks for a 10 second crawl delay
    "api.openalex.org": 0.2,
    "www.ebi.ac.uk": 0.3,        # Europe PMC allows 10 a second
    "api.datacite.org": 0.7,     # 500 per 5 minutes without an account
    "openlibrary.org": 1.0,      # 1 a second for scripts that send no email
    "api2.openreview.net": 1.0,
    "api.openreview.net": 12.5,  # the older OpenReview API allows 5 searches a minute
    "api.core.ac.uk": 6.5,       # 10 a minute and 100 a day without a key
}

NOISE_FIELDS = {"timestamp", "biburl", "bibsource", "collection", "abstract", "keywords",
                "file", "language", "issn", "copyright", "urldate", "langid"}
FIELD_ORDER = ["author", "editor", "title", "booktitle", "journal", "series", "volume", "number",
               "pages", "publisher", "address", "organization", "school", "institution",
               "howpublished", "year", "month", "url", "doi", "eprinttype", "eprint",
               "archiveprefix", "primaryclass", "isbn", "note"]

# Common CS venues, so the script can notice "your entry says ICML but it was NeurIPS".
# More specific patterns come first (NAACL before ACL, for example).
VENUES = [
    ("NAACL", [r"\bnaacl\b", r"north american chapter of the association"]),
    ("EACL", [r"\beacl\b", r"european chapter of the association"]),
    ("EMNLP", [r"\bemnlp\b", r"empirical methods in natural language processing"]),
    ("ACL", [r"\bacl\b", r"annual meeting of the association for computational linguistics"]),
    ("NeurIPS", [r"\bneurips\b", r"\bnips\b", r"neural information processing systems"]),
    ("ICLR", [r"\biclr\b", r"international conference on learning representations"]),
    ("ICML", [r"\bicml\b", r"international conference on machine learning\b(?! and appl)"]),
    ("AAAI", [r"\baaai\b"]),
    ("IJCAI", [r"\bijcai\b", r"international joint conference on artificial intelligence"]),
    ("CVPR", [r"\bcvpr\b", r"computer vision and pattern recognition"]),
    ("ICCV", [r"\biccv\b", r"international conference on computer vision"]),
    ("ECCV", [r"\beccv\b", r"european conference on computer vision"]),
    ("CHI", [r"\bchi\b", r"human factors in computing systems"]),
    ("UIST", [r"\buist\b", r"user interface software and technology"]),
    ("CSCW", [r"\bcscw\b", r"computer.supported cooperative work"]),
    ("KDD", [r"\bkdd\b", r"knowledge discovery and data mining"]),
    ("COLM", [r"\bcolm\b", r"conference on language modeling"]),
    ("TMLR", [r"\btmlr\b", r"transactions on machine learning research"]),
    ("JMLR", [r"\bjmlr\b", r"journal of machine learning research"]),
    ("AISTATS", [r"\baistats\b", r"artificial intelligence and statistics"]),
    ("UAI", [r"\buai\b", r"uncertainty in artificial intelligence"]),
    ("COLT", [r"\bcolt\b", r"conference on learning theory"]),
    ("CoRL", [r"\bcorl\b", r"conference on robot learning"]),
    ("arXiv", [r"\barxiv\b", r"\bcorr\b"]),
]

MONTHS = {m[:3].lower(): m for m in ("January February March April May June July August September "
                                      "October November December").split()}
# When two records match equally well, show the more carefully curated one.
SOURCE_RANK = {"DBLP": 3, "Crossref": 2, "Europe PMC": 2, "Semantic Scholar": 1, "OpenAlex": 1, "arXiv": 1,
               "OpenReview": 1, "DataCite": 1, "CORE": 0, "Open Library": 0}
WEB_TYPES = {"misc", "online", "software", "manual", "electronic", "www", "webpage", "dataset"}

STOPWORDS = set("a an the of for and in on to with by from at as is are be via using towards "
                "toward into over under its their our your we you all can not do does".split())


# ----------------------------------------------------------------------------------------
# HTTP with per-host pacing, retries and a local cache
# ----------------------------------------------------------------------------------------

class Http:
    def __init__(self, cache_file, use_cache, email):
        self.session = requests.Session()
        ua = "bib-check-agent (https://github.com/nnicholas-c/bib-check-agent)"
        self.session.headers["User-Agent"] = ua  # the email goes only to Crossref and OpenAlex, as their mailto parameter
        self.email = email
        self.cache_file = cache_file
        self.use_cache = use_cache
        self.cache = {}
        if use_cache and cache_file.exists():
            try:
                self.cache = json.loads(cache_file.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                self.cache = {}
        self.dirty = 0
        self.next_time = defaultdict(float)
        self.down = {}  # host -> why we stopped calling it for the rest of this run
        if email:
            MIN_INTERVAL["api.crossref.org"] = 0.4
        atexit.register(self.save)

    def save(self):
        if self.use_cache and self.dirty:
            tmp = self.cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.cache), encoding="utf-8")
            tmp.replace(self.cache_file)
            self.dirty = 0

    def _wait(self, host):
        now = time.time()
        if self.next_time[host] > now:
            time.sleep(self.next_time[host] - now)
        self.next_time[host] = time.time() + MIN_INTERVAL.get(host, 0.5)

    def get(self, url, params=None, headers=None, data=None, method="GET", tries=5,
            retry_429=True, cache=True):
        """Return (status, text). Status 0 means the request never got an answer."""
        params = dict(params or {})
        if self.email and ("api.crossref.org" in url or "api.openalex.org" in url):
            params["mailto"] = self.email
        if OPENALEX_KEY and "api.openalex.org" in url:
            params["api_key"] = OPENALEX_KEY
        visible = {k: v for k, v in params.items() if k not in ("api_key", "mailto")}
        key = json.dumps([method, url, sorted(visible.items()), data], default=str)
        if cache and self.use_cache and key in self.cache:
            return tuple(self.cache[key])
        host = urlparse(url).netloc
        if host in self.down:
            return 429, ""
        status, text = 0, ""
        for attempt in range(tries):
            self._wait(host)
            retry_after = 0.0
            try:
                r = self.session.request(method, url, params=params or None, data=data,
                                         headers=headers, timeout=45)
                status, text = r.status_code, r.text
                try:
                    retry_after = float(r.headers.get("Retry-After", 0) or 0)
                except ValueError:
                    retry_after = 0.0
            except requests.RequestException as e:
                status, text = 0, str(e)
            if status == 429 and retry_after > 120:
                # The host asks us to stay away for minutes or hours (for example a used-up daily quota).
                # Waiting would stall the whole run, so stop calling it and say so in the report.
                self.down[host] = f"it asked us to wait {retry_after / 3600:.1f} hours (rate limit or used-up quota)"
                print(f"  {host} is refusing requests ({self.down[host]}); continuing without it.", flush=True)
                break
            if status == 429 and not retry_429:
                break
            if status in (0, 429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(min(60.0, max(retry_after, 2.0 ** (attempt + 1))))
                continue
            break
        if status in (0, 401, 403, 407) and host not in CORE and host not in self.down:
            # Blocked or offline after every retry. Stop asking it, and say so in the report.
            self.down[host] = "it did not answer (blocked or offline)" if status == 0 else f"it refused access (HTTP {status})"
            print(f"  {host}: {self.down[host]}; continuing without it.", flush=True)
        if cache and self.use_cache and status in (200, 404):
            self.cache[key] = [status, text]
            self.dirty += 1
            if self.dirty >= 20:
                self.save()
        return status, text

    def json(self, url, **kw):
        status, text = self.get(url, **kw)
        try:
            return status, json.loads(text) if text else None
        except ValueError:
            return status, None  # an HTML page (for example a bot check) instead of JSON


# ----------------------------------------------------------------------------------------
# Text helpers
# ----------------------------------------------------------------------------------------

LATEX_LETTERS = {"ss": "ss", "oe": "oe", "OE": "OE", "ae": "ae", "AE": "AE", "aa": "a",
                 "AA": "A", "o": "o", "O": "O", "l": "l", "L": "L", "i": "i", "j": "j"}


def clean_latex(s):
    """Turn a LaTeX-ish BibTeX value into plain text (accents dropped, braces removed)."""
    if not s:
        return ""
    s = re.sub(r"\\([&%_$#])", r"\1", s)
    s = re.sub(r"\\(ss|oe|OE|ae|AE|aa|AA|o|O|l|L|i|j)(?![A-Za-z])",
               lambda m: LATEX_LETTERS[m.group(1)], s)
    s = re.sub(r"\\[`'^\"~=.]\s*(?:\{\s*([A-Za-z])\s*\}|([A-Za-z]))",
               lambda m: m.group(1) or m.group(2), s)
    s = re.sub(r"\\[uvHcdbkrt](?:\s*\{\s*([A-Za-z])\s*\}|\s+([A-Za-z]))",
               lambda m: m.group(1) or m.group(2), s)
    s = re.sub(r"\\[A-Za-z]+\*?", " ", s)
    s = s.replace("~", " ")
    s = re.sub(r"[{}$]", "", s)
    return re.sub(r"\s+", " ", s).strip()


FOLD = str.maketrans({"\u0142": "l", "\u0141": "L", "\u00f8": "o", "\u00d8": "O", "\u00df": "ss",
                      "\u00e6": "ae", "\u00c6": "AE", "\u0111": "d", "\u0131": "i"})


def fold(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).translate(FOLD)


RETRACTION_LABEL = re.compile(r"^\s*(retracted|withdrawn)(\s+(article|paper|publication))?\s*[:.\-]+\s*", re.I)


def norm_title(s):
    s = RETRACTION_LABEL.sub("", fold(clean_latex(s)))  # "RETRACTED: <title>" names the same paper
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())


def title_sim(a, b):
    a, b = norm_title(a), norm_title(b)
    if not a or not b:
        return 0
    score = max(fuzz.ratio(a, b), fuzz.token_sort_ratio(a, b))
    short, long_ = sorted((a, b), key=len)
    if (score < 88 and len(short) >= 20 and len(short) >= 0.3 * len(long_)
            and fuzz.partial_ratio(short, long_) >= 97):
        score = 88  # one title contains the other: a subtitle was dropped or added
    return round(score)


def to_year(v):
    m = re.search(r"\b(1[89]\d\d|20\d\d)\b", str(v or ""))
    return int(m.group(1)) if m else None


def strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def strip_doi(d):
    d = (d or "").strip().rstrip(".")
    return re.sub(r"^((https?://)?(www\.|dx\.)?doi\.org/|doi:\s*)", "", d, flags=re.I)


def venue_code(text):
    t = clean_latex(text or "").lower()
    for code, patterns in VENUES:
        if any(re.search(p, t) for p in patterns):
            return code
    return None


def short(s, n=90):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 3] + "..."


# ----------------------------------------------------------------------------------------
# Authors
# ----------------------------------------------------------------------------------------

def split_authors(s):
    """Split a BibTeX author field on ' and ' that is not inside braces."""
    s = re.sub(r"\s+", " ", s or "").strip()
    out, depth, start, i, low = [], 0, 0, 0, s.lower()
    while i < len(s):
        c = s[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif depth == 0 and low.startswith(" and ", i):
            out.append(s[start:i])
            i += 5
            start = i
            continue
        i += 1
    out.append(s[start:])
    return [a.strip() for a in out if a.strip()]


def _key(s):
    return re.sub(r"[^a-z]", "", fold(clean_latex(s)).lower())


def surname_keys(name):
    """Possible surnames for a name, as lowercase ASCII. 'van den Oord, Aaron' -> {'oord'}."""
    name = re.sub(r"\s+\d{4}$", "", (name or "").strip())  # dblp homonym numbers: "Wei Wang 0001"
    if not name or name in ("...", "\u2026"):
        return set()
    if name.startswith("{") and name.endswith("}") and name.count("{") == 1:
        toks = clean_latex(name).split()
        return {k for k in (_key(name), _key(toks[-1]) if toks else "") if k}  # corporate author such as {OpenAI},
        # with its last word too, which is how a database that doesn't brace the name parses it
    if "," in name:
        last = clean_latex(name.split(",")[0])
        toks = last.split()
        return {_key(toks[-1] if toks else last)}
    toks = [t for t in clean_latex(name).split() if t.lower().strip(".") not in {"jr", "sr", "ii", "iii", "iv"}]
    if not toks:
        return set()
    keys = {_key(toks[-1])}
    if len(toks) == 2:
        keys.add(_key(toks[0]))  # allow "Family Given" order for two-part names
    return {k for k in keys if k}


def _name_hit(name, cand_keys):
    for k in surname_keys(name):
        if k in cand_keys:
            return True
        if len(k) >= 5 and any(len(c) >= 5 and fuzz.ratio(k, c) >= 88 for c in cand_keys):
            return True
    return False


@dataclass
class AuthorCheck:
    frac: float
    missing: list
    first_ok: bool


def author_check(entry_authors, cand):
    ents = [a for a in entry_authors if a.lower().strip(".") not in ("others", "et al")]
    cand_names = [c for c in cand.authors if c and c not in ("...", "\u2026")]
    if not ents or not cand_names:
        return None
    cand_keys = set().union(*(surname_keys(c) for c in cand_names))
    hits = [_name_hit(a, cand_keys) for a in ents]
    first_ok = _name_hit(ents[0], surname_keys(cand_names[0]))
    return AuthorCheck(sum(hits) / len(ents), [a for a, h in zip(ents, hits) if not h], first_ok)


def surnames_for_display(names, n=4):
    out = []
    for a in names[:n]:
        a = clean_latex(re.sub(r"\s+\d{4}$", "", a))
        out.append(a.split(",")[0].strip() if "," in a else (a.split() or [a])[-1])
    return "; ".join(out) + ("; ..." if len(names) > n else "")


# ----------------------------------------------------------------------------------------
# Data model
# ----------------------------------------------------------------------------------------

@dataclass
class Cand:
    """One paper record found in a database."""
    source: str
    title: str
    authors: list
    year: int = None
    venue: str = ""
    doi: str = None
    arxiv: str = None
    dblp: str = None
    url: str = None
    bibtex: str = None
    preprint: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class Ref:
    """One entry of the user's .bib file and everything learned about it."""
    key: str
    etype: str
    raw: str
    fields: dict
    title: str = ""
    authors: list = field(default_factory=list)
    year: int = None
    doi: str = None
    arxiv: str = None
    venue: str = None
    cands: list = field(default_factory=list)
    id_problems: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    status: str = "UNCHECKED"
    problems: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    best: Cand = None
    best_score: int = 0
    suggestion: tuple = None   # (entrytype, fields, source)
    decision: str = ""         # auto, accept, paste, keep, fake, skip
    pasted: tuple = None
    confirmed_by: list = field(default_factory=list)  # databases whose record matches the entry
    searched: list = field(default_factory=list)      # databases that answered a search for it


ARXIV_NEW = r"(\d{4}\.\d{4,5})(?:v\d+)?"


def find_arxiv(f):
    eprint = f.get("eprint", "").strip()
    m = re.fullmatch(ARXIV_NEW, eprint) or re.fullmatch(r"([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", eprint)
    if m:
        return m.group(1)
    blob = " ".join(f.get(k, "") for k in ("journal", "url", "note", "howpublished", "volume",
                                           "booktitle", "doi", "publisher"))
    for pat in (r"arxiv\.org/(?:abs|pdf)/" + ARXIV_NEW, r"arxiv\W{0,3}(?:preprint\W{0,3})?(?:arxiv\W{0,3})?" + ARXIV_NEW,
                r"abs/" + ARXIV_NEW, r"10\.48550/arxiv\." + ARXIV_NEW):
        m = re.search(pat, blob, re.I)
        if m:
            return m.group(1)
    return None


def find_doi(f):
    d = f.get("doi", "")
    if not d:
        m = re.search(r"doi\.org/(10\.\S+)", f.get("url", ""), re.I)
        d = m.group(1) if m else ""
    d = strip_doi(d)
    return d if d.startswith("10.") else None


def make_ref(key, etype, raw, fields):
    ref = Ref(key=key, etype=etype, raw=raw, fields=fields)
    ref.title = clean_latex(fields.get("title", ""))
    ref.authors = split_authors(fields.get("author", "") or fields.get("editor", ""))
    ref.year = to_year(fields.get("year") or fields.get("date"))
    ref.doi = find_doi(fields)
    ref.arxiv = find_arxiv(fields)
    if ref.doi and ref.doi.lower().startswith("10.48550/arxiv."):
        ref.arxiv = ref.arxiv or ref.doi.split(".", 2)[-1]
        ref.doi = None
    text = " ".join(fields.get(k, "") for k in ("booktitle", "journal", "howpublished", "series",
                                                 "publisher", "note", "organization"))
    ref.venue = venue_code(text) or ("arXiv" if ref.arxiv and not (fields.get("booktitle") or fields.get("journal")) else None)
    return ref


# ----------------------------------------------------------------------------------------
# Reading the .bib file
# ----------------------------------------------------------------------------------------

def read_bib(src):
    """Decode strictly, since the verified copy may replace the original and a lossy decode would
    destroy text. read_text also turns CRLF into \\n, so write_text doesn't double the \\r."""
    try:
        return src.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as e:
        sys.exit(f"{src} is not UTF-8 (byte {e.start}); it is probably Latin-1 or cp1252. Convert it to UTF-8 "
                 "first (for example: iconv -f cp1252 -t utf-8), make sure the paper loads it as UTF-8, then rerun.")


def scan_bib(text):
    """Split a .bib file into blocks, keeping the exact original text of each one."""
    blocks, i, n = [], 0, len(text)
    while i < n:
        at = text.find("@", i)
        if at < 0:
            blocks.append(("other", None, None, text[i:]))
            break
        if at > i:
            blocks.append(("other", None, None, text[i:at]))
        m = re.match(r"@\s*([A-Za-z]+)\s*([{(])", text[at:])
        if not m:
            blocks.append(("other", None, None, text[at:at + 1]))
            i = at + 1
            continue
        kind, opener = m.group(1).lower(), m.group(2)
        j, depth = at + m.end(), 1
        while j < n and depth:
            c = text[j]
            if c == "{" and opener == "{":
                depth += 1
            elif c == "}" and opener == "{":
                depth -= 1
            elif opener == "(" and c in "()":
                depth += 1 if c == "(" else -1
            j += 1
        raw = text[at:j]
        if kind in ("string", "preamble", "comment"):
            blocks.append((kind, None, None, raw))
        else:
            # A key with no comma after it still gets a key, so the entry is reported as unparseable
            km = re.match(r"\s*([^,\s]+)\s*,", text[at + m.end(): j]) or re.match(r"\s*([^,\s{}()]+)", text[at + m.end(): j])
            blocks.append(("entry", kind, km.group(1) if km else None, raw))
        i = j
    return blocks


def parse_entry(raw, strings=""):
    """Parse one entry. If it uses an undefined macro (Crossref writes month=July, for example),
    retry with bare word values wrapped in braces, leaving your own @string macros alone."""
    defined = {m.lower() for m in re.findall(r"@string\s*[{(]\s*([A-Za-z][\w\-]*)\s*=", strings, flags=re.I)}

    def wrap(m):
        return m.group(0) if m.group(2).lower() in defined else f"{m.group(1)}{{{m.group(2)}}}{m.group(3)}"

    for text in (raw, re.sub(r"(=\s*)([A-Za-z][\w\-]*)(\s*[,}])", wrap, raw)):
        parser = BibTexParser(common_strings=True, ignore_nonstandard_types=False)
        parser.homogenize_fields = False
        try:
            db = bibtexparser.loads(strings + "\n" + text, parser=parser)
        except Exception:
            continue
        if db.entries:
            return db.entries[0]
    return None


# ----------------------------------------------------------------------------------------
# Sources
# ----------------------------------------------------------------------------------------

S2_FIELDS = "title,authors,year,venue,publicationVenue,externalIds,citationStyles,url"


def s2_match(http, title):
    headers = {"x-api-key": S2_KEY} if S2_KEY else None
    st, j = http.json("https://api.semanticscholar.org/graph/v1/paper/search/match",
                      params={"query": title[:300], "fields": S2_FIELDS}, headers=headers, tries=5)
    if st != 200 or not j or not j.get("data"):
        return [], st
    d = j["data"][0]
    ext = d.get("externalIds") or {}
    venue = d.get("venue") or (d.get("publicationVenue") or {}).get("name") or ""
    return [Cand("Semantic Scholar", d.get("title") or "", [a.get("name", "") for a in d.get("authors") or []],
                 d.get("year"), venue, ext.get("DOI"), ext.get("ArXiv"), ext.get("DBLP"), d.get("url"),
                 (d.get("citationStyles") or {}).get("bibtex"),
                 preprint=(not venue or "arxiv" in venue.lower()))], st


def crossref_cand(it):
    authors = []
    for a in it.get("author") or []:
        authors.append(" ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name", ""))
    year = None
    for k in ("issued", "published-print", "published-online", "created"):
        parts = (it.get(k) or {}).get("date-parts") or [[None]]
        if parts and parts[0] and parts[0][0]:
            year = parts[0][0]
            break
    doi = it.get("DOI")
    title = " ".join(it.get("title") or [])
    if it.get("subtitle"):  # Crossref keeps subtitles apart ("XGBoost" + "A Scalable Tree Boosting System")
        title += ": " + " ".join(it["subtitle"])
    return Cand("Crossref", strip_tags(title), authors, year,
                strip_tags(" ".join(it.get("container-title") or [])), doi, None, None,
                f"https://doi.org/{doi}" if doi else None, preprint=(it.get("type") == "posted-content"))


def crossref_doi(http, doi):
    st, j = http.json(f"https://api.crossref.org/works/{quote(doi, safe='/')}")
    if st == 404:
        return None, "missing"
    if st != 200 or not j or "message" not in j:
        return None, "error"
    return crossref_cand(j["message"]), "ok"


def crossref_search(http, title, first_author):
    q = title + (" " + first_author if first_author else "")
    st, j = http.json("https://api.crossref.org/works",
                      params={"query.bibliographic": q[:400], "rows": 5,
                              "select": "DOI,title,subtitle,author,issued,published-print,published-online,container-title,type"})
    if st != 200 or not j:
        return [], st
    return [crossref_cand(it) for it in (j.get("message") or {}).get("items", [])], st


def doi_exists(http, doi):
    st, _ = http.get(f"https://doi.org/api/handles/{quote(doi, safe='/')}")
    return True if st == 200 else False if st == 404 else None


def arxiv_by_id(http, aid):
    # The API wants old-style IDs without the subject class: math.GT/0309136 -> math/0309136
    qid = re.sub(r"^([a-z\-]+)\.[A-Z]{2}/", r"\1/", aid)
    st, txt = http.get("https://export.arxiv.org/api/query", params={"id_list": qid, "max_results": 1})
    if st != 200:
        return None, "error"
    try:
        root = ET.fromstring(txt)
    except ET.ParseError:
        return None, "error"
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    e = root.find("a:entry", ns)
    title = " ".join((e.findtext("a:title", "", ns) if e is not None else "").split())
    if e is None or not title or title.lower() == "error" or "arxiv.org/api/errors" in (e.findtext("a:id", "", ns)):
        return None, "missing"
    authors = [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)]
    jref = e.findtext("x:journal_ref", "", ns) or ""
    return Cand("arXiv", title, authors, to_year(e.findtext("a:published", "", ns)),
                "arXiv" + (f" ({jref})" if jref else ""), e.findtext("x:doi", None, ns), aid, None,
                f"https://arxiv.org/abs/{aid}", preprint=True), "ok"


# dblp: its website now sits behind a bot check, so we use the official SPARQL endpoint,
# which dblp's robots.txt explicitly allows. Queries are batched to keep the request count low.

SPARQL = "https://sparql.dblp.org/sparql"
DBLP_REC = "https://dblp.org/rec/"
PFX = ("PREFIX dblp: <https://dblp.org/rdf/schema#>\n"
       "PREFIX ql: <http://qlever.cs.uni-freiburg.de/builtin-functions/>\n")


def dblp_words(title, k=4):
    toks = re.findall(r"\w+", clean_latex(title).lower())
    toks = [t for t in toks if t.isascii() and t.isalnum() and len(t) >= 3 and t not in STOPWORDS]
    return sorted(set(toks), key=lambda t: (-len(t), t))[:k]


def dblp_norm(title):
    return re.sub(r"[^a-z0-9]", "", clean_latex(title).lower())


def sparql(http, query):
    st, txt = http.get(SPARQL, method="POST", data={"query": PFX + query},
                       headers={"Accept": "application/sparql-results+json"}, retry_429=False, tries=3)
    if st != 200:
        return None
    try:
        return json.loads(txt)["results"]["bindings"]
    except (ValueError, KeyError):
        return None


def dblp_find(http, items, answered=None):
    """items: list of (ref index, title). Returns (ref index, dblp key) pairs with the same title,
    and adds to `answered` the ref indexes whose query got an answer."""
    blocks, asked = [], []
    for i, t in items:
        words, n = dblp_words(t), dblp_norm(t)
        if words and len(n) >= 8:
            asked.append(i)
            blocks.append(f'{{ BIND({i} AS ?seed) ?text ql:contains-entity ?title . '
                          f'?text ql:contains-word "{" ".join(words)}" . ?pub dblp:title ?title . '
                          f'FILTER(REPLACE(LCASE(STR(?title)), "[^a-z0-9]", "") = "{n}") }}')
    if not blocks:
        return []
    rows = sparql(http, "SELECT ?seed ?pub WHERE { " + " UNION ".join(blocks) + " }")
    if rows is not None:
        if answered is not None:
            answered.update(asked)
        return [(int(r["seed"]["value"]), r["pub"]["value"].replace(DBLP_REC, "")) for r in rows]
    if len(items) > 1:  # too heavy or failed: split the batch and try again
        mid = len(items) // 2
        return dblp_find(http, items[:mid], answered) + dblp_find(http, items[mid:], answered)
    return []


def dblp_details(http, keys):
    recs = {}
    keys = [k for k in keys if re.fullmatch(r"[\w/\-.:]+", k)]
    for start in range(0, len(keys), 40):
        values = "VALUES ?pub { " + " ".join(f"<{DBLP_REC}{k}>" for k in keys[start:start + 40]) + " }"
        rows = sparql(http, f"""SELECT ?pub ?p ?o WHERE {{
 {{ {values} VALUES ?p {{ dblp:bibtexType dblp:title dblp:publishedIn dblp:yearOfPublication dblp:pagination
    dblp:doi dblp:primaryDocumentPage dblp:publishedInJournalVolume dblp:publishedInJournalVolumeIssue }} ?pub ?p ?o }}
 UNION {{ {values} ?pub dblp:hasSignature ?sig . ?sig dblp:signatureOrdinal ?ord . ?sig dblp:signatureDblpName ?name .
    BIND(<urn:author> AS ?p) BIND(CONCAT(STR(?ord), "#", ?name) AS ?o) }}
 UNION {{ {values} ?pub dblp:publishedAsPartOf ?proc . ?proc dblp:title ?o . BIND(<urn:booktitle> AS ?p) }}
 UNION {{ {values} ?pub dblp:publishedAsPartOf ?proc . ?proc dblp:publishedBy ?o . BIND(<urn:publisher> AS ?p) }}
}}""") or []
        for r in rows:
            k = r["pub"]["value"].replace(DBLP_REC, "")
            p = r["p"]["value"].rsplit("#", 1)[-1].replace("urn:", "")
            recs.setdefault(k, defaultdict(list))[p].append(r["o"]["value"])
    return recs


def dblp_cand(key, rec):
    first = lambda p: (rec.get(p) or [""])[0]
    signed = sorted({(int(a.split("#", 1)[0]), a.split("#", 1)[1]) for a in rec.get("author", []) if "#" in a})
    authors = [re.sub(r"\s+\d{4}$", "", name) for _, name in signed]
    title = first("title")
    if title.endswith(".") and not title.endswith(".."):
        title = title[:-1]
    venue = first("publishedIn")
    return Cand("DBLP", title, authors, to_year(first("yearOfPublication")),
                " ".join(x for x in (venue, first("booktitle")) if x), strip_doi(first("doi")) or None,
                None, key, f"https://dblp.org/rec/{key}", preprint=(venue == "CoRR"),
                extra={"rec": rec, "btype": first("bibtexType").rsplit("#", 1)[-1].lower()})


def dblp_fields(c):
    rec = c.extra["rec"]
    first = lambda p: (rec.get(p) or [""])[0]
    btype, venue = c.extra["btype"], first("publishedIn")
    f = {"author": " and ".join(c.authors), "title": c.title}
    if btype in ("inproceedings", "incollection"):
        f["booktitle"] = first("booktitle") or venue
    elif btype == "article":
        f["journal"] = venue
        if first("publishedInJournalVolume"):
            f["volume"] = first("publishedInJournalVolume")
        if first("publishedInJournalVolumeIssue"):
            f["number"] = first("publishedInJournalVolumeIssue")
    for src, dst in (("pagination", "pages"), ("publisher", "publisher")):
        if first(src):
            f[dst] = first(src)
    if c.year:
        f["year"] = str(c.year)
    page = first("primaryDocumentPage")
    if page and not re.match(r"https?://(dx\.)?doi\.org/", page):
        f["url"] = page
    if c.doi:
        f["doi"] = c.doi
    m = re.match(r"abs/(.+)", f.get("volume", ""))
    if venue == "CoRR" and m:
        f["eprinttype"], f["eprint"] = "arXiv", m.group(1)
        f.setdefault("url", f"https://arxiv.org/abs/{m.group(1)}")
    known = {"inproceedings", "article", "incollection", "book", "phdthesis", "mastersthesis", "techreport"}
    return (btype if btype in known else "misc"), f


HOSTS = ("api.semanticscholar.org, api.crossref.org, export.arxiv.org, arxiv.org, doi.org, data.crosscite.org, "
         "sparql.dblp.org and api.openalex.org")
CORE = {"api.crossref.org": "https://api.crossref.org/works?rows=0",
        "export.arxiv.org": "https://export.arxiv.org/api/query?search_query=all:electron&max_results=0",
        "api.semanticscholar.org": "https://api.semanticscholar.org/graph/v1/paper/search?query=attention&limit=1&fields=title"}


def preflight(http):
    """Stop before checking anything if a core paper database is blocked. Without this, a sandbox with no
    internet, or with only some hosts allowed, would report real papers as NOT FOUND."""
    blocked = []
    for host, url in CORE.items():
        st, _ = http.get(url, tries=2, cache=False, retry_429=False)
        if st == 0 or st in (401, 403, 407):  # no answer, or refused by a proxy or firewall
            blocked.append(host)
        elif st not in (200, 429):  # a rate limit proves the host is reachable; a 5xx is a passing outage
            print(f"Warning: {host} answered HTTP {st}, so results from it may be missing from this run.")
    if blocked:
        sys.exit(f"Cannot reach the paper databases ({', '.join(blocked)} did not answer), so nothing was checked. "
                 f"This environment probably has no internet access, or it blocks some of these hosts: {HOSTS}. "
                 f"Allow outbound HTTPS to them (for example in your sandbox or network settings) and run again. "
                 f"Do not treat any entry as made up until the check has run.")


ARXIV_STOP = set("and are but for into not such that the their then there these they this was will with".split())


def search_papers(http, query, n=5):
    """Candidate papers for a free-text query (a title, or title words plus an author), from each source."""
    found = []
    # OpenAlex: free, no key, good relevance ranking, abstracts stored as a word -> positions index
    # OpenAlex rejects ? and * (wildcard syntax) with HTTP 400
    st, j = http.json("https://api.openalex.org/works", params={"search": re.sub(r"[?*]", " ", query)[:300], "per-page": n})
    if st != 200:
        found.append(("OpenAlex", f"(no answer: HTTP {st}; retry in a few seconds)", [], None, "", None, None, None, None))
    for w in (j or {}).get("results") or []:
        inv = w.get("abstract_inverted_index") or {}
        words = sorted((p, word) for word, ps in inv.items() for p in ps)
        doi = strip_doi(w.get("doi") or "") or None
        venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
        found.append(("OpenAlex", w.get("display_name"), [(a.get("author") or {}).get("display_name", "") for a in w.get("authorships") or []],
                      w.get("publication_year"), venue, doi, None, w.get("id"), " ".join(word for _, word in words) or None))
    headers = {"x-api-key": S2_KEY} if S2_KEY else None
    st, j = http.json("https://api.semanticscholar.org/graph/v1/paper/search", headers=headers, tries=2, retry_429=False,  # don't wait out its rate limit
                      params={"query": query[:300], "limit": n, "fields": "title,authors,year,venue,externalIds,url,abstract"})
    for d in (j or {}).get("data") or []:
        ext = d.get("externalIds") or {}
        found.append(("Semantic Scholar", d.get("title"), [a.get("name", "") for a in d.get("authors") or []], d.get("year"),
                      d.get("venue"), ext.get("DOI"), ext.get("ArXiv"), d.get("url"), d.get("abstract")))
    if st not in (200, 404):
        found.append(("Semantic Scholar", f"(no answer: HTTP {st}; retry in a few seconds)", [], None, "", None, None, None, None))
    for c in crossref_search(http, query, "")[0][:n]:
        found.append(("Crossref", c.title, c.authors, c.year, c.venue, c.doi, None, c.url, None))
    # arXiv's index drops stopwords, so ANDing one of them would match nothing
    words = [w for w in re.findall(r"\w+", clean_latex(query)) if len(w) > 2 and w.lower() not in ARXIV_STOP][:8]
    if words:
        for c in _arxiv_feed(http, " AND ".join(f"all:{w}" for w in words), n)[0]:
            found.append(("arXiv", c.title, c.authors, c.year, "arXiv", None, c.arxiv, c.url, c.extra.get("abstract")))
    more = [("Europe PMC", lambda: europepmc_search(http, query, anywhere=True)), ("DataCite", lambda: datacite_search(http, query)),
            ("OpenReview", lambda: openreview_search(http, query, v1=False)), ("Open Library", lambda: openlibrary_search(http, query))]
    if CORE_KEY:  # without a key CORE allows 100 searches a day, which parallel research would use up
        more.append(("CORE", lambda: core_search(http, query)))
    for name, ask in more:
        cands, st = ask()
        if st != 200:
            found.append((name, f"(no answer: HTTP {st})", [], None, "", None, None, None, None))
        for c in cands[:n]:
            found.append((name, c.title, c.authors, c.year, c.venue, c.doi, c.arxiv, c.url, c.extra.get("abstract")))
    return found


def crossref_bibtex(http, doi):
    st, txt = http.get(f"https://api.crossref.org/works/{quote(doi, safe='/')}/transform/application/x-bibtex")
    return txt if st == 200 and txt.lstrip().startswith("@") else None


def arxiv_bibtex(http, aid):
    st, txt = http.get(f"https://arxiv.org/bibtex/{aid}")
    if st != 200 or not txt.lstrip().startswith("@"):
        return None
    # arXiv's export gives the latest revision's year. Use v1's date instead, falling back to the
    # ID's YYMM (the announcement month, which is a year late for papers sent in late December).
    c, _ = arxiv_by_id(http, re.sub(r"v\d+$", "", aid))
    yy = int(re.search(r"\d\d", aid.split("/")[-1]).group())
    year = c.year if c and c.year else (1900 + yy if yy > 90 else 2000 + yy)
    return re.sub(r"(year\s*=\s*\{)\d{4}(\})", rf"\g<1>{year}\g<2>", txt, count=1)


# ----------------------------------------------------------------------------------------
# Cleaning and printing BibTeX
# ----------------------------------------------------------------------------------------

ACCENTS = {"\u0301": "'", "\u0300": "`", "\u0302": "^", "\u0308": '"', "\u0303": "~", "\u0304": "=",
           "\u0307": ".", "\u0327": "c", "\u030c": "v", "\u0306": "u", "\u030b": "H", "\u030a": "r", "\u0328": "k"}
SPECIAL = {"\u00df": r"{\ss}", "\u00e6": r"{\ae}", "\u00c6": r"{\AE}", "\u00f8": r"{\o}", "\u00d8": r"{\O}",
           "\u0142": r"{\l}", "\u0141": r"{\L}", "\u0153": r"{\oe}", "\u0152": r"{\OE}", "\u0131": r"{\i}",
           "\u2013": "--", "\u2014": "---", "\u2019": "'", "\u2018": "`", "\u201c": "``", "\u201d": "''",
           "\u2026": r"\ldots{}", "\u00a0": "~"}


def latexify(s):
    """Replace accented and other non-ASCII characters with plain LaTeX commands."""
    out = []
    for ch in s:
        if ord(ch) < 128:
            out.append(ch)
        elif ch in SPECIAL:
            out.append(SPECIAL[ch])
        else:
            d = unicodedata.normalize("NFD", ch)
            if len(d) == 2 and d[1] in ACCENTS and ord(d[0]) < 128:
                base = r"\i" if d[0] == "i" else d[0]
                out.append("{\\" + ACCENTS[d[1]] + "{" + base + "}}")
            else:
                out.append(ch)
    return "".join(out)


def protect_caps(title):
    """Brace acronyms and mixed-case words so bibliography styles do not lowercase them."""
    def wrap(m):
        w = m.group(0)
        return "{" + w + "}" if (len(w) == 1 and w.isupper()) or re.search(r"[A-Z]", w[1:]) else w
    return re.sub(r"[A-Za-z][A-Za-z0-9]*", wrap, title)


def tidy(etype, fields):
    out = {}
    for k, v in fields.items():
        k = k.lower()
        v = " ".join(str(v).split())
        if k in ("id", "entrytype") or k in NOISE_FIELDS or not v:
            continue
        if k == "pages":
            v = re.sub(r"\s*[-\u2010-\u2015]+\s*", "--", v)
        if k == "month":
            v = MONTHS.get(v.lower()[:3], v)
        if k in ("author", "editor", "title", "booktitle", "journal", "publisher", "series",
                 "school", "institution", "organization", "address"):
            # Crossref's JATS markup; a real tag ends right after its name, so math like $a<b$ survives
            v = re.sub(r"</?(?:scp|i|b|em|strong|sub|sup|span|u|ovl|tt|font|mml:\w+)(?:\s[^<>]*)?/?>", "", v)
            v = html.unescape(v)
            v = re.sub(r"(?<!\\)([&%#])", r"\\\1", v)
            v = latexify(v)
        if k == "title" and "{" not in v:
            v = protect_caps(v)
        if k == "doi":
            v = strip_doi(v)
            if v.startswith("10.5555/"):  # ACM's internal IDs look like DOIs but are not registered, so they don't resolve
                continue
        out[k] = v
    if "doi" in out and re.match(r"https?://(dx\.)?doi\.org/", out.get("url", "")):
        del out["url"]
    etype = etype.lower()
    if etype == "article" and "journal" in out:
        out.pop("booktitle", None)  # Semantic Scholar sometimes writes both
    elif etype == "article" and "booktitle" in out:
        etype = "inproceedings"
    return etype, out


def from_bibtex_text(text):
    e = parse_entry(text)
    if not e:
        return None
    return tidy(e.get("ENTRYTYPE", "misc"), {k: v for k, v in e.items() if k not in ("ID", "ENTRYTYPE")})


def format_entry(etype, key, fields):
    keys = sorted(fields, key=lambda k: (FIELD_ORDER.index(k) if k in FIELD_ORDER else len(FIELD_ORDER), k))
    width = max((len(k) for k in keys), default=0)
    body = ",\n".join(f"  {k.ljust(width)} = {{{fields[k]}}}" for k in keys)
    return f"@{etype}{{{key},\n{body}\n}}"


def fetch_bibtex(http, ident):
    """Official BibTeX for a DOI, an arXiv ID, or a URL serving BibTeX. Returns (etype, fields, source) or None."""
    ident = ident.strip()
    doi = strip_doi(ident)
    aid = r"(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?"
    m = (re.fullmatch(r"(?:arxiv:)?" + aid, ident, re.I)
         or re.search(r"arxiv\.org/(?:abs|pdf|bibtex)/" + aid, ident, re.I)
         or re.fullmatch(r"10\.48550/arxiv\." + aid, doi, re.I))
    if m:
        txt, src = arxiv_bibtex(http, m.group(1)), "arXiv"
    elif doi.startswith("10."):
        txt, src = crossref_bibtex(http, doi), "Crossref"
        if not txt:  # DataCite and other registries answer DOI content negotiation instead
            st, txt = http.get(f"https://doi.org/{quote(doi, safe='/')}", headers={"Accept": "application/x-bibtex"})
            txt, src = (txt if st == 200 and txt.lstrip().startswith("@") else None), "doi.org"
    elif ident.startswith("http"):
        st, txt = http.get(ident)
        txt, src = (txt if st == 200 and txt.lstrip().startswith("@") else None), urlparse(ident).netloc
    else:
        return None
    parsed = from_bibtex_text(txt) if txt else None
    return (*parsed, src) if parsed else None


# ----------------------------------------------------------------------------------------
# Checking
# ----------------------------------------------------------------------------------------

def is_good(ref, c):
    if title_sim(ref.title, c.title) < TITLE_OK:
        return False
    ac = author_check(ref.authors, c)
    return ac is None or ac.frac >= 0.5


def first_surname(ref):
    return surnames_for_display(ref.authors, 1).rstrip("; .") if ref.authors else ""


def openalex_search(http, title, first_author):
    """Title search in OpenAlex, a third independent index next to Semantic Scholar and Crossref."""
    q = re.sub(r"[?*]", " ", title + (" " + first_author if first_author else ""))[:300]
    st, j = http.json("https://api.openalex.org/works", params={"search": q, "per-page": 5})
    out = []
    for w in (j or {}).get("results") or []:
        loc = (w.get("primary_location") or {}).get("source") or {}
        venue = loc.get("display_name") or ""
        out.append(Cand("OpenAlex", strip_tags(w.get("display_name") or ""),
                        [(a.get("author") or {}).get("display_name", "") for a in w.get("authorships") or []],
                        w.get("publication_year"), venue, strip_doi(w.get("doi") or "") or None, None, None, w.get("id"),
                        preprint=(w.get("type") == "preprint" or "arxiv" in venue.lower())))
    return out, st


# More independent databases. Each one is asked only where it adds something, and every function
# returns (records, status) so that a failed query is never mistaken for "not there".

def _words(s):
    """Plain lowercase words, with every character a query language treats as syntax removed and
    accents folded, so no title can break a query or be read as an operator."""
    s = re.sub(r"['’]s\b", "", fold(clean_latex(s or "")))  # possessives: Parkinson's -> Parkinson
    return re.findall(r"[^\W_]+", s.lower())


def _surname(name):
    w = _words(name)
    return w[-1] if w else ""


EPMC_NOTICES = {"retraction notice", "retraction of publication", "correction", "erratum", "published erratum",
                "expression of concern", "expression-of-concern", "editorial"}
EPMC_ABOUT = {"retraction of", "comment on", "expression of concern for", "erratum for", "correction for"}


def europepmc_search(http, title, first_author="", anywhere=False):
    """Europe PMC: PubMed, PubMed Central and life-science preprints. Its MEDLINE records are curated
    by the US National Library of Medicine, and it marks retracted papers."""
    words = [w for w in _words(title) if len(w) > 1 and w not in ("and", "or", "not")]  # lowercase "or" is still OR
    if not words:
        return [], 200
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    base = {"format": "json", "resultType": "core", "pageSize": 5}
    q = " ".join(words) if anywhere else f"TITLE:({' '.join(words)})"  # every word must be in the title
    st, j = http.json(url, params={**base, "query": q})
    res = ((j or {}).get("resultList") or {}).get("result") or []
    sur = _surname(first_author)
    if (j or {}).get("hitCount", 0) > 5 and sur:  # a generic title: the paper may not be on page one
        _, j2 = http.json(url, params={**base, "query": f'{q} AND AUTH:"{sur}"'})
        res = (((j2 or {}).get("resultList") or {}).get("result") or []) + res
    out, seen = [], set()
    for x in res:
        types = {t.lower() for t in (x.get("pubTypeList") or {}).get("pubType") or []}
        links = {c.get("type", "").lower() for c in (x.get("commentCorrectionList") or {}).get("commentCorrection") or []}
        letter = types & {"letter", "comment"} and "journal article" not in types
        if x.get("id") in seen or types & EPMC_NOTICES or letter or links & EPMC_ABOUT:
            continue  # a notice, letter or reply about a paper often repeats its title, but is not the paper
        seen.add(x.get("id"))
        journal = (x.get("journalInfo") or {}).get("journal") or {}
        venue = journal.get("title") or journal.get("medlineAbbreviation") or (x.get("bookOrReportDetails") or {}).get("publisher") or ""
        if x.get("source") == "PPR" and "arxiv" in (venue + " " + ((x.get("bookOrReportDetails") or {}).get("publisher") or "")).lower():
            continue  # Europe PMC's copy of an arXiv record; arXiv is asked directly
        t = re.sub(r"^\[(.*)\]$", r"\1", strip_tags(x.get("title") or "").strip().rstrip("."))  # MEDLINE brackets translations
        authors = [a.get("collectiveName") or " ".join(p for p in (a.get("firstName"), a.get("lastName")) if p) or a.get("fullName", "")
                   for a in (x.get("authorList") or {}).get("author") or []]
        out.append(Cand("Europe PMC", t, authors, to_year(x.get("pubYear")), venue, (x.get("doi") or "").lower() or None,
                        url=f"https://europepmc.org/article/{x.get('source')}/{x.get('id')}",
                        preprint=x.get("source") == "PPR" or "preprint" in types,
                        extra={"retracted": "retracted publication" in types or "retraction in" in links}))
    return out, st


def _arxiv_feed(http, query, n=5):
    st, txt = http.get("https://export.arxiv.org/api/query", params={"search_query": query, "max_results": n, "sortBy": "relevance"})
    if st != 200:
        return [], st
    ns = {"a": "http://www.w3.org/2005/Atom"}
    try:
        entries = ET.fromstring(txt).findall("a:entry", ns)
    except ET.ParseError:
        return [], 0
    out = []
    for e in entries:
        if "/api/errors" in e.findtext("a:id", "", ns):
            continue
        aid = re.sub(r"v\d+$", "", e.findtext("a:id", "", ns).rsplit("/abs/", 1)[-1])
        out.append(Cand("arXiv", " ".join(e.findtext("a:title", "", ns).split()),
                        [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)],
                        to_year(e.findtext("a:published", "", ns)), "arXiv", None, aid, url=f"https://arxiv.org/abs/{aid}",
                        preprint=True, extra={"abstract": " ".join(e.findtext("a:summary", "", ns).split())}))
    return out, st


def arxiv_title_search(http, title, first_author=""):
    """arXiv by title: the exact phrase first, then the title's content words with the first author.
    A stray quote makes arXiv return junk with HTTP 200, so the words are rebuilt from scratch."""
    words = re.findall(r"[^\W_]+(?:'[^\W_]+)*", fold(clean_latex(title or "")).replace("’", "'").lower())
    if not words:
        return [], 200
    out, st = _arxiv_feed(http, f'ti:"{" ".join(words)}"')
    sur = _surname(first_author)
    hit = lambda c: title_sim(title, c.title) >= TITLE_OK and (not sur or any(sur in surname_keys(a) for a in c.authors))
    if st == 200 and not any(hit(c) for c in out):  # the phrase is also inside other titles, or a word differs
        terms = [f"ti:{w}" for w in words if w not in ARXIV_STOP and len(w) > 2 and "'" not in w][:8]
        if terms:
            more, st = _arxiv_feed(http, " AND ".join(terms + ([f"au:{sur}"] if sur else [])))
            out += [c for c in more if c.arxiv not in {d.arxiv for d in out}]
    return out, st


def datacite_search(http, title, first_author=""):
    """DataCite, the DOI registry for theses, software, datasets and repository copies. Its arXiv
    records are copies of arXiv's own, so they are skipped: arXiv is asked directly."""
    words = _words(title)[:40]
    if not words:
        return [], 200
    t = "titles.title:(" + " OR ".join(words) + ")"  # bare words are ANDed here, so one changed word would miss
    sur = _surname(first_author)
    q = f"{t} OR ({t} AND creators.name:({sur}))" if sur else t  # the author lifts a record, never filters
    st, j = http.json("https://api.datacite.org/dois", params={"query": q, "page[size]": 5, "sort": "relevance",
                      "fields[dois]": "doi,titles,creators,publicationYear,publisher,container,types,url"})
    out = []
    for d in (j or {}).get("data") or []:
        a = d.get("attributes") or {}
        doi = (a.get("doi") or "").lower()
        if doi.startswith("10.48550/"):
            continue
        titles = a.get("titles") or []
        main = next((x.get("title", "") for x in titles if not x.get("titleType")), titles[0].get("title", "") if titles else "")
        sub = next((x.get("title", "") for x in titles if x.get("titleType") == "Subtitle"), "")
        pub = a.get("publisher")
        pub = pub.get("name", "") if isinstance(pub, dict) else (pub or "")
        kind = (a.get("types") or {}).get("resourceTypeGeneral") or ""
        out.append(Cand("DataCite", f"{main}: {sub}" if sub else main, [c.get("name", "") for c in a.get("creators") or []],
                        to_year(a.get("publicationYear")), (a.get("container") or {}).get("title") or pub, doi or None,
                        url=a.get("url") or (f"https://doi.org/{doi}" if doi else None), preprint=kind == "Preprint",
                        extra={"kind": kind}))
    return out, st


def openlibrary_search(http, title, first_author="", year=None):
    """Open Library, a library catalog of books. Its records list the year of every edition, and the
    title of each edition, since a work's own title can be the original-language one."""
    t, sur = " ".join(_words(title)), _surname(first_author)
    if not t:
        return [], 200
    params = {"fields": "key,title,subtitle,author_name,first_publish_year,publish_year,publisher,editions,editions.title", "limit": 5}
    st, j = http.json("https://openlibrary.org/search.json", params={**params, "q": f"{t} {sur}".strip()})
    docs = (j or {}).get("docs") or []
    if sur and st == 200 and not docs:  # "Schoelkopf" does not find "Schölkopf": try the title alone
        st, j = http.json("https://openlibrary.org/search.json", params={**params, "q": t})
        docs = (j or {}).get("docs") or []
    out = []
    for d in docs:
        names = [(d.get("title") or "") + (": " + d["subtitle"] if d.get("subtitle") else "")]
        names += [e.get("title", "") for e in ((d.get("editions") or {}).get("docs") or [])]
        best = max(names, key=lambda n: title_sim(title, n))
        years = [y for y in sorted(set(d.get("publish_year") or [])) if y] or [y for y in [d.get("first_publish_year")] if y]
        y = min(years, key=lambda v: abs(v - year)) if year and years else d.get("first_publish_year")  # the cited edition
        out.append(Cand("Open Library", best, d.get("author_name") or [], y, (d.get("publisher") or [""])[0],
                        url=f"https://openlibrary.org{d['key']}" if d.get("key") else None, extra={"btype": "book"}))
    return out, st


OPENREVIEW_COPIES = ("dblp.org/", "DBLP.org/", "OpenReview.net/Public_Article", "OpenReview.net/Archive", "ML_Reproducibility_Challenge/")
OPENREVIEW_VENUE = re.compile(r"\b(iclr|neurips|nips|icml|tmlr|corl|colm|uai|aistats)\b|learning representations|neural information "
                              r"processing|transactions on machine learning research|robot learning|language modeling|openreview\.net", re.I)


def _or_val(x):
    return x.get("value") if isinstance(x, dict) else x  # API v2 wraps every field as {"value": ...}


def openreview_fate(venue, vid, invitation):
    """What an OpenReview record says happened to the paper. Only a positive sign counts as accepted."""
    low = " ".join((venue, vid, invitation)).lower()
    if "withdrawn" in low:
        return "withdrawn"
    if "rejected" in low:
        return "rejected"
    if (re.search(r"/(Submission|Under_Review|Decision_Pending|Submitted)$", vid) or re.match(r"(under review|decision pending)\b", venue, re.I)
            or venue.endswith("Conference Submission")):
        return "under review"
    if "submitted" in venue.lower():  # "Submitted to ICLR 2023": public, but not accepted
        return "rejected"
    if "/workshop" in vid.lower() or "workshop" in low:
        return "workshop"  # accepted at a workshop, which is not the main conference
    return "accepted" if venue else "unknown"  # the oldest records (about ICLR 2021 and before) carry no decision


def openreview_search(http, title, first_author="", v1=True):
    """OpenReview, where ICLR, NeurIPS, TMLR and others take their submissions. It also shows papers
    that were rejected or withdrawn, which no other database records. The older API (v1) allows
    5 searches a minute, so callers that run in parallel pass v1=False."""
    term = " ".join(_words(title))[:300]
    if not term:
        return [], 200
    out, statuses = [], []
    apis = [("https://api2.openreview.net", {"type": "terms"})] + ([("https://api.openreview.net", {})] if v1 else [])
    for base, extra in apis:
        st, j = http.json(base + "/notes/search", params={"term": term, "content": "title", "group": "all",
                          "source": "forum", "limit": 10, **extra}, tries=2, retry_429=False)
        statuses.append(st)
        for n in (j or {}).get("notes") or []:
            c = n.get("content") or {}
            invs = n.get("invitations") or [n.get("invitation") or ""]
            vid, venue = _or_val(c.get("venueid")) or "", _or_val(c.get("venue")) or ""
            if n.get("ddate") or any(i.startswith(OPENREVIEW_COPIES) for i in invs) or vid.startswith(OPENREVIEW_COPIES):
                continue  # deleted, or OpenReview's copy of a dblp, ORCID or arXiv record
            authors = [a.get("fullname", "") if isinstance(a, dict) else a for a in _or_val(c.get("authors")) or []]
            if not [a for a in authors if a and a.lower() != "anonymous"]:
                continue  # an anonymous submission would match on its title alone
            where = " ".join((venue, vid, invs[0]))
            fate = openreview_fate(venue, vid, invs[0])
            m = re.search(r"\b(?:19|20)\d{2}\b", where)
            ms = n.get("pdate") or n.get("odate") or n.get("cdate")
            year = int(m.group()) if m else (time.gmtime(ms / 1000).tm_year if ms else None)
            out.append(Cand("OpenReview", " ".join((_or_val(c.get("title")) or "").split()), authors, year,
                            venue if fate == "accepted" else "",  # a failed or pending submission says nothing about the venue
                            url=f"https://openreview.net/forum?id={n.get('forum') or n.get('id')}", preprint=fate != "accepted",
                            extra={"fate": fate, "where": where, "workshop": "/workshop" in vid.lower() or "workshop" in where.lower()}))
        if st == 200 and any(title_sim(title, c.title) >= TITLE_OK for c in out):
            break  # the older API only when the newer one lacks the paper
    return out, next((x for x in statuses if x != 200), 200)  # any API that failed makes the search incomplete


def core_search(http, title, first_author=""):
    """CORE, which gathers open-access repositories: university theses and reports that the DOI-based
    indexes miss. Its records of arXiv papers are copies, so they are skipped. Without CORE_API_KEY
    it allows 100 searches a day, so the checker asks it only about theses, reports and entries no
    other database has."""
    t = " ".join(_words(title))[:250]  # CORE answers HTTP 500 to any punctuation, even inside quotes
    if not t:
        return [], 200
    # A plain query costs one of the 100 daily tokens; a boolean one (an author boost) costs 3 to 5
    headers = {"Authorization": f"Bearer {CORE_KEY}"} if CORE_KEY else None
    st, j = http.json("https://api.core.ac.uk/v3/search/works/", params={"q": f'title:"{t}"', "limit": 5}, headers=headers, tries=2)
    out = []
    for w in (j or {}).get("results") or []:
        if w.get("arxivId"):
            continue
        names = list(dict.fromkeys(a.get("name", "") for a in w.get("authors") or []))  # CORE repeats names
        kind = w.get("documentType") or ""
        venue = next((x.get("title") for x in w.get("journals") or [] if x.get("title")), "") or w.get("publisher") or ""
        out.append(Cand("CORE", " ".join((w.get("title") or "").split()), names, w.get("yearPublished") or to_year(w.get("publishedDate")),
                        venue, strip_doi(w.get("doi") or "") or None, url=f"https://core.ac.uk/works/{w.get('id')}",
                        preprint="preprint" in kind.lower(), extra={"kind": kind}))
    return out, st


BOOK_TYPES = {"book", "inbook", "incollection", "booklet", "proceedings", "collection", "mvbook"}
REPORT_TYPES = {"phdthesis", "mastersthesis", "thesis", "techreport", "report", "unpublished", "misc", "manual"}


def lookup_extra(http, refs):
    """Ask more independent databases about each entry the main ones left short of two confirmations,
    and Europe PMC about every entry, since it is quick and marks retracted papers."""
    # A website or software entry is checked by opening its own link; a library or DOI record with the
    # same short name ("PyTorch") is usually something else.
    todo = [r for r in refs if r.title and not looks_like_web(r)]
    if not todo:
        return
    print(f"Asking more databases (Europe PMC, arXiv, DataCite, Open Library, OpenReview, CORE) about {len(todo)} entries...", flush=True)
    for ref in todo:
        sur = first_surname(ref)

        def ask(name, fn, *extra):
            got, st = fn(http, ref.title, sur, *extra)
            ref.cands.extend(got)
            if st == 200:
                ref.searched.append(name)

        ask("Europe PMC", europepmc_search)
        f = ref.fields
        at_openreview = bool(OPENREVIEW_VENUE.search(" ".join(f.get(k, "") for k in ("booktitle", "journal", "url", "howpublished", "note"))))
        # OpenReview also for confirmed papers whose stated venue no record shows, since it may show a rejection
        shown_there = any(confirms(ref, c) and not c.preprint and ref.venue and venue_code(c.venue) == ref.venue
                          and c.year == ref.year for c in ref.cands)
        if at_openreview and not shown_there:
            ask("OpenReview", openreview_search)
        if len(confirmations(ref)) >= 2:
            continue
        if not any(c.source == "arXiv" and title_sim(ref.title, c.title) >= TITLE_CLOSE for c in ref.cands):
            ask("arXiv", arxiv_title_search)  # also when the entry's arXiv ID led to another paper
        ask("DataCite", datacite_search)
        nothing_close = not any(title_sim(ref.title, c.title) >= TITLE_CLOSE for c in ref.cands)
        if ref.etype in BOOK_TYPES or (f.get("publisher") and not (f.get("journal") or f.get("booktitle"))) or nothing_close:
            ask("Open Library", openlibrary_search, ref.year)
        if ref.etype in REPORT_TYPES or nothing_close:
            ask("CORE", core_search)


def confirms(ref, c):
    """Stricter than is_good: the first author has to match as well, so a different paper that
    shares a famous title and one author doesn't count as a second source."""
    ac = author_check(ref.authors, c)
    return is_good(ref, c) and (ac is None or ac.first_ok)


def confirmations(ref):
    """The distinct databases whose record matches the entry's title and authors."""
    return sorted({c.source for c in ref.cands if confirms(ref, c)})


def corroborated(ref, fields, source):
    """True if a database other than `source` agrees with this BibTeX on title, first author and year.
    One database's record is never trusted alone: they all have errors, and checking one
    against another catches most of them."""
    title = clean_latex(fields.get("title", ""))
    authors = split_authors(fields.get("author", "") or fields.get("editor", ""))
    year = to_year(fields.get("year") or fields.get("date"))
    for c in ref.cands:
        if c.source == source or title_sim(title, c.title) < 88:
            continue
        if authors and c.authors and not _name_hit(authors[0], set().union(*(surname_keys(a) for a in c.authors))):
            continue
        if year and c.year and abs(year - c.year) > 1:  # a preprint and its proceedings often differ by a year
            continue
        return True
    return False


def lookup_basic(http, ref):
    """Identifier checks plus title search in Semantic Scholar, Crossref and OpenAlex, every one of
    them for every entry, so each result can be checked against independent records."""
    if ref.doi:
        c, state = crossref_doi(http, ref.doi)
        if c:
            ref.cands.append(c)
            if ref.title and title_sim(ref.title, c.title) < TITLE_CLOSE:
                ref.id_problems.append(f'the DOI {ref.doi} belongs to a different paper ("{short(c.title, 70)}")')
        elif state == "missing" and doi_exists(http, ref.doi) is False:
            ref.id_problems.append(f"the DOI {ref.doi} does not exist")
    if ref.arxiv:
        c, state = arxiv_by_id(http, ref.arxiv)
        if c:
            ref.cands.append(c)
            if ref.title and title_sim(ref.title, c.title) < TITLE_CLOSE:
                ref.id_problems.append(f'the arXiv ID {ref.arxiv} belongs to a different paper ("{short(c.title, 70)}")')
        elif state == "missing":
            ref.id_problems.append(f"the arXiv ID {ref.arxiv} does not exist")
    if not ref.title:
        return
    cands, st = s2_match(http, ref.title)
    ref.cands += cands
    if st not in (200, 404):
        ref.errors.append("Semantic Scholar did not answer")
    else:
        ref.searched.append("Semantic Scholar")
    for name, host, fn in (("Crossref", "api.crossref.org", crossref_search), ("OpenAlex", "api.openalex.org", openalex_search)):
        got, st = fn(http, ref.title, first_surname(ref))
        ref.cands += got
        if st == 200:
            ref.searched.append(name)
        elif host not in http.down:  # a host that is down for the whole run is named once, at the top
            ref.errors.append(f"{name} did not answer")


def lookup_dblp(http, refs):
    items, wanted = [], defaultdict(set)
    for i, ref in enumerate(refs):
        if not ref.title:
            continue
        titles = [ref.title] + [c.title for c in ref.cands if title_sim(ref.title, c.title) >= TITLE_CLOSE]
        seen = set()
        for t in titles:
            n = dblp_norm(t)
            if n and n not in seen:
                seen.add(n)
                items.append((i, t))
        for c in ref.cands:
            if c.dblp:
                wanted[i].add(c.dblp)
    if not items:
        return
    batches = [items[s:s + 10] for s in range(0, len(items), 10)]
    print(f"Looking up DBLP in {len(batches)} batch(es); dblp asks for 10 s between queries, so this takes a moment...")
    answered = set()
    for b in batches:
        for i, k in dblp_find(http, b, answered):
            wanted[i].add(k)
    recs = dblp_details(http, sorted(set().union(*wanted.values()))) if wanted else {}
    if not recs and wanted:
        print("  DBLP did not answer, continuing without it.")
    for i in answered:
        if recs or not wanted.get(i):  # a match whose record could not be read was not really answered
            refs[i].searched.append("DBLP")
    for i, keys in wanted.items():
        have = {c.dblp for c in refs[i].cands if c.source == "DBLP"}
        refs[i].cands += [dblp_cand(k, recs[k]) for k in sorted(keys) if k in recs and k not in have]


def version_rank(ref, c):
    return (venue_code(c.venue) == ref.venue, c.extra.get("btype") == ref.etype, c.year == ref.year, c.year or 0)


def judge(ref):
    if not ref.title:
        ref.status = "NOT FOUND"
        ref.problems.append("the entry has no title, so it could not be looked up")
        ref.problems += ref.id_problems
        return
    scored = [(title_sim(ref.title, c.title), c) for c in ref.cands]
    if not scored or max(s for s, _ in scored) < TITLE_CLOSE:
        ref.status = "NOT FOUND"
        where = ", ".join(dict.fromkeys(ref.searched)) or "any database (none answered)"
        ref.problems.append(f"no paper with this title was found in {where}. It may be made up, "
                            "or it may be a book, thesis, website, or very new paper")
        ref.problems += ref.id_problems
        return

    def rank(sc):
        s, c = sc
        ac = author_check(ref.authors, c)
        return (min(s, TITLE_OK), ac.frac if ac else 0.5, s, not c.preprint, SOURCE_RANK.get(c.source, 0))

    ref.best_score, ref.best = max(scored, key=rank)
    best = ref.best
    same = [c for s, c in scored if s >= TITLE_OK] or [best]
    checks = [a for a in (author_check(ref.authors, c) for c in same) if a]
    ac = max(checks, key=lambda a: (a.frac, a.first_ok)) if checks else None

    if ref.best_score < TITLE_OK:
        ref.problems.append(f'the title does not match the closest real paper, which is "{best.title}"')
    if not ref.authors:
        ref.notes.append("your entry has no author field")
    elif ac and ac.frac == 0:
        ref.problems.append(f"none of your authors are on the real paper (real authors: {surnames_for_display(best.authors)})")
    elif ac and ac.frac < 1:
        names = ", ".join(clean_latex(a) for a in ac.missing[:3])
        ref.problems.append(f"some authors are not on the real paper ({names})")
    elif ac and not ac.first_ok:
        ref.problems.append(f"the first author differs (real first author: {surnames_for_display(best.authors, 1)})")

    pool = [c for c in same if c.year and is_good(ref, c)] or [c for c in same if c.year]  # not a letter that reuses the title
    if ref.venue != "arXiv" and any(not c.preprint for c in pool):
        pool = [c for c in pool if not c.preprint]  # the entry cites a published version
    registry = {c.doi.lower(): c.year for c in pool if c.source == "Crossref" and c.doi}
    years = [registry.get((c.doi or "").lower(), c.year) for c in pool]  # for a DOI, Crossref's year wins
    if ref.year and years:
        closest = min(years, key=lambda y: abs(y - ref.year))
        if abs(closest - ref.year) >= 2:
            ref.problems.append(f"the year differs (yours {ref.year}, real {closest})")
        elif closest != ref.year:
            ref.notes.append(f"year is off by one (yours {ref.year}, found {closest})")

    found = {venue_code(c.venue) for c in same if not c.preprint} - {None, "arXiv"}
    preprint_only = not found and any(c.preprint for c in same)
    if ref.venue == "arXiv" and found:
        ref.notes.append(f"a published version exists ({', '.join(sorted(found))}), and the new BibTeX cites it")
    elif ref.venue and ref.venue != "arXiv":
        if found and ref.venue not in found:
            ref.problems.append(f"the venue differs (yours says {ref.venue}, the paper appeared at {', '.join(sorted(found))})")
        elif preprint_only and not any(venue_code(c.venue) == ref.venue for c in same):
            ref.problems.append(f"only an arXiv preprint was found; check that it really appeared at {ref.venue}")
    mine = clean_latex(ref.fields.get("journal") or ref.fields.get("journaltitle") or ref.fields.get("booktitle") or "")
    if mine and not ref.venue:  # a venue venue_code() doesn't know, so compare the names
        pubs = [c for c in same if c.venue and not c.preprint and is_good(ref, c)]
        near = [c for c in pubs if ref.year and c.year and abs(c.year - ref.year) <= 1] or pubs
        if near and not any(same_venue(mine, c.venue) for c in near):
            ref.problems.append(f"the venue differs (yours says {short(mine, 60)}; the databases list "
                                f"{'; '.join(sorted({short(c.venue, 60) for c in near}))})")

    retracted = [c for c in ref.cands if c.extra.get("retracted") and confirms(ref, c)]
    cited = [c for c in retracted if (c.doi.lower() == ref.doi.lower() if c.doi and ref.doi
                                      else bool(ref.year and c.year and abs(c.year - ref.year) <= 1))]
    if cited:
        ref.problems.append("Europe PMC marks this paper as retracted; check that you still want to cite it")
    elif retracted:
        ref.notes.append("Europe PMC lists a retracted version of this paper; check that yours is not that one")
    entry_workshop = "workshop" in " ".join(ref.fields.get(k, "") for k in ("booktitle", "journal", "note", "howpublished", "series")).lower()
    for c in ref.cands:
        if (c.extra.get("fate") not in ("rejected", "withdrawn") or not ref.venue or ref.year != c.year
                or c.extra.get("workshop", False) != entry_workshop or venue_code(c.extra.get("where", "")) != ref.venue
                or not confirms(ref, c)):
            continue
        # A record of the paper at that venue and year, from a source that dates by edition, overrides the rejection
        if not any(d is not c and d.source in ("OpenReview", "DBLP") and not d.preprint and venue_code(d.venue) == ref.venue
                   and d.year == ref.year for d in same):
            ref.problems.append(f"OpenReview shows this paper was {c.extra['fate']} at {ref.venue} {c.year}, so it did not "
                                f"appear there ({c.url})")
            break

    ref.problems += ref.id_problems
    ref.confirmed_by = confirmations(ref)
    if not ref.problems and len(ref.confirmed_by) < 2:
        only = ref.confirmed_by[0] if ref.confirmed_by else "no database"
        ref.problems.append(f"only one database ({only}) confirms this entry, and every entry needs two independent "
                            "sources; confirm it in a second one (Google Scholar, the publisher's page or a library catalog)")
    ref.status = "CHECK" if ref.problems else "OK"


VENUE_STOP = {"of", "the", "and", "on", "in", "for", "proceedings", "proc", "annual", "international", "conference"}


def _abbrev(x, y):
    """x abbreviates y, ISO 4 style: same first letter, and x's letters appear in y in order (natl, national)."""
    it = iter(y)
    return x[0] == y[0] and all(ch in it for ch in x)


def _venue_words(s):
    return [w for w in re.sub(r"[^a-z0-9 ]", " ", fold(clean_latex(s)).lower()).split() if w not in VENUE_STOP]


def same_venue(a, b):
    """True if two venue names plausibly name the same venue, abbreviations included
    ("J. Mach. Learn. Res." and "Journal of Machine Learning Research")."""
    ca, cb = venue_code(a), venue_code(b)
    if ca and cb:
        return ca == cb
    wa, wb = _venue_words(a), _venue_words(b)
    if not wa or not wb:
        return True  # nothing to compare
    if len(wa) == len(wb) and all(_abbrev(x, y) or _abbrev(y, x) for x, y in zip(wa, wb)):
        return True  # word by word, one name abbreviates the other
    for x, y in ((a, b), (b, a)):  # an acronym (PNAS, JMLR)
        words = [w for w in re.sub(r"[^a-z0-9 ]", " ", fold(clean_latex(y)).lower()).split()
                 if w not in ("of", "the", "and", "on", "in", "for")]
        if re.fullmatch(r"[A-Z]{3,}", clean_latex(x).strip()) and "".join(w[0] for w in words).startswith(x.strip().lower()):
            return True
    return fuzz.token_sort_ratio(" ".join(wa), " ".join(wb)) >= 85


def _same_person(a, b):
    """Loosely the same author: one name's surname is among the other's name parts (Mosquera, Rafael
    and Rafael Mosquera Gomez; Perez-Nieves and Perez Nieves)."""
    parts = lambda n: {_key(t) for t in re.split(r"[\s,\-]+", clean_latex(n)) if len(t) > 1}
    return bool(surname_keys(a) & parts(b) or surname_keys(b) & parts(a))


def version_conflict(ref, fields):
    """Why using this BibTeX would change what the entry cites, or lose something it says; None if safe.
    It guards against the two ways a cleanup goes wrong: swapping the cited version for another one
    (a conference paper for its later journal reprint, one co-published copy for another), and dropping
    where the work appeared (a book's publisher, a thesis's school)."""
    for k in ("publisher", "school", "institution"):
        if ref.fields.get(k) and not fields.get(k) and ref.etype in ("book", "phdthesis", "mastersthesis", "techreport", "manual"):
            return f"has no {k}, which your entry gives ({short(clean_latex(ref.fields[k]), 40)})"
    mine = clean_latex(ref.fields.get("journal") or ref.fields.get("journaltitle") or ref.fields.get("booktitle") or "")
    if mine and not (fields.get("journal") or fields.get("booktitle")) and ref.etype in ("article", "inproceedings", "incollection", "inbook", "conference"):
        return f"has no journal or booktitle, which your entry gives ({short(mine, 40)})"
    theirs = clean_latex(fields.get("journal") or fields.get("booktitle") or "")
    # The same people in another order: databases get this wrong too (dblp swapped two PRISM authors),
    # and nothing tells which order is right, so the entry is kept.
    mine_a = [a for a in split_authors(ref.fields.get("author", "")) if a.lower().strip(".") not in ("others", "et al")]
    theirs_a = split_authors(fields.get("author", ""))
    if (len(mine_a) >= 2 and len(mine_a) == len(theirs_a) and all(any(_same_person(m, t) for t in theirs_a) for m in mine_a)
            and not all(_same_person(m, t) for m, t in zip(mine_a, theirs_a))):
        return "lists the same authors in a different order from your entry; check the order on the paper itself"
    preprint = venue_code(theirs) == "arXiv" or "10.48550/" in (fields.get("doi") or "").lower() or bool(
        (fields.get("eprint") or fields.get("archiveprefix")) and not theirs)
    if mine and venue_code(mine) != "arXiv" and preprint:
        return f"is the arXiv preprint, not the published version your entry cites ({short(mine, 50)})"
    new_year = to_year(fields.get("year") or fields.get("date"))
    year_moves = bool(ref.year and new_year and abs(new_year - ref.year) >= 2)
    venue_moves = bool(mine and theirs and not same_venue(mine, theirs))
    if not (year_moves or venue_moves):
        return None
    # The change is a fix only if no database knows the version the entry cites.
    new_doi = strip_doi(fields.get("doi", "")).lower()
    own = [c for c in ref.cands if is_good(ref, c) and not (new_doi and (c.doi or "").lower() == new_doi)
           and c.extra.get("fate") not in ("rejected", "withdrawn", "under review", "workshop")
           and (not ref.year or not c.year or abs(c.year - ref.year) <= 1)
           and (not mine or not c.venue or same_venue(mine, c.venue))]
    if own:
        return (f"describes a different version ({short(theirs, 50) or 'no venue'}, {new_year}) from the one your "
                f"entry cites, which {own[0].source} also lists")
    return None


def suggest(http, ref):
    """The best BibTeX for the matched paper, as (etype, fields, source), but only when a second,
    independent database agrees with it on title, first author and year, and it describes the same
    version the entry cites without dropping where it appeared. Otherwise None, and the entry is kept."""
    got = _suggest(http, ref)
    if not got:
        return None
    if not corroborated(ref, got[1], got[2]):
        ref.notes.append(f"the {got[2]} record was not confirmed by a second database, so its BibTeX was not used")
        return None
    why = version_conflict(ref, got[1])
    if why:
        ref.notes.append(f"the {got[2]} record {why}, so your entry was kept as it is")
        return None
    return got


def _suggest(http, ref):
    """Pick the best BibTeX for the paper that was matched. Returns (etype, fields, source) or None."""
    best = ref.best
    if not best:
        return None
    versions = []
    for c in ref.cands:
        if title_sim(best.title, c.title) >= TITLE_OK:
            ac = author_check(best.authors, c)
            if ac is None or ac.frac >= 0.5:
                versions.append(c)
    dblp = [c for c in versions if c.source == "DBLP"]
    published = [c for c in dblp if not c.preprint]
    if published:
        c = max(published, key=lambda c: version_rank(ref, c))
        if c.extra["btype"] == "article" and c.doi:
            txt = crossref_bibtex(http, c.doi)
            if txt and from_bibtex_text(txt):
                return (*from_bibtex_text(txt), "Crossref")
        return (*tidy(*dblp_fields(c)), "DBLP")
    mine = clean_latex(ref.fields.get("journal") or ref.fields.get("booktitle") or "")
    with_doi = sorted((c for c in versions if c.doi and c.source != "DataCite" and not c.doi.lower().startswith("10.48550/")),
                      key=lambda c: (c.doi.lower() != (ref.doi or "").lower(),  # the entry's own DOI first,
                                     not (mine and c.venue and same_venue(mine, c.venue)),  # then its venue,
                                     abs((c.year or 0) - (ref.year or c.year or 0))))  # then its year
    doi = with_doi[0].doi if with_doi else None
    if doi:
        txt = crossref_bibtex(http, doi)
        if txt and from_bibtex_text(txt):
            return (*from_bibtex_text(txt), "Crossref")
    if dblp:
        return (*tidy(*dblp_fields(dblp[0])), "DBLP")
    aid = next((c.arxiv for c in versions if c.arxiv), None)
    if aid:
        txt = arxiv_bibtex(http, aid)
        if txt and from_bibtex_text(txt):
            return (*from_bibtex_text(txt), "arXiv")
    s2 = next((c for c in versions if c.bibtex), None)
    if s2 and from_bibtex_text(s2.bibtex):
        return (*from_bibtex_text(s2.bibtex), "Semantic Scholar")
    return None


def entry_url(ref):
    url = ref.fields.get("url", "").strip()
    if not url:
        m = re.search(r"\\url\{([^}]+)\}", ref.fields.get("howpublished", "") + " " + ref.fields.get("note", ""))
        url = m.group(1) if m else ""
    return re.sub(r"^\\url\{(.*)\}$", r"\1", url)


def looks_like_web(ref):
    f = ref.fields
    return (ref.etype in WEB_TYPES and not ref.arxiv and not ref.doi and bool(entry_url(ref))
            and not f.get("journal") and not f.get("booktitle"))


def check_link(http, ref):
    url = entry_url(ref)
    if not url.startswith("http"):
        return
    try:
        r = http.session.get(url, timeout=15, allow_redirects=True, stream=True)
        r.close()
        if r.status_code < 400:
            ref.notes.append("the url in the entry works")
        elif r.status_code in (401, 403, 429):
            ref.notes.append(f"the url answered {r.status_code} (the site may block scripts); open it in a browser")
        else:
            ref.problems.append(f"the url is broken (HTTP {r.status_code})")
    except requests.RequestException:
        ref.problems.append("the url could not be reached")


def scholar_link(ref):
    """A Google Scholar search for the exact title, for the user or a browser the user watches. A matched
    entry uses the database's own spelling, since LaTeX math ($\\epsilon$) leaves holes in the cleaned title."""
    matched = ref.best and title_sim(ref.title, ref.best.title) >= TITLE_OK
    title = (ref.best.title if matched else ref.title or "").replace('"', " ").strip()
    exact = title and (matched or not re.search(r"\$|\\[A-Za-z]", ref.fields.get("title", "")))
    return "https://scholar.google.com/scholar?hl=en&q=" + quote_plus(f'"{title}"' if exact else title or ref.key)


def mark_duplicates(refs):
    groups = defaultdict(list)
    for r in refs:
        if r.best and r.status in ("OK", "CHECK"):
            ident = r.best.dblp or (r.best.doi or "").lower() or r.best.arxiv or norm_title(r.best.title)
            groups[ident].append(r)
    for rs in groups.values():
        if len(rs) > 1:
            for r in rs:
                others = ", ".join(o.key for o in rs if o is not r)
                r.notes.append(f"same paper as {others}; you may want to merge these keys")


# ----------------------------------------------------------------------------------------
# Interactive review
# ----------------------------------------------------------------------------------------

def read_pasted_bibtex():
    print("  Paste the BibTeX entry (for example from Google Scholar: Cite, then BibTeX).")
    print("  It is read until the braces close. Press Enter on an empty line to cancel.")
    lines, depth, started = [], 0, False
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not started and not line.strip():
            return None
        lines.append(line)
        for ch in line:
            if ch == "@":
                started = True
            elif ch == "{" and started:
                depth += 1
            elif ch == "}" and started:
                depth -= 1
        if started and depth <= 0 and "{" in "".join(lines):
            break
    return from_bibtex_text("\n".join(lines))


def describe(ref):
    f = ref.fields
    yours_venue = clean_latex(f.get("booktitle") or f.get("journal") or f.get("howpublished") or "")
    print(f"  Yours : {short(ref.title, 100) or '(no title)'}")
    print(f"          {surnames_for_display(ref.authors) or '(no authors)'} ({ref.year or '?'}) {short(yours_venue, 50)}")
    if ref.best:
        b = ref.best
        print(f"  Real  : {short(b.title, 100)}")
        print(f"          {surnames_for_display(b.authors)} ({b.year or '?'}) {short(b.venue, 50)}  [{b.source}]")
    for p in ref.problems:
        print(f"  Why   : {p}")
    for n in ref.notes:
        print(f"  Note  : {n}")
    print(f"  Scholar: {scholar_link(ref)}")


FLAGGED = ("CHECK", "NOT FOUND", "UNCHECKED")


def review(refs):
    flagged = [r for r in refs if r.status in FLAGGED]
    if not flagged:
        print("\nNothing to review. Every checked entry was confirmed.")
        return
    print(f"\nReviewing {len(flagged)} flagged entr{'y' if len(flagged) == 1 else 'ies'}.")
    for n, ref in enumerate(flagged, 1):
        print("\n" + "-" * 78)
        print(f"[{n}/{len(flagged)}] {ref.status}  {ref.key}")
        describe(ref)
        if ref.suggestion:
            print("\n  Suggested replacement (from %s):" % ref.suggestion[2])
            print("    " + format_entry(ref.suggestion[0], ref.key, ref.suggestion[1]).replace("\n", "\n    "))
        while True:
            opts = ("[a] use the suggested entry  " if ref.suggestion else "") + \
                   "[k] keep yours  [p] paste BibTeX  [o] open in browser  [x] mark as fake  [s] skip  [q] quit"
            try:
                choice = input(f"\n  {opts}\n  > ").strip().lower()[:1]
            except EOFError:
                choice = "q"
            if choice == "a" and ref.suggestion:
                ref.decision = "accept"
            elif choice == "k":
                ref.decision = "keep"
            elif choice == "x":
                ref.decision = "fake"
            elif choice == "s":
                ref.decision = "skip"
            elif choice == "p":
                pasted = read_pasted_bibtex()
                if not pasted:
                    print("  Could not read a BibTeX entry from that. Try again.")
                    continue
                ref.pasted, ref.decision = pasted, "paste"
            elif choice == "o":
                for url in (scholar_link(ref), ref.best.url if ref.best else None):
                    if url:
                        try:
                            webbrowser.open(url)
                        except Exception:
                            print(f"  Open this yourself: {url}")
                continue
            elif choice == "q":
                print("  Stopping the review. Entries not reviewed keep your original text.")
                return
            else:
                continue
            break


# ----------------------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------------------

def comment_block(text):
    return "\n".join("% " + line for line in text.splitlines())


def render_entry(ref):
    if ref.decision in ("auto", "accept") and ref.suggestion:
        return format_entry(ref.suggestion[0], ref.key, ref.suggestion[1])
    if ref.decision == "paste" and ref.pasted:
        return format_entry(ref.pasted[0], ref.key, ref.pasted[1])
    if ref.decision == "fake":
        return f"% [verify_bib] {ref.key} was marked as fake during review and is commented out.\n" + comment_block(ref.raw)
    if ref.decision == "keep" or ref.status in ("OK", "UNCHECKED"):
        return ref.raw
    lines = [f"% [verify_bib] {ref.status}: {p}" for p in ref.problems]
    return "\n".join(lines + [ref.raw])


def write_bib(path, blocks, refs_by_block, counts, src_name):
    text = (f"% Checked by verify_bib.py on {date.today()} from {src_name}.\n"
            f"% OK: {counts['OK']}, CHECK: {counts['CHECK']}, NOT FOUND: {counts['NOT FOUND']}, "
            f"WEB: {counts['WEB']}. Details are in the report file.\n\n")
    for i, (kind, _, _, raw) in enumerate(blocks):
        text += render_entry(refs_by_block[i]) if kind == "entry" and i in refs_by_block else raw
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def md_escape(s):
    return (s or "").replace("|", "\\|").replace("\n", " ")


def write_report(path, refs, counts, src_name, down=None):
    L = [f"# Reference check for {src_name}", ""]
    for host, why in sorted((down or {}).items()):
        L += [f"**Unavailable during this run: {host}**, because {why}. Entries it would have confirmed were flagged "
              "instead of passed. Run the check again later.", ""]
    L += [
         f"Checked {len(refs)} entries on {date.today()}. "
         f"OK: {counts['OK']}, CHECK: {counts['CHECK']}, NOT FOUND: {counts['NOT FOUND']}, WEB: {counts['WEB']}"
         + (f", UNCHECKED: {counts['UNCHECKED']}" if counts.get("UNCHECKED") else "") + ".", "",
         "OK means at least two independent databases found the paper and agree with your entry, so it was replaced "
         "with clean BibTeX that a second database confirms. "
         "CHECK means something in your entry is wrong, or only one database confirms it. "
         "NOT FOUND means no matching paper was found anywhere, so check it by hand. "
         "WEB means a website or software entry. Paper databases do not list these, so only the link was checked. "
         "UNCHECKED means the entry could not be parsed, so it was not looked up; fix its syntax.", ""]
    flagged = [r for r in refs if r.status in FLAGGED]
    if flagged:
        L += ["## Needs your attention", ""]
        for r in flagged:
            decided = {"accept": "fixed during review", "paste": "replaced with pasted BibTeX", "keep": "kept as is",
                       "fake": "marked as fake", "skip": "skipped during review"}.get(r.decision, "")
            L += [f"### {r.status}: `{r.key}`" + (f" ({decided})" if decided else ""), ""]
            L.append(f"- Your entry: \"{md_escape(r.title) or '(no title)'}\", {md_escape(surnames_for_display(r.authors)) or 'no authors'}, {r.year or 'no year'}")
            if r.best:
                L.append(f"- Closest real paper: \"{md_escape(r.best.title)}\", {md_escape(surnames_for_display(r.best.authors))}, "
                         f"{r.best.year or '?'}, {md_escape(short(r.best.venue, 80)) or 'venue unknown'} (found in {r.best.source})")
            L.append(f"- Confirmed by: {', '.join(r.confirmed_by) or 'no database'}")
            for p in r.problems:
                L.append(f"- Problem: {md_escape(p)}")
            for n in r.notes:
                L.append(f"- Note: {md_escape(n)}")
            links = [f"[Google Scholar]({scholar_link(r)})"]
            if r.best and r.best.url:
                links.append(f"[{r.best.source}]({r.best.url})")
            if r.best and r.best.doi:
                links.append(f"[DOI](https://doi.org/{r.best.doi})")
            L.append("- Links: " + " | ".join(links))
            if r.suggestion and r.decision not in ("accept", "paste", "fake"):
                L += ["", f"Suggested replacement (from {r.suggestion[2]}):", "", "```bibtex",
                      format_entry(r.suggestion[0], r.key, r.suggestion[1]), "```"]
            L.append("")
    L += ["## All entries", "", "| Status | Key | Title | Confirmed by | BibTeX from | Notes |", "|---|---|---|---|---|---|"]
    for r in refs:
        src = r.suggestion[2] if r.suggestion and r.decision in ("auto", "accept") else ("pasted" if r.decision == "paste" else "yours")
        notes = "; ".join(r.notes + ([f"lookup trouble: {', '.join(r.errors)}"] if r.errors else []))
        L.append(f"| {r.status} | `{r.key}` | {md_escape(short(r.title, 70))} | {', '.join(r.confirmed_by) or '-'} | {src} | {md_escape(notes)} |")
    # An exact-title search for every entry with a title; websites and software are checked through their own link
    L += ["", "## Google Scholar links", ""] + [f"- `{r.key}`: {scholar_link(r)}" for r in refs if r.title and r.status != "WEB"]
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------------------

def main():
    # Windows sends piped output through cp1252, which cannot print most non-Latin titles.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)  # progress shows up even when piped
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # BibTeX pasted during -i review
    ap = argparse.ArgumentParser(description="Check every entry of a .bib file against real paper databases.")
    ap.add_argument("bibfile", nargs="?")
    ap.add_argument("--bibtex", metavar="ID", help="print the official BibTeX for a DOI, arXiv ID or .bib URL, then exit")
    ap.add_argument("--key", default="KEY", help="citation key for --bibtex output")
    ap.add_argument("--search", metavar="QUERY", help="list candidate papers (with abstracts) for a title or keywords, then exit")
    ap.add_argument("-o", "--out", help="output .bib file (default: <name>.verified.bib)")
    ap.add_argument("--report", help="report file (default: <name>.report.md)")
    ap.add_argument("-i", "--interactive", action="store_true", help="review flagged entries one by one")
    ap.add_argument("--email", default=os.environ.get("VERIFY_BIB_EMAIL"), help="your email, for Crossref's and OpenAlex's polite pools")
    ap.add_argument("--only", nargs="+", metavar="KEY", help="check only these citation keys")
    ap.add_argument("--no-dblp", action="store_true", help="skip DBLP")
    ap.add_argument("--no-cache", action="store_true", help="ignore cached lookups from earlier runs")
    args = ap.parse_args()
    if MISSING:
        sys.exit(MISSING)

    if args.search:
        http = Http(Path(".verify_bib_cache.json"), False, args.email)
        preflight(http)
        hits = search_papers(http, args.search)
        for source, title, authors, year, venue, doi, aid, url, abstract in hits:
            ids = "  ".join(x for x in (f"DOI {doi}" if doi else "", f"arXiv {aid}" if aid else "", url or "") if x)
            print(f"[{source}] {title} ({year or '?'}). {surnames_for_display(authors, 6) or 'no authors listed'}. {short(venue or '', 80)}")
            if ids:
                print(f"    {ids}")
            if abstract:
                # the full abstract for the paper the query names, since specific claims often come last
                close = title_sim(args.search, title or "") >= TITLE_CLOSE
                print(f"    Abstract: {' '.join(abstract.split()) if close else short(abstract, 600)}")
        if not hits:
            print("No candidates found. Try fewer or different words, or an author's surname.")
        return
    if args.bibtex:
        got = fetch_bibtex(Http(Path(".verify_bib_cache.json"), False, args.email), args.bibtex)
        if not got:
            sys.exit(f"No BibTeX export found for {args.bibtex}. Either it is not a DOI, arXiv ID or URL, or the "
                     "source did not answer (rate limit or outage), so retry once before concluding there is none.")
        print(f"% BibTeX from {got[2]} for {args.bibtex}", file=sys.stderr)
        print(format_entry(got[0], args.key, got[1]))
        return
    if not args.bibfile:
        ap.error("give a .bib file to check, or --bibtex ID, or --search QUERY")

    src = Path(args.bibfile)
    if not src.exists():
        sys.exit(f"File not found: {src}")
    stem = src.name[:-4] if src.name.endswith(".bib") else src.name
    out_path = Path(args.out) if args.out else src.with_name(stem + ".verified.bib")
    report_path = Path(args.report) if args.report else src.with_name(stem + ".report.md")

    blocks = scan_bib(read_bib(src))
    strings = "\n".join(raw for kind, _, _, raw in blocks if kind == "string")
    refs, refs_by_block = [], {}
    for i, (kind, etype, key, raw) in enumerate(blocks):
        if kind != "entry" or not key:
            continue
        parsed = parse_entry(raw, strings)
        fields = {k.lower(): v for k, v in (parsed or {}).items() if k not in ("ID", "ENTRYTYPE")}
        ref = make_ref(key, etype, raw, fields)
        if parsed is None:
            ref.status = "UNCHECKED"
            ref.notes.append("could not parse this entry, so it was left as is")
        refs.append(ref)
        refs_by_block[i] = ref
    todo = [r for r in refs if (not args.only or r.key in args.only) and r.fields]
    if not todo:
        sys.exit("No entries to check.")

    http = Http(src.with_name(".verify_bib_cache.json"), not args.no_cache, args.email)
    preflight(http)
    if not S2_KEY:
        print("Tip: a free Semantic Scholar API key (export S2_API_KEY=...) makes this faster.")
    print(f"Checking {len(todo)} entries from {src.name}...\n")
    try:
        for n, ref in enumerate(todo, 1):
            lookup_basic(http, ref)
            hit = next((c.source for c in ref.cands if is_good(ref, c)), None)
            print(f"  [{n:>3}/{len(todo)}] {short(ref.key, 38):<38} {('found in ' + hit) if hit else 'not confirmed yet'}")
        if not args.no_dblp:
            lookup_dblp(http, todo)
        lookup_extra(http, todo)
        print("Comparing entries and fetching clean BibTeX...")
        for ref in todo:
            judge(ref)
            if http.down and ref.status in ("NOT FOUND", "CHECK"):
                ref.problems.append(f"some databases were unavailable during this run ({', '.join(sorted(http.down))}), "
                                    "so run the check again later before concluding anything")
            if ref.status == "NOT FOUND" and looks_like_web(ref):
                ref.status, ref.problems = "WEB", []
                check_link(http, ref)
                if ref.problems:
                    ref.status = "CHECK"
            if ref.status in ("OK", "CHECK"):
                ref.suggestion = suggest(http, ref)
            if ref.status == "OK":
                ref.decision = "auto" if ref.suggestion else "keep"
        mark_duplicates(todo)
    except KeyboardInterrupt:
        http.save()
        sys.exit("\nStopped. Lookups so far are cached, so the next run picks up quickly.")
    http.save()

    # Unparseable entries are not looked up, but they are reported and reviewed so they still get fixed.
    checked = [r for r in refs if not args.only or r.key in args.only]
    if args.interactive:
        review(checked)

    counts = {s: sum(1 for r in checked if r.status == s) for s in ("OK", "CHECK", "NOT FOUND", "WEB", "UNCHECKED")}
    write_bib(out_path, blocks, refs_by_block, counts, src.name)
    write_report(report_path, checked, counts, src.name, http.down)

    for host, why in sorted(http.down.items()):
        print(f"\nUnavailable during this run: {host}, because {why}. Entries it would have confirmed are flagged; rerun later.")
    print(f"\nOK: {counts['OK']}   CHECK: {counts['CHECK']}   NOT FOUND: {counts['NOT FOUND']}   WEB: {counts['WEB']}"
          + (f"   UNCHECKED: {counts['UNCHECKED']}" if counts["UNCHECKED"] else ""))
    for r in checked:
        if r.status in FLAGGED:
            print(f"  {r.status:<9} {r.key}: {short((r.problems or r.notes or [''])[0], 100)}")
    print(f"\nWrote {out_path} and {report_path}")
    if any(counts[s] for s in FLAGGED):
        print("Flagged entries keep your original text. Run again with -i to fix them one by one.")


if __name__ == "__main__":
    main()
