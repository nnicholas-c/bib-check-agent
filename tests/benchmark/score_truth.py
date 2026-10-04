"""Score a run on the accuracy benchmark against registry-backed ground truth.

Usage:
  python tests/benchmark/score_truth.py RUN_DIR            score the final refs.bib after the skill ran
  python tests/benchmark/score_truth.py RUN_DIR --checker  score the checker's own refs.verified.bib

The headline number is FAULTY REFERENCES, which should be zero:
  - a real work whose entry has the wrong title, first author, authors, year, venue, DOI or arXiv ID
  - a real work marked as fabricated
  - an invented reference left looking real, or swapped for another paper without saying so
An entry left flagged for the author (UNRESOLVED, or a "% [verify_bib]" comment in checker mode)
is not faulty, only unfinished.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "skills" / "bib-check-agent" / "scripts"))
import verify_bib as vb  # noqa: E402

VENUE_FIELDS = ("journal", "journaltitle", "booktitle", "publisher", "school", "institution", "organization",
                "howpublished", "note", "series", "eprint", "archiveprefix", "eprinttype", "url")


def entries(path):
    """key -> (comment lines just above the entry, parsed fields or None)"""
    blocks = vb.scan_bib(path.read_text(encoding="utf-8-sig"))
    strings = "\n".join(raw for kind, _, _, raw in blocks if kind == "string")
    out, before = {}, ""
    for kind, _, key, raw in blocks:
        if kind == "entry" and key:
            parsed = vb.parse_entry(raw, strings)
            out[key] = ([l.strip() for l in before.splitlines() if l.strip().startswith("%")],
                        {k.lower(): v for k, v in parsed.items() if k not in ("ID", "ENTRYTYPE")} if parsed else None)
            before = ""
        else:
            before += raw
    return out


def citable(t):
    t = re.sub(r"^[IVX]+\.\s*[—-]+\s*", "", t)
    return t.title() if t.isupper() else t


def score_real(t, comments, f, checker):
    if any("FABRICATED" in c for c in comments):
        return "FALSE FABRICATED", ["a real work was marked as made up"]
    if any("UNRESOLVED" in c for c in comments) or (checker and any("[verify_bib]" in c for c in comments)):
        return "LEFT FOR AUTHOR", []
    if f is None:
        return "FAULTY", ["the entry does not parse"]
    p = []
    title = vb.clean_latex(f.get("title", ""))
    sim = max(vb.title_sim(title, x) for x in (t["title"], citable(t["title"]), *t.get("alt_titles", [])))
    if sim < 90:
        p.append(f"title {title!r} (similarity {sim})")
    authors = [a for a in vb.split_authors(f.get("author", "") or f.get("editor", "")) if a.lower() != "others"]
    truth_keys = set().union(*(vb.surname_keys(a) for a in t["authors"] + t.get("author_aliases", []))) if t["authors"] else set()
    if authors and t["authors"]:
        if not vb._name_hit(authors[0], vb.surname_keys(t["authors"][0])):
            p.append(f"first author {vb.clean_latex(authors[0])!r}, not {t['authors'][0]}")
        wrong = [a for a in authors[:10] if not vb._name_hit(a, truth_keys)]
        if wrong:
            p.append(f"authors not on the paper: {', '.join(vb.clean_latex(a) for a in wrong)}")
    year = vb.to_year(f.get("year") or f.get("date"))
    venue_text = " ".join(vb.clean_latex(f.get(k, "")) for k in VENUE_FIELDS).lower()
    allowed = {t["year"], *t.get("alt_years", [])}
    if t.get("registry_year") and "arxiv" in venue_text:  # citing the preprint, with its own year, is legitimate
        allowed.add(t["registry_year"])
    if year not in allowed:
        p.append(f"year {year}, not {t['year']}")
    named = vb.clean_latex(f.get("journal") or f.get("journaltitle") or f.get("booktitle") or "")
    if not any(v.lower() in venue_text or (named and vb.same_venue(v, named)) for v in t.get("venue", [])):  # abbreviations count
        p.append(f"venue {venue_text[:80]!r} matches none of {t.get('venue')}")
    doi = vb.strip_doi(f.get("doi", "")).lower()
    ok_dois = {t.get("doi", "").lower()} | ({f"10.48550/arxiv.{t['arxiv']}".lower()} if t.get("arxiv") else set())
    if doi and doi not in ok_dois:
        p.append(f"DOI {doi} belongs to another work")
    eprint = re.sub(r"v\d+$", "", f.get("eprint", "").strip())
    if eprint and t.get("arxiv") and eprint != t["arxiv"]:
        p.append(f"arXiv ID {eprint}, not {t['arxiv']}")
    return ("FAULTY", p) if p else ("CORRECT", [])


def score_fabricated(x, comments, f, decisions, checker):
    if f is None and not comments:
        return "FAULTY", ["the entry was deleted, so its citation is now undefined"]
    if any("FABRICATED" in c or "UNRESOLVED" in c for c in comments) or (checker and any("[verify_bib]" in c for c in comments)):
        return "FLAGGED", []
    title = vb.clean_latex((f or {}).get("title", ""))
    if vb.title_sim(title, x["title"]) >= 95:
        return "FAULTY", ["the invented reference was left looking real"]
    disclosed = re.search(rf"{re.escape(x['key'])}[^\n]*SUBSTITUTED|SUBSTITUTED[^\n]*{re.escape(x['key'])}", decisions)
    return ("SUBSTITUTED", [f"now cites {title!r}"]) if disclosed else ("FAULTY", [f"silently replaced with {title!r}"])


def main():
    run = Path(sys.argv[1]).resolve()
    checker = "--checker" in sys.argv
    truth = json.loads((HERE / "truth.json").read_text(encoding="utf-8"))
    expected = json.loads((HERE / "expected.json").read_text(encoding="utf-8"))
    fab = {x["key"]: x for x in json.loads((HERE / "fabricated.json").read_text(encoding="utf-8"))["fabricated"]}
    got = entries(run / ("refs.verified.bib" if checker else "refs.bib"))
    dec = run / "refs.decisions.md"
    decisions = dec.read_text(encoding="utf-8") if dec.exists() else ""
    rows = []
    for key, exp in expected.items():
        comments, f = got.get(key, ([], None))
        if key not in got:
            rows.append((key, exp["kind"], "FAULTY", ["the entry is missing, so its citation is undefined"]))
        elif exp["kind"] == "fabricated":
            rows.append((key, exp["kind"], *score_fabricated(fab[key], comments, f, decisions, checker)))
        else:
            rows.append((key, exp["kind"], *score_real(truth[key], comments, f, checker)))
    for key, kind, verdict, why in rows:
        if verdict != "CORRECT" and verdict != "FLAGGED":
            print(f"{verdict:<16} {kind:<15} {key}: {'; '.join(why)}")
    count = lambda kind, verdict: sum(1 for r in rows if r[1] == kind and r[2] == verdict)
    faulty = [r for r in rows if r[2] in ("FAULTY", "FALSE FABRICATED")]
    print(f"\nMode: {'checker only (refs.verified.bib)' if checker else 'final refs.bib'}")
    for kind in ("real", "real-corrupted"):
        n = sum(1 for r in rows if r[1] == kind)
        print(f"  {kind:<15} {n:>3}: correct {count(kind, 'CORRECT')}, left for the author {count(kind, 'LEFT FOR AUTHOR')}, "
              f"faulty {count(kind, 'FAULTY')}, called made up {count(kind, 'FALSE FABRICATED')}")
    n = sum(1 for r in rows if r[1] == "fabricated")
    print(f"  {'fabricated':<15} {n:>3}: flagged {count('fabricated', 'FLAGGED')}, disclosed substitutes {count('fabricated', 'SUBSTITUTED')}, "
          f"faulty {count('fabricated', 'FAULTY')}")
    print(f"\nFAULTY REFERENCES: {len(faulty)}")
    sys.exit(1 if faulty else 0)


if __name__ == "__main__":
    main()
