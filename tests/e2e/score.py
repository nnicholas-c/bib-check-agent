"""Score a finished bib-check-agent run on the test paper against the answer key.

Usage: python score.py RUN_DIR [ORIGINAL_DIR]
RUN_DIR is a copy of a fixture (tests/e2e/fixture, or the smaller fixture-small) that the skill has
processed. ORIGINAL_DIR defaults to the pristine fixture the copy came from, told apart by the
sections/ folder only the 19-entry fixture has. Checks about keys the original lacks are skipped. MUST checks are correctness and safety: any failure exits 1.
SHOULD checks are quality: they show how many planted problems the run fixed the right way.
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "skills" / "bib-check-agent" / "scripts"))
import verify_bib as vb  # noqa: E402

CITE = re.compile(r"\\[A-Za-z]*cite[A-Za-z]*\*?(?:\[[^\]]*\]){0,2}\{([^}]*)\}")
PAIR = {"vaswani2017attention", "vaswani2017transformer"}


def entries(path):
    """key -> (comment lines just above the entry, parsed fields or None, raw text)"""
    text = path.read_text(encoding="utf-8-sig")
    blocks = vb.scan_bib(text)
    strings = "\n".join(raw for kind, _, _, raw in blocks if kind == "string")
    out, before = {}, ""
    for kind, _, key, raw in blocks:
        if kind == "entry" and key:
            parsed = vb.parse_entry(raw, strings)
            fields = {k.lower(): v for k, v in (parsed or {}).items() if k not in ("ID", "ENTRYTYPE")}
            comments = [l.strip() for l in before.splitlines() if l.strip().startswith("%")]
            out[key] = (comments, fields if parsed else None, raw)
            before = ""
        else:
            before += raw
    return out, text


def tex_files(d):
    return {p.relative_to(d).as_posix(): p.read_text(encoding="utf-8") for p in sorted(d.rglob("*.tex"))}


def main():
    run = Path(sys.argv[1]).resolve()
    # only the 19-entry fixture has a sections/ folder
    orig = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else HERE / ("fixture" if (run / "sections").is_dir() else "fixture-small")
    before, _ = entries(orig / "refs.bib")
    after, after_text = entries(run / "refs.bib")
    results = []

    def check(level, name, ok, detail="", needs=()):
        if all(k in before for k in needs):
            results.append((level, name, bool(ok), detail))

    def f(key, field):
        e = after.get(key)
        return vb.clean_latex((e[1] or {}).get(field, "")) if e else ""

    def marked(key, word):
        return key in after and any(word in c for c in after[key][0])

    # MUST: correctness and safety
    survivors = PAIR & set(after)
    retired = set(before) - set(after)
    pair = PAIR <= set(before)
    check("MUST", "every original key kept" + (", except one of the duplicate pair" if pair else ""),
          (retired <= PAIR and len(survivors) == 1) if pair else not retired, f"removed: {sorted(retired) or 'none'}")
    unparsed = [k for k, (c, fields, _) in after.items() if fields is None and not any("UNRESOLVED" in x for x in c)]
    check("MUST", "every entry parses (unless marked UNRESOLVED)", not unparsed, ", ".join(unparsed))
    tex0, tex1 = tex_files(orig), tex_files(run)
    strip = lambda s: CITE.sub("", s).replace("\r\n", "\n")
    changed = [n for n in tex0 if strip(tex0[n]) != strip(tex1.get(n, ""))]
    check("MUST", ".tex files changed only inside cite commands", not changed, ", ".join(changed))
    cited = {k.strip() for s in tex1.values() for m in CITE.finditer(s) for k in m.group(1).split(",") if k.strip()}
    check("MUST", "every cited key exists in the .bib", cited <= set(after), ", ".join(sorted(cited - set(after))))
    check("MUST", "chen2023gradfold (made up) is marked FABRICATED or UNRESOLVED",
          marked("chen2023gradfold", "FABRICATED") or marked("chen2023gradfold", "UNRESOLVED"), needs=["chen2023gradfold"])
    original = (orig / "refs.bib").read_bytes()
    lf = lambda b: b.replace(b"\r\n", b"\n")
    baks = [p for p in run.glob("refs.bib.bak*") if lf(p.read_bytes()) == lf(original)]
    git = subprocess.run(["git", "-C", str(run), "show", "HEAD:refs.bib"], capture_output=True)
    check("MUST", "the original .bib is recoverable (.bak or git)",
          baks or (git.returncode == 0 and git.stdout.replace(b"\r\n", b"\n") == original.replace(b"\r\n", b"\n")))
    check("MUST", "a decisions file was written", (run / "refs.decisions.md").exists())

    # SHOULD: quality of each fix
    dec = (run / "refs.decisions.md").read_text(encoding="utf-8") if (run / "refs.decisions.md").exists() else ""
    check("SHOULD", "kingma2014adam: ICLR 2015", f("kingma2014adam", "year") == "2015" and
          re.search(r"ICLR|Learning Representations", f("kingma2014adam", "booktitle"), re.I), needs=["kingma2014adam"])
    check("SHOULD", "devlin2019bert: Toutanova, not Le", "Toutanova" in f("devlin2019bert", "author") and
          "Quoc" not in f("devlin2019bert", "author"), needs=["devlin2019bert"])
    check("SHOULD", "smith2014dropout: replaced with Srivastava et al. (Dropout)",
          "Srivastava" in f("smith2014dropout", "author") and "Dropout" in f("smith2014dropout", "title"),
          "marked FABRICATED instead" if marked("smith2014dropout", "FABRICATED") else "", needs=["smith2014dropout"])
    check("SHOULD", "radford2019gpt2: wrong arXiv ID removed", "2005.14165" not in after.get("radford2019gpt2", ([], {}, "x"))[2], needs=["radford2019gpt2"])
    check("SHOULD", "ioffe:2015-bn: year 2015", f("ioffe:2015-bn", "year") == "2015", needs=["ioffe:2015-bn"])
    mnih = after.get("mnih2015dqn", ([], {}, ""))[2]
    check("SHOULD", "mnih2015dqn: AlphaGo DOI removed", "nature16961" not in mnih, needs=["mnih2015dqn"])
    check("SHOULD", "hestness2017broken: repaired", f("hestness2017broken", "year") == "2017" and
          "Predictable" in f("hestness2017broken", "title"), needs=["hestness2017broken"])
    check("SHOULD", "nocomma2016: repaired", "Rethinking the Inception" in f("nocomma2016", "title"), needs=["nocomma2016"])
    attention = re.search(r"needs your attention(.*?)(?=^## |\Z)", dec, re.I | re.S | re.M)
    check("SHOULD", "hochreiter1997lstm reported as not supporting its sentence",
          (attention and "hochreiter1997lstm" in attention.group(1))
          or re.search(r"hochreiter1997lstm.{0,400}(mismatch|not support|doesn't support|unrelated|says nothing)", dec, re.I | re.S)
          or re.search(r"(mismatch|not support|doesn't support).{0,400}hochreiter1997lstm", dec, re.I | re.S), needs=["hochreiter1997lstm"])
    for key, name in (("maldacena1997", "Maldacena"), ("scholkopf2002kernels", "Smola"), ("sutskever2013thesis", "Sutskever"),
                      ("goodfellow2016deep", "Goodfellow"), ("he2016resnet", "He"), ("brown2020gpt3", "Brown"),
                      ("hochreiter1997lstm", "Hochreiter"), ("pytorch", "PyTorch")):
        check("SHOULD", f"{key}: real source kept as real", name in f(key, "author") and
              not marked(key, "FABRICATED") and not marked(key, "UNRESOLVED"), needs=[key])
    stale = [k for k, (c, _, _) in after.items() if any("[verify_bib]" in x for x in c) and not any("UNRESOLVED" in x for x in c)]
    check("SHOULD", "no leftover [verify_bib] comments", not stale, ", ".join(stale))
    check("SHOULD", "checker header removed", "Checked by verify_bib.py" not in after_text)
    added = sorted(set(after) - set(before))
    check("SHOULD", "no entries added (substitutes belong in the decisions file)", not added, ", ".join(added))
    expected = re.compile(r"(\.bak\d*|\.verified\.bib|\.report\.md|\.decisions\.md|\.verify_bib_cache\.json|\.gitignore)$")
    have = {p.relative_to(orig).as_posix() for p in orig.rglob("*") if p.is_file()}
    stray = sorted(n for p in run.rglob("*") if p.is_file() and not {".git", ".claude"} & set(p.relative_to(run).parts)
                   for n in [p.relative_to(run).as_posix()] if n not in have and not expected.search(n))
    check("SHOULD", "no other files created in the project", not stray, ", ".join(stray[:5]) + (" ..." if len(stray) > 5 else ""))

    for level, name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {level:<6} {name}" + (f"  ({detail})" if detail else ""))
    must = [r for r in results if r[0] == "MUST"]
    should = [r for r in results if r[0] == "SHOULD"]
    print(f"\nMUST {sum(r[2] for r in must)}/{len(must)}   SHOULD {sum(r[2] for r in should)}/{len(should)}")
    sys.exit(0 if all(r[2] for r in must) else 1)


if __name__ == "__main__":
    main()
