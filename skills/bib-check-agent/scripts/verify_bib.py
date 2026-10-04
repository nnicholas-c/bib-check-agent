#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["requests", "rapidfuzz", "bibtexparser<2"]
# ///
"""
verify_bib.py - check every entry of a .bib file against real paper databases.
Part of Bib Check Agent (https://github.com/nnicholas-c/bib-check-agent), MIT licensed.

For each reference the script looks the paper up in Semantic Scholar, DBLP, Crossref and
arXiv (and optionally Google Scholar through SerpApi), decides whether the entry is real and
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
                                                    OpenAlex, Semantic Scholar, Crossref and arXiv)

Optional environment variables
  S2_API_KEY        free Semantic Scholar key, makes runs faster and steadier
  SERPAPI_KEY       needed for --scholar and --scholar-all (Google Scholar has no official API)
  VERIFY_BIB_EMAIL  your email, sent to Crossref so you get their faster "polite" rate limit

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
SERPAPI_KEY = os.environ.get("SERPAPI_KEY")

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
    "serpapi.com": 1.0,
    "api.openalex.org": 0.2,
    "scholar.googleusercontent.com": 2.0,
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
    ("arXiv", [r"\barxiv\b", r"\bcorr\b"]),
]

MONTHS = {m[:3].lower(): m for m in ("January February March April May June July August September "
                                      "October November December").split()}
# When two records match equally well, show the more carefully curated one.
SOURCE_RANK = {"DBLP": 3, "Crossref": 2, "Semantic Scholar": 1, "arXiv": 1, "Google Scholar": 0}
WEB_TYPES = {"misc", "online", "software", "manual", "electronic", "www", "webpage", "dataset"}

STOPWORDS = set("a an the of for and in on to with by from at as is are be via using towards "
                "toward into over under its their our your we you all can not do does".split())


# ----------------------------------------------------------------------------------------
# HTTP with per-host pacing, retries and a local cache
# ----------------------------------------------------------------------------------------

class Http:
    def __init__(self, cache_file, use_cache, email):
        self.session = requests.Session()
        ua = "verify-bib/1.0 (reference checker for a personal .bib file)"
        self.session.headers["User-Agent"] = ua  # the email goes only to Crossref, as its mailto parameter
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
        if self.email and "api.crossref.org" in url:
            params["mailto"] = self.email
        visible = {k: v for k, v in params.items() if k not in ("api_key", "mailto")}
        key = json.dumps([method, url, sorted(visible.items()), data], default=str)
        if cache and self.use_cache and key in self.cache:
            return tuple(self.cache[key])
        host = urlparse(url).netloc
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
            if status == 429 and not retry_429:
                break
            if status in (0, 429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(min(60.0, max(retry_after, 2.0 ** (attempt + 1))))
                continue
            break
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


def norm_title(s):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", fold(clean_latex(s)).lower()).split())


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
        return {_key(name)}  # corporate author such as {OpenAI}
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
    if cand.truncated_authors:  # Google Scholar shows only the first few names
        ents = ents[: len(cand_names)]
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
    truncated_authors: bool = False
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
    scholar_checked: bool = False


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
        closer = "}" if opener == "{" else ")"
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
    return Cand("Crossref", strip_tags(" ".join(it.get("title") or [])), authors, year,
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
                              "select": "DOI,title,author,issued,published-print,published-online,container-title,type"})
    if st != 200 or not j:
        return []
    return [crossref_cand(it) for it in (j.get("message") or {}).get("items", [])]


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


def dblp_find(http, items):
    """items: list of (ref index, title). Returns (ref index, dblp key) pairs with the same title."""
    blocks = []
    for i, t in items:
        words, n = dblp_words(t), dblp_norm(t)
        if words and len(n) >= 8:
            blocks.append(f'{{ BIND({i} AS ?seed) ?text ql:contains-entity ?title . '
                          f'?text ql:contains-word "{" ".join(words)}" . ?pub dblp:title ?title . '
                          f'FILTER(REPLACE(LCASE(STR(?title)), "[^a-z0-9]", "") = "{n}") }}')
    if not blocks:
        return []
    rows = sparql(http, "SELECT ?seed ?pub WHERE { " + " UNION ".join(blocks) + " }")
    if rows is not None:
        return [(int(r["seed"]["value"]), r["pub"]["value"].replace(DBLP_REC, "")) for r in rows]
    if len(items) > 1:  # too heavy or failed: split the batch and try again
        mid = len(items) // 2
        return dblp_find(http, items[:mid]) + dblp_find(http, items[mid:])
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


# Google Scholar has no official API and blocks scripts, so it is reached through SerpApi.

class ScholarOff(Exception):
    pass


def serpapi(http, params):
    st, txt = http.get("https://serpapi.com/search.json", params={**params, "api_key": SERPAPI_KEY}, tries=2)
    try:
        j = json.loads(txt) if txt else {}
    except ValueError:
        j = {}
    if st != 200 or j.get("error"):
        msg = j.get("error") or f"HTTP {st}"
        if "hasn't returned any results" in msg.lower():
            return {}
        raise ScholarOff(msg)
    return j


def scholar_search(http, title):
    cands = []
    for r in serpapi(http, {"engine": "google_scholar", "q": title, "num": 3}).get("organic_results", [])[:3]:
        info = r.get("publication_info") or {}
        summary = info.get("summary", "")
        authors = [a.get("name", "") for a in info.get("authors") or []]
        parts = summary.split(" - ")
        if not authors and parts:
            authors = [a.strip() for a in parts[0].split(",") if a.strip()]
        cands.append(Cand("Google Scholar", r.get("title", ""), authors, to_year(summary),
                          parts[1] if len(parts) >= 3 else "", url=r.get("link"),
                          truncated_authors=True, extra={"result_id": r.get("result_id")}))
    return cands


def scholar_bibtex(http, result_id):
    j = serpapi(http, {"engine": "google_scholar_cite", "q": result_id})
    link = next((x.get("link") for x in j.get("links") or [] if x.get("name") == "BibTeX"), None)
    if not link:
        return None
    st, txt = http.get(link, tries=2)
    return txt if st == 200 and txt.lstrip().startswith("@") else None


HOSTS = ("api.semanticscholar.org, api.crossref.org, export.arxiv.org, arxiv.org, doi.org, data.crosscite.org, "
         "sparql.dblp.org and api.openalex.org (plus serpapi.com and scholar.googleusercontent.com with --scholar)")
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
    for c in crossref_search(http, query, "")[:n]:
        found.append(("Crossref", c.title, c.authors, c.year, c.venue, c.doi, None, c.url, None))
    # arXiv's index drops stopwords, so ANDing one of them would match nothing
    words = [w for w in re.findall(r"\w+", clean_latex(query)) if len(w) > 2 and w.lower() not in ARXIV_STOP][:8]
    if words:
        st, txt = http.get("https://export.arxiv.org/api/query",
                           params={"search_query": " AND ".join(f"all:{w}" for w in words), "max_results": n})
        try:
            ns = {"a": "http://www.w3.org/2005/Atom"}
            for e in ET.fromstring(txt).findall("a:entry", ns) if st == 200 else []:
                aid = re.sub(r"v\d+$", "", e.findtext("a:id", "", ns).rsplit("/abs/", 1)[-1])
                found.append(("arXiv", " ".join(e.findtext("a:title", "", ns).split()),
                              [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)],
                              to_year(e.findtext("a:published", "", ns)), "arXiv", None, aid,
                              f"https://arxiv.org/abs/{aid}", " ".join(e.findtext("a:summary", "", ns).split())))
        except ET.ParseError:
            pass
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


def lookup_basic(http, ref):
    """Identifier checks plus title search in Semantic Scholar, then Crossref if needed."""
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
    if not any(is_good(ref, c) for c in ref.cands):
        ref.cands += crossref_search(http, ref.title, first_surname(ref))


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
    for b in batches:
        for i, k in dblp_find(http, b):
            wanted[i].add(k)
    recs = dblp_details(http, sorted(set().union(*wanted.values()))) if wanted else {}
    if not recs and wanted:
        print("  DBLP did not answer, continuing without it.")
    for i, keys in wanted.items():
        have = {c.dblp for c in refs[i].cands if c.source == "DBLP"}
        refs[i].cands += [dblp_cand(k, recs[k]) for k in sorted(keys) if k in recs and k not in have]


def lookup_scholar(http, refs, everything):
    todo = [r for r in refs if r.title and (everything or not any(is_good(r, c) for c in r.cands))]
    if not todo:
        return
    print(f"Searching Google Scholar (SerpApi) for {len(todo)} entr{'y' if len(todo) == 1 else 'ies'}...")
    for r in todo:
        try:
            r.cands += scholar_search(http, r.title)
            r.scholar_checked = True
        except ScholarOff as e:
            print(f"  Google Scholar stopped answering ({e}); continuing without it.")
            return


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
        where = "Semantic Scholar, DBLP, Crossref" + (", arXiv" if ref.arxiv else "")
        if ref.scholar_checked:
            where += ", Google Scholar"
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

    years = [c.year for c in same if c.year]
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

    ref.problems += ref.id_problems
    ref.status = "CHECK" if ref.problems else "OK"


def suggest(http, ref, scholar_bib):
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
    try:
        if scholar_bib:
            sc = next((c for c in versions if c.source == "Google Scholar" and c.extra.get("result_id")), None)
            if sc:
                txt = scholar_bibtex(http, sc.extra["result_id"])
                if txt and from_bibtex_text(txt):
                    return (*from_bibtex_text(txt), "Google Scholar")
    except ScholarOff:
        pass
    dblp = [c for c in versions if c.source == "DBLP"]
    published = [c for c in dblp if not c.preprint]
    if published:
        c = max(published, key=lambda c: version_rank(ref, c))
        if c.extra["btype"] == "article" and c.doi:
            txt = crossref_bibtex(http, c.doi)
            if txt and from_bibtex_text(txt):
                return (*from_bibtex_text(txt), "Crossref")
        return (*tidy(*dblp_fields(c)), "DBLP")
    doi = next((c.doi for c in versions if c.doi and not c.doi.lower().startswith("10.48550/")), None)
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
    return "https://scholar.google.com/scholar?q=" + quote_plus(ref.title or ref.key)


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


def write_report(path, refs, counts, src_name):
    L = [f"# Reference check for {src_name}", "",
         f"Checked {len(refs)} entries on {date.today()}. "
         f"OK: {counts['OK']}, CHECK: {counts['CHECK']}, NOT FOUND: {counts['NOT FOUND']}, WEB: {counts['WEB']}"
         + (f", UNCHECKED: {counts['UNCHECKED']}" if counts.get("UNCHECKED") else "") + ".", "",
         "OK means the paper was found and your entry matches it, so it was replaced with clean BibTeX. "
         "CHECK means a real paper was found but something in your entry is wrong. "
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
    L += ["## All entries", "", "| Status | Key | Title | BibTeX from | Notes |", "|---|---|---|---|---|"]
    for r in refs:
        src = r.suggestion[2] if r.suggestion and r.decision in ("auto", "accept") else ("pasted" if r.decision == "paste" else "yours")
        notes = "; ".join(r.notes + ([f"lookup trouble: {', '.join(r.errors)}"] if r.errors else []))
        L.append(f"| {r.status} | `{r.key}` | {md_escape(short(r.title, 70))} | {src} | {md_escape(notes)} |")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------------------

def main():
    # Windows sends piped output through cp1252, which cannot print most non-Latin titles.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # BibTeX pasted during -i review
    ap = argparse.ArgumentParser(description="Check every entry of a .bib file against real paper databases.")
    ap.add_argument("bibfile", nargs="?")
    ap.add_argument("--bibtex", metavar="ID", help="print the official BibTeX for a DOI, arXiv ID or .bib URL, then exit")
    ap.add_argument("--key", default="KEY", help="citation key for --bibtex output")
    ap.add_argument("--search", metavar="QUERY", help="list candidate papers (with abstracts) for a title or keywords, then exit")
    ap.add_argument("-o", "--out", help="output .bib file (default: <name>.verified.bib)")
    ap.add_argument("--report", help="report file (default: <name>.report.md)")
    ap.add_argument("-i", "--interactive", action="store_true", help="review flagged entries one by one")
    ap.add_argument("--scholar", action="store_true",
                    help="also search Google Scholar (needs SERPAPI_KEY) for entries the free sources could not confirm")
    ap.add_argument("--scholar-all", action="store_true",
                    help="take the BibTeX of every confirmed entry from Google Scholar (about 2 SerpApi searches per entry)")
    ap.add_argument("--email", default=os.environ.get("VERIFY_BIB_EMAIL"), help="your email, for Crossref's polite pool")
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

    if (args.scholar or args.scholar_all) and not SERPAPI_KEY:
        sys.exit("--scholar needs a SerpApi key. Get one at https://serpapi.com (the free plan has 250 searches "
                 "a month), then run:  export SERPAPI_KEY=your_key")

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
        if args.scholar or args.scholar_all:
            lookup_scholar(http, todo, args.scholar_all)
        print("Comparing entries and fetching clean BibTeX...")
        for ref in todo:
            judge(ref)
            if ref.status == "NOT FOUND" and looks_like_web(ref):
                ref.status, ref.problems = "WEB", []
                check_link(http, ref)
                if ref.problems:
                    ref.status = "CHECK"
            if ref.status in ("OK", "CHECK"):
                ref.suggestion = suggest(http, ref, args.scholar_all)
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
    write_report(report_path, checked, counts, src.name)

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
