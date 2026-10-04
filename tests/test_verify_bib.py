"""Offline checks of fetch_bibtex routing, arXiv years and BibTeX cleanup. Run: python test_verify_bib.py"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "skills" / "bib-check-agent" / "scripts"))
import verify_bib as vb  # noqa: E402

# Crossref's JATS markup and HTML entities become LaTeX, and &, % and # are escaped
_, f = vb.tidy("article", {"journal": "Pattern Analysis &amp; Machine Intelligence", "title": "On <i>k</i>-means at 50% cost"})
assert f["journal"] == r"Pattern Analysis \& Machine Intelligence" and "<" not in f["title"] and r"50\%" in f["title"], f
_, f = vb.tidy("article", {"title": r"Already \& escaped"})
assert f["title"] == r"Already \& escaped", f
_, f = vb.tidy("article", {"title": "When $a<b$ and $c>d$ holds"})  # math is not a tag
assert f["title"] == "When $a<b$ and $c>d$ holds", f
_, f = vb.tidy("techreport", {"institution": "Research &amp; Development", "title": "T"})
assert f["institution"] == r"Research \& Development", f

# arXiv's export carries the latest revision's year; v1's date from the API replaces it,
# and the ID's YYMM is the fallback when the API does not answer
latest = "@misc{x, title={T}, author={A}, year={2023}, eprint={1706.03762}}"
vb_get = lambda year_text: type("H", (), {"get": lambda self, url, **kw: (200, year_text)})()
assert "year={2017}" in vb.arxiv_bibtex(vb_get(latest), "1706.03762v7")
assert "year={1999}" in vb.arxiv_bibtex(vb_get(latest), "hep-th/9901001")
assert "year={2003}" in vb.arxiv_bibtex(vb_get(latest), "math.GT/0309136")
ATOM = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/1801.00001v1</id><title>T</title>'
        '<published>2017-12-28T00:00:00Z</published><author><name>A</name></author></entry></feed>')
api = type("H", (), {"get": lambda self, url, **kw: (200, ATOM if "export.arxiv.org" in url else latest)})()
assert "year={2017}" in vb.arxiv_bibtex(api, "1801.00001"), "sent in December 2017, announced in January 2018"

# Old-style IDs lose their subject class for the API: math.GT/0309136 -> math/0309136
asked = []
cap = type("H", (), {"get": lambda self, url, params=None, **kw: asked.append(params) or (200, "<feed/>")})()
vb.arxiv_by_id(cap, "math.GT/0309136")
assert asked[0]["id_list"] == "math/0309136", asked

# The .bib is read strictly as UTF-8 (BOM allowed) with CRLF folded to \n, so writing it back can't double \r
import pathlib, tempfile
tmp = pathlib.Path(tempfile.mkdtemp())
(tmp / "crlf.bib").write_bytes(b"\xef\xbb\xbf@misc{a,\r\n  title={M\xc3\xbcller}\r\n}\r\n")
assert vb.read_bib(tmp / "crlf.bib") == "@misc{a,\n  title={Müller}\n}\n"
(tmp / "latin.bib").write_bytes(b"@misc{a, title={M\xfcller}}")
try:
    vb.read_bib(tmp / "latin.bib")
    raise AssertionError("a Latin-1 file must stop the run")
except SystemExit as e:
    assert "not UTF-8" in str(e)

# A blocked core database stops the run instead of letting real papers come back NOT FOUND.
# A rate limit (429) proves the host is reachable, and a passing outage (5xx) only warns.
def preflight_with(status_for):
    return vb.preflight(type("H", (), {"get": lambda self, url, **kw: (status_for(url), "")})())
for blocked in (lambda url: 0, lambda url: 403 if "arxiv" in url else 200):
    try:
        preflight_with(blocked)
        raise AssertionError("preflight must stop when a core host is blocked")
    except SystemExit as e:
        assert "Cannot reach the paper databases" in str(e) and "data.crosscite.org" in str(e)
preflight_with(lambda url: 429 if "semanticscholar" in url else 200)
preflight_with(lambda url: 503 if "crossref" in url else 200)

# A host that asks us to stay away for hours is dropped for the rest of the run instead of stalling it
class Resp:
    status_code, text, headers = 429, "", {"Retry-After": "61153"}
calls429 = []
h = vb.Http(pathlib.Path(tempfile.mkdtemp()) / "c.json", False, None)
h.session.request = lambda *a, **kw: calls429.append(1) or Resp()
assert h.get("https://api.openalex.org/works", params={"search": "x"})[0] == 429 and len(calls429) == 1
assert h.get("https://api.openalex.org/works", params={"search": "y"})[0] == 429 and len(calls429) == 1  # not called again
assert "api.openalex.org" in h.down

# The email goes to Crossref as a query parameter only, never in the User-Agent every host sees
h = vb.Http(pathlib.Path(tempfile.mkdtemp()) / "c.json", False, "me@example.org")
assert "example.org" not in h.session.headers["User-Agent"]

# --search rebuilds OpenAlex's abstract from its word -> positions index
import json
OA = {"results": [{"display_name": "Dropout", "publication_year": 2014, "doi": "https://doi.org/10.5555/x",
                   "authorships": [{"author": {"display_name": "Nitish Srivastava"}}],
                   "abstract_inverted_index": {"units": [2], "Randomly": [0], "drop": [1]}}]}
sent = []
oa = type("H", (), {"json": lambda self, url, params=None, **kw: sent.append(params) or ((200, OA) if "openalex" in url else (404, None)),
                    "get": lambda self, url, params=None, **kw: sent.append(params) or (404, "")})()
hit = vb.search_papers(oa, "Do transformers dream? The case for attention")[0]
assert hit[0] == "OpenAlex" and hit[5] == "10.5555/x" and hit[8] == "Randomly drop units", hit
assert "?" not in sent[0]["search"]                       # OpenAlex rejects ? and * with HTTP 400
arxiv_q = next(p["search_query"] for p in sent if p and "search_query" in p)
assert "all:the" not in arxiv_q.lower() and "all:for" not in arxiv_q.lower() and "all:transformers" in arxiv_q, arxiv_q
dead_oa = type("H", (), {"json": lambda self, url, **kw: (400, None), "get": lambda self, url, **kw: (404, "")})()
assert vb.search_papers(dead_oa, "x y z")[0][1].startswith("(no answer: HTTP 400")

# Double verification: one database is never enough to confirm an entry or to supply its BibTeX
TITLE = "Deep Residual Learning for Image Recognition"
cand = lambda src, year, authors=("Kaiming He", "Xiangyu Zhang"): vb.Cand(src, TITLE, list(authors), year, "CVPR")
fresh = lambda: vb.make_ref("he2016", "inproceedings", "", {"title": TITLE, "author": "Kaiming He and Xiangyu Zhang", "year": "2016", "booktitle": "CVPR"})
one = fresh(); one.cands = [cand("Semantic Scholar", 2016)]
vb.judge(one)
assert one.status == "CHECK" and "only one database" in one.problems[-1], one.problems
two = fresh(); two.cands = [cand("Semantic Scholar", 2016), cand("Crossref", 2016)]
vb.judge(two)
assert two.status == "OK" and two.confirmed_by == ["Crossref", "Semantic Scholar"], (two.status, two.problems)
same_source_twice = fresh(); same_source_twice.cands = [cand("Crossref", 2016), cand("Crossref", 2016)]
vb.judge(same_source_twice)
assert same_source_twice.status == "CHECK", "two records from one database are not two sources"
fields = {"title": TITLE, "author": "Kaiming He and Xiangyu Zhang", "year": "2016"}
assert vb.corroborated(two, fields, "DBLP")                                   # another database agrees
assert not vb.corroborated(two, dict(fields, year="2012"), "DBLP")            # every other database says 2016
assert not vb.corroborated(two, dict(fields, author="Ross Girshick"), "DBLP")  # wrong first author
assert not vb.corroborated(one, fields, "Semantic Scholar")                   # a source can't confirm itself

# Same-version guard: a cleanup must not swap the cited version for another one, or drop where it appeared
assert vb.same_venue("J. Mach. Learn. Res.", "Journal of Machine Learning Research")
assert vb.same_venue("Journal of Machine Learning Research", "J. Mach. Learn. Res.")  # either order
assert vb.same_venue("Advances in Neural Information Processing Systems 25", "NIPS")
assert not vb.same_venue("BMJ", "Systematic Reviews")
assert not vb.same_venue("Advances in Neural Information Processing Systems", "Communications of the ACM")
IMG = "ImageNet Classification with Deep Convolutional Neural Networks"
nips = vb.make_ref("k12", "inproceedings", "", {"title": IMG, "author": "Alex Krizhevsky and Ilya Sutskever and Geoffrey Hinton",
                                                 "booktitle": "Advances in Neural Information Processing Systems", "year": "2012"})
nips.cands = [vb.Cand("Semantic Scholar", IMG, ["Alex Krizhevsky", "I. Sutskever", "Geoffrey E. Hinton"], 2012, "Neural Information Processing Systems"),
              vb.Cand("Crossref", IMG, ["Alex Krizhevsky", "Ilya Sutskever", "Geoffrey E. Hinton"], 2017, "Communications of the ACM")]
reprint = {"title": IMG, "author": "Krizhevsky, Alex and Sutskever, Ilya and Hinton, Geoffrey E.", "journal": "Communications of the ACM", "year": "2017"}
assert "different version" in vb.version_conflict(nips, reprint)               # the 2012 paper exists, so keep it
wrong_year = vb.make_ref("k14", "inproceedings", "", {"title": TITLE, "author": "Kaiming He and Xiangyu Zhang", "booktitle": "CVPR", "year": "2010"})
wrong_year.cands = [cand("Semantic Scholar", 2016), cand("Crossref", 2016)]
assert vb.version_conflict(wrong_year, {"title": TITLE, "author": "Kaiming He", "booktitle": "CVPR", "year": "2016"}) is None  # a real fix
book = vb.make_ref("b", "book", "", {"title": "Deep Learning", "author": "Ian Goodfellow", "publisher": "MIT Press", "year": "2016"})
assert "no publisher" in vb.version_conflict(book, {"title": "Deep Learning", "author": "Ian Goodfellow", "year": "2016"})
assert "doi" not in vb.tidy("article", {"title": "T", "doi": "10.5555/2627435.2670313"})[1]  # ACM IDs aren't real DOIs

# Two planted errors the accuracy benchmark (tests/benchmark) caught getting through.
# Semantic Scholar gives ResNet's CVPR DOI with the arXiv year, which made a wrong 2014 look one year off.
res = vb.make_ref("he", "inproceedings", "", {"title": TITLE, "author": "Kaiming He and Xiangyu Zhang", "booktitle": "CVPR", "year": "2014"})
res.cands = [vb.Cand("Semantic Scholar", TITLE, ["Kaiming He", "X. Zhang"], 2015, "Computer Vision and Pattern Recognition", "10.1109/cvpr.2016.90"),
             vb.Cand("Crossref", TITLE, ["Kaiming He", "Xiangyu Zhang"], 2016, "2016 IEEE Conference on Computer Vision and Pattern Recognition (CVPR)", "10.1109/cvpr.2016.90"),
             vb.Cand("DBLP", TITLE, ["Kaiming He", "Xiangyu Zhang"], 2015, "CoRR", preprint=True)]
vb.judge(res)
assert any("yours 2014, real 2016" in p for p in res.problems), res.problems
fix = {"title": TITLE, "author": "Kaiming He", "booktitle": "CVPR", "year": "2016", "doi": "10.1109/CVPR.2016.90"}
assert vb.version_conflict(res, fix) is None  # the 2015 record has the fix's own DOI, so it isn't another version
# A wrong journal outside the known CS venues; Semantic Scholar's match is a different paper with one shared author.
PT = "Prospect Theory: An Analysis of Decision under Risk"
pt = vb.make_ref("k79", "article", "", {"title": PT, "author": "Daniel Kahneman and Amos Tversky", "journal": "American Economic Review", "year": "1979"})
pt.cands = [vb.Cand("Semantic Scholar", PT, ["Craig R. Fox", "A. Tversky"], 1995),
            vb.Cand("Crossref", PT, ["Daniel Kahneman", "Amos Tversky"], 2000, "Choices, Values, and Frames", "10.1017/cbo9780511803475.003"),
            vb.Cand("Crossref", PT, ["Daniel Kahneman", "Amos Tversky"], 1979, "Econometrica", "10.2307/1914185")]
vb.judge(pt)
assert pt.confirmed_by == ["Crossref"] and any("Econometrica" in p for p in pt.problems), (pt.confirmed_by, pt.problems)
assert vb.same_venue("Proc. Natl. Acad. Sci. U.S.A.", "Proceedings of the National Academy of Sciences of the United States of America")
assert vb.same_venue("PNAS", "Proceedings of the National Academy of Sciences") and vb.same_venue("The Lancet", "Lancet")
assert vb._name_hit("{International Human Genome Sequencing Consortium}", vb.surname_keys("International Human Genome Sequencing Consortium"))
assert not vb.same_venue("Nature", "Nature Communications") and not vb.same_venue("American Economic Review", "Econometrica")

# An entry with no comma after its key is still found, so it can be reported as unparseable
assert [b[2] for b in vb.scan_bib("@article{nokey\n title={X}}\n@misc{ok, title={Y}}") if b[0] == "entry"] == ["nokey", "ok"]

calls = []
ARXIV = "@misc{a, title={Paper one}, author={A. Author}, year={2017}}"
OTHER = "@article{b, title={Paper two}, author={B. Author}, year={2019}}"


class FakeHttp:
    def get(self, url, headers=None, **kw):
        calls.append(("get", url))
        return 200, OTHER


vb.arxiv_bibtex = lambda http, aid: calls.append(("arxiv", aid)) or ARXIV
vb.crossref_bibtex = lambda http, doi: calls.append(("crossref", doi)) or (OTHER if doi.startswith("10.1145") else None)


def route(ident):
    calls.clear()
    got = vb.fetch_bibtex(FakeHttp(), ident)
    return calls[:], got


for ident, aid in (("1412.6980", "1412.6980"), ("1412.6980v9", "1412.6980"), ("arXiv:hep-th/9901001", "hep-th/9901001"),
                   ("hep-th/9901001v2", "hep-th/9901001"), ("https://arxiv.org/abs/1706.03762v5", "1706.03762"),
                   ("https://arxiv.org/abs/hep-th/9901001", "hep-th/9901001"), ("https://arxiv.org/bibtex/1706.03762", "1706.03762"),
                   ("10.48550/arXiv.1412.6980", "1412.6980"),
                   ("https://doi.org/10.48550/ARXIV.1412.6980", "1412.6980")):
    c, got = route(ident)
    assert c == [("arxiv", aid)] and got[2] == "arXiv" and got[1]["title"] == "Paper one", (ident, c, got)

for ident in ("https://doi.org/10.1145/3065386", "doi.org/10.1145/3065386", "https://www.doi.org/10.1145/3065386", "doi:10.1145/3065386"):
    c, got = route(ident)
    assert c == [("crossref", "10.1145/3065386")] and got[2] == "Crossref", (ident, c)

c, got = route("10.5281/zenodo.1234")  # not in Crossref: falls back to doi.org content negotiation
assert c == [("crossref", "10.5281/zenodo.1234"), ("get", "https://doi.org/10.5281/zenodo.1234")] and got[2] == "doi.org", c

c, got = route("https://aclanthology.org/N19-1423.bib")
assert c == [("get", "https://aclanthology.org/N19-1423.bib")] and got[2] == "aclanthology.org", c

assert route("not an id") == ([], None)
print("ok")
