"""Build truth.json for the accuracy benchmark from the DOI and arXiv registries.

Run: python tests/benchmark/build_truth.py   (needs requests; writes tests/benchmark/truth.json)

Each real work in works.json is identified by a DOI or arXiv ID, and its title, authors and
year are taken from that registry, not from memory. A work whose registry title doesn't
contain its 'expect' fragment is rejected, so a mistyped identifier can't slip in. Works with
no identifier carry hand-written truth, marked source=manual for independent checking.
"""
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
S = requests.Session()
S.headers["User-Agent"] = "bib-check-agent benchmark builder (https://github.com/nnicholas-c/bib-check-agent)"


def get(url, **kw):
    for attempt in range(4):
        r = S.get(url, timeout=40, **kw)
        if r.status_code in (429, 500, 502, 503):
            time.sleep(3 * (attempt + 1))
            continue
        return r
    return r


def crossref(doi):
    r = get(f"https://api.crossref.org/works/{doi}")
    if r.status_code == 200:
        m = r.json()["message"]
        year = next((m[k]["date-parts"][0][0] for k in ("published-print", "published-online", "issued") if m.get(k, {}).get("date-parts", [[None]])[0][0]), None)
        title = " ".join(m.get("title") or [])
        if m.get("subtitle"):  # Crossref keeps subtitles apart; the citable title includes them
            title += ": " + " ".join(m["subtitle"])
        return {"title": " ".join(re.sub(r"<[^>]+>", "", title).split()), "authors": [a.get("family") or a.get("name", "") for a in m.get("author") or []],
                "authors_full": [" ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name", "") for a in m.get("author") or []],
                "year": year, "venue_found": " ".join(m.get("container-title") or []) or m.get("publisher", ""), "registry": "crossref"}
    r = get(f"https://doi.org/{doi}", headers={"Accept": "application/vnd.citationstyles.csl+json"})
    if r.status_code == 200:
        m = r.json()
        return {"title": m.get("title", ""), "authors": [a.get("family") or a.get("literal", "") for a in m.get("author") or []],
                "authors_full": [" ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("literal", "") for a in m.get("author") or []],
                "year": (m.get("issued", {}).get("date-parts") or [[None]])[0][0], "venue_found": m.get("container-title") or m.get("publisher", ""), "registry": "doi.org"}
    return None


def arxiv(aid):
    time.sleep(3)
    r = get("https://export.arxiv.org/api/query", params={"id_list": aid, "max_results": 1})
    ns = {"a": "http://www.w3.org/2005/Atom"}
    e = ET.fromstring(r.text).find("a:entry", ns)
    if e is None:
        return None
    names = [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)]
    return {"title": " ".join(e.findtext("a:title", "", ns).split()), "authors": [n.split()[-1] for n in names], "authors_full": names,
            "year": int(e.findtext("a:published", "", ns)[:4]), "venue_found": "arXiv", "registry": "arxiv"}


def main():
    spec = json.loads((HERE / "works.json").read_text(encoding="utf-8"))
    truth, problems = {}, []
    for w in spec["real"]:
        if "manual" in w:
            t = dict(w["manual"], source="manual")
        else:
            rec = crossref(w["doi"]) if "doi" in w else arxiv(w["arxiv"])
            if not rec:
                problems.append(f"{w['key']}: no registry record for {w.get('doi') or w.get('arxiv')}")
                continue
            if w["expect"] not in rec["title"].lower():
                problems.append(f"{w['key']}: registry title {rec['title']!r} lacks {w['expect']!r}")
                continue
            t = {"title": rec["title"], "authors": rec["authors"], "authors_full": rec["authors_full"], "year": w.get("year", rec["year"]),
                 "registry_year": rec["year"], "venue_found": rec["venue_found"], "source": rec["registry"]}
            if "doi" in w:
                t["doi"] = w["doi"]
            if "arxiv" in w:
                t["arxiv"] = w["arxiv"]
        if w.get("title_override"):  # the published version's title differs from the registry's
            t["title"] = w["title_override"]
        t.update({k: w[k] for k in ("venue", "alt_years", "alt_titles", "author_aliases", "alt_first_authors", "trap", "field", "type", "verified") if k in w})
        truth[w["key"]] = t
        print(f"{w['key']:<28} {t['source']:<9} {t['year']}  {t['title'][:70]}")
    (HERE / "truth.json").write_text(json.dumps(truth, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n{len(truth)} works written to truth.json")
    for p in problems:
        print("REJECTED", p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
