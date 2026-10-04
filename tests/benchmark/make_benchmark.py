"""Generate the accuracy benchmark paper (refs.bib, main.tex, expected.json) from truth.json.

Run: python tests/benchmark/make_benchmark.py

Every real work becomes one entry. Most are written correctly; the ones in CORRUPT get exactly
one kind of planted error. Every invented reference in fabricated.json becomes an entry too.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "paper"

# One planted error per entry: what a hurried author, or a language model, typically gets wrong
CORRUPT = {
    "kingma2015adam": {"what": "wrong venue and year", "set": {"booktitle": "International Conference on Machine Learning (ICML)", "year": "2014"}},
    "devlin2019bert": {"what": "wrong author", "swap_author": ("Toutanova", "Quoc V. Le")},
    "he2016deep": {"what": "wrong year", "set": {"year": "2014"}},
    "silver2016mastering": {"what": "DOI of another paper (the DQN paper)", "set": {"doi": "10.1038/nature14236"}},
    "lowry1951protein": {"what": "wrong year", "set": {"year": "1959"}},
    "kahneman1979prospect": {"what": "wrong journal", "set": {"journal": "American Economic Review"}},
    "jinek2012programmable": {"what": "first author missing", "drop_first_author": True},
    "watson1953molecular": {"what": "title cut short", "set": {"title": "Molecular Structure of Nucleic Acids"}},
    "shannon1948mathematical": {"what": "misspelled title", "set": {"title": "A Mathematical Theory of Communications"}},
    "breiman2001random": {"what": "wrong year and journal", "set": {"year": "1999", "journal": "Journal of Machine Learning Research"}},
    "ronneberger2015unet": {"what": "arXiv ID of another paper (ResNet)", "set": {"eprint": "1512.03385", "archiveprefix": "arXiv"}},
    "hochreiter1997long": {"what": "wrong second author", "swap_author": ("Schmidhuber", "Yoshua Bengio")},
    "dempster1977maximum": {"what": "wrong year", "set": {"year": "1987"}},
    "goodfellow2014gan": {"what": "wrong venue", "set": {"booktitle": "International Conference on Machine Learning (ICML)"}},
    "ho2020denoising": {"what": "authors in the wrong order", "rotate_authors": True},
    "touvron2023llama": {"what": "preprint claimed as a NeurIPS paper", "as_type": "inproceedings", "set": {"booktitle": "Advances in Neural Information Processing Systems"}},
    "harris2020array": {"what": "DOI of another paper (SciPy)", "set": {"doi": "10.1038/s41592-019-0686-2"}},
    "tibshirani1996regression": {"what": "wrong authors", "set": {"author": "Bradley Efron and Robert Tibshirani"}},
}

PUBLISHED_AT = {  # arXiv papers published at a venue: the version a paper normally cites
    "brown2020language": "Advances in Neural Information Processing Systems",
    "kingma2015adam": "International Conference on Learning Representations (ICLR)",
    "vaswani2017attention": "Advances in Neural Information Processing Systems",
    "goodfellow2014gan": "Advances in Neural Information Processing Systems",
    "mikolov2013distributed": "Advances in Neural Information Processing Systems",
    "sutskever2014sequence": "Advances in Neural Information Processing Systems",
    "bahdanau2015neural": "International Conference on Learning Representations (ICLR)",
    "wei2022chain": "Advances in Neural Information Processing Systems",
    "ho2020denoising": "Advances in Neural Information Processing Systems",
    "kingma2014auto": "International Conference on Learning Representations (ICLR)",
    "dosovitskiy2021image": "International Conference on Learning Representations (ICLR)",
    "krizhevsky2012imagenet": "Advances in Neural Information Processing Systems",
    "ren2015faster": "Advances in Neural Information Processing Systems",
}


def tex(s):
    return re.sub(r"(?<!\\)([&%#])", r"\\\1", s)


def citable_title(t):
    t = re.sub(r"^[IVX]+\.\s*[\u2014-]+\s*", "", t)  # Mind's "I.—COMPUTING ..." numbering
    if t.isupper():
        t = t.title()
    return t


def entry(key, etype, fields):
    body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields.items() if v)
    return f"@{etype}{{{key},\n{body}\n}}"


def main():
    truth = json.loads((HERE / "truth.json").read_text(encoding="utf-8"))
    fab = json.loads((HERE / "fabricated.json").read_text(encoding="utf-8"))["fabricated"]
    entries, sentences, expected = [], [], {}
    for i, (key, t) in enumerate(truth.items()):
        names = t.get("authors_full") or t["authors"]
        authors = " and ".join(names[:10]) + (" and others" if len(names) > 10 else "")
        venue = (t.get("venue") or [""])[0]
        kind = t.get("type") or ("inproceedings" if key in PUBLISHED_AT else "misc" if t.get("source") == "arxiv" else "article")
        f = {"author": tex(authors), "title": tex(citable_title(t["title"]))}
        if kind == "article":
            f["journal"] = tex(t.get("venue_found") or venue)
        elif kind == "inproceedings":
            f["booktitle"] = tex(PUBLISHED_AT.get(key) or t.get("venue_found") or venue)
        elif kind == "book":
            f["publisher"] = venue
        elif kind == "phdthesis":
            f["school"] = venue
        elif kind == "misc" and t.get("source") == "arxiv":
            f.update(eprint=t["arxiv"], archiveprefix="arXiv")
        elif kind == "misc":
            f["howpublished"] = "RFC 2616" if "rfc" in key else venue
        f["year"] = str(t["year"])
        if t.get("doi") and i % 2 == 0:  # about half the entries carry their DOI, as in real bibliographies
            f["doi"] = t["doi"]
        c = CORRUPT.get(key)
        if c:
            if c.get("as_type"):
                kind = c["as_type"]
                f.pop("eprint", None)
                f.pop("archiveprefix", None)
            names2 = list(names[:10])
            if c.get("swap_author"):
                old, new = c["swap_author"]
                names2 = [new if old in n else n for n in names2]
            if c.get("drop_first_author"):
                names2 = names2[1:]
            if c.get("rotate_authors"):
                names2 = names2[-1:] + names2[:-1]
            if names2 != list(names[:10]):
                f["author"] = tex(" and ".join(names2))
            f.update(c.get("set", {}))
        entries.append(entry(key, kind, f))
        sentences.append(f"For {tex(citable_title(t['title']).rstrip('.'))}, see \\citet{{{key}}}.")
        expected[key] = {"kind": "real-corrupted" if c else "real", "error": c["what"] if c else None}
    for x in fab:
        f = {"author": " and ".join(x["authors"]), "title": x["title"]}
        f["journal" if x["type"] == "article" else "booktitle"] = x["venue"]
        f.update({k: x[k] for k in ("volume", "pages", "doi") if x.get(k)})
        f["year"] = str(x["year"])
        entries.append(entry(x["key"], x["type"], f))
        sentences.append(f"{x['sentence']} \\citep{{{x['key']}}}.")
        expected[x["key"]] = {"kind": "fabricated", "error": "invented reference"}
    OUT.mkdir(exist_ok=True)
    (OUT / "refs.bib").write_text("\n\n".join(entries) + "\n", encoding="utf-8", newline="\n")
    (OUT / "main.tex").write_text("\\documentclass{article}\n\\usepackage{natbib}\n\\begin{document}\n\\section{Related work}\n"
                                  + "\n".join(sentences) + "\n\\bibliographystyle{plainnat}\n\\bibliography{refs}\n\\end{document}\n",
                                  encoding="utf-8", newline="\n")
    (HERE / "expected.json").write_text(json.dumps(expected, indent=1) + "\n", encoding="utf-8")
    kinds = [e["kind"] for e in expected.values()]
    print({k: kinds.count(k) for k in sorted(set(kinds))}, "entries written to", OUT)


if __name__ == "__main__":
    main()
