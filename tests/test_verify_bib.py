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
# A paper cited at a venue is never swapped for its arXiv preprint, whatever the BibTeX source
uai = vb.make_ref("j", "inproceedings", "", {"title": "Who Guards the Guardians?", "author": "Shruti Joshi",
                  "booktitle": "Proceedings of the 42nd Conference on Uncertainty in Artificial Intelligence", "year": "2026"})
assert uai.venue == "UAI" and vb.venue_code("Proceedings of The 26th International Conference on Artificial Intelligence and Statistics") == "AISTATS"
corr = {"title": "Who Guards the Guardians?", "author": "Shruti Joshi", "journal": "CoRR", "volume": "abs/2602.24278", "year": "2026",
        "doi": "10.48550/ARXIV.2602.24278"}
assert "arXiv preprint" in vb.version_conflict(uai, corr)
arx = vb.make_ref("a", "article", "", {"title": "Skywork-Reward", "author": "Chris Liu", "journal": "arXiv preprint arXiv:2410.18451", "year": "2024"})
assert vb.version_conflict(arx, dict(corr, title="Skywork-Reward", author="Chris Liu", year="2024")) is None  # an arXiv entry may get arXiv BibTeX
# A cleanup never reorders authors: dblp lists two PRISM authors swapped, while NeurIPS and arXiv agree with the entry
prism = vb.make_ref("p", "inproceedings", "", {"title": "The PRISM Alignment Dataset", "booktitle": "Advances in Neural Information Processing Systems",
                    "author": "Kirk, Hannah Rose and Margatina, Katerina and Ciro, Juan and Mosquera, Rafael and Bartolo, Max", "year": "2024"})
swapped = {"title": "The PRISM Alignment Dataset", "booktitle": "NeurIPS 2024", "year": "2024",
           "author": "Hannah Rose Kirk and Katerina Margatina and Rafael Mosquera Gomez and Juan Ciro and Max Bartolo"}
assert "different order" in vb.version_conflict(prism, swapped)
assert vb.version_conflict(prism, dict(swapped, author="Hannah Rose Kirk and Katerina Margatina and Juan Ciro and Rafael Mosquera Gomez and Max Bartolo")) is None
assert vb._same_person("Perez-Nieves, Nicolas", "Nicolas Perez Nieves")
chapter = vb.make_ref("c", "incollection", "", {"title": "Prospect Theory", "author": "Daniel Kahneman", "booktitle": "Choices, Values, and Frames", "year": "2000"})
assert "no journal or booktitle" in vb.version_conflict(chapter, {"title": "Prospect Theory", "author": "Daniel Kahneman", "year": "2000"})
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

# The extra databases: canned answers shaped like the live ones, through a stand-in for Http
class Canned:
    def __init__(self, answers):
        self.answers, self.down, self.asked = answers, {}, []

    def json(self, url, params=None, **kw):
        self.asked.append(url)
        return next((v for k, v in self.answers.items() if k in url), (404, None))

    def get(self, url, params=None, **kw):
        st, j = self.json(url, params)
        return st, j if isinstance(j, str) else ""


assert vb.title_sim("RETRACTED: Long term toxicity of a Roundup herbicide", "Long term toxicity of a Roundup herbicide") == 100
fate = vb.openreview_fate
assert fate("Submitted to ICLR 2025", "ICLR.cc/2025/Conference/Rejected_Submission", "") == "rejected"
assert fate("Submitted to ICLR 2023", "ICLR.cc/2023/Conference", "") == "rejected"  # v1 keeps the accepted venueid
assert fate("", "", "ICLR.cc/2022/Conference/-/Withdrawn_Submission") == "withdrawn"
assert fate("MTI-LLM @ NeurIPS 2025 Poster", "NeurIPS.cc/2025/Workshop/MTI-LLM", "") == "workshop"  # never "workshop" in the venue
assert fate("Under review for TMLR", "TMLR/Under_Review", "") == "under review"
assert fate("Decision pending for TMLR", "TMLR/Decision_Pending", "") == "under review"
assert fate("ICLR 2021 Oral", "ICLR.cc/2021/Conference", "") == "accepted"
note = lambda venue, vid, authors=("Ann Lee",): {"id": "x", "forum": "x", "invitations": [""], "content": {
    "title": {"value": "Estimating Empowerment"}, "venue": {"value": venue}, "venueid": {"value": vid}, "authors": {"value": list(authors)}}}
got, st = vb.openreview_search(Canned({"api2.openreview.net": (200, {"notes": [
    note("MTI-LLM @ NeurIPS 2025 Poster", "NeurIPS.cc/2025/Workshop/MTI-LLM"), note("ICLR 2026 Poster", "ICLR.cc/2026/Conference", ["Anonymous"])]})}),
    "Estimating Empowerment", v1=False)
assert st == 200 and [(c.venue, c.preprint, c.extra["fate"]) for c in got] == [("", True, "workshop")]  # no venue; the anonymous one is dropped
assert vb.openreview_search(Canned({"api2.openreview.net": (500, None), "api.openreview.net": (200, {"notes": []})}), "Estimating Empowerment")[1] == 500

# A rejection is flagged only on the same track and year, and only an edition-dated record at that venue and year overrides it
EMP = "Estimating Empowerment"
emp = lambda booktitle: vb.make_ref("e", "inproceedings", "", {"title": EMP, "author": "Ann Lee and Bo Kim", "booktitle": booktitle, "year": "2025"})
rej = vb.Cand("OpenReview", EMP, ["Ann Lee", "Bo Kim"], 2025, "", url="u", preprint=True,
              extra={"fate": "rejected", "where": "Submitted to ICLR 2025 ICLR.cc/2025/Conference/Rejected_Submission", "workshop": False})
arx = vb.Cand("arXiv", EMP, ["Ann Lee", "Bo Kim"], 2024, "arXiv", preprint=True)
for extra_cands, booktitle, flagged in (([], "ICLR", True),
                                        ([vb.Cand("DBLP", EMP, ["Ann Lee", "Bo Kim"], 2026, "ICLR 2026")], "ICLR", True),  # another year's ICLR
                                        ([vb.Cand("DBLP", EMP, ["Ann Lee", "Bo Kim"], 2025, "ICLR 2025")], "ICLR", False),
                                        ([], "ICLR 2025 Workshop on Agents", False)):  # a workshop paper, not the main track
    r = emp(booktitle); r.cands = [rej, arx] + extra_cands
    vb.judge(r)
    assert any("OpenReview shows" in p for p in r.problems) == flagged, (booktitle, extra_cands, r.problems)

# Europe PMC: letters, notices and arXiv copies that repeat a title are not the paper; a retraction counts only for the cited version
GM = "Long term toxicity of a Roundup herbicide and a Roundup-tolerant genetically modified maize"
rec = lambda i, types, year, **kw: {"id": i, "source": kw.get("source", "MED"), "title": kw.get("title", GM), "pubYear": str(year),
                                     "pubTypeList": {"pubType": types}, "authorList": {"author": [{"firstName": "Gilles", "lastName": "Seralini"}]},
                                     "journalInfo": {"journal": {"title": kw.get("journal", "Food Chem Toxicol")}}, "doi": kw.get("doi"),
                                     "bookOrReportDetails": {"publisher": kw.get("publisher", "")}}
got, _ = vb.europepmc_search(Canned({"ebi.ac.uk": (200, {"hitCount": 5, "resultList": {"result": [
    rec("1", ["Journal Article", "Retracted Publication"], 2012, doi="10.1016/j.fct.2012.08.005"),
    rec("2", ["Letter", "Comment"], 2013), rec("3", ["Retraction Notice"], 2013, title="Retraction notice to " + GM),
    rec("4", ["Preprint"], 2012, source="PPR", publisher="arXiv")]}})}), GM, "Seralini")
assert [(c.year, c.extra["retracted"]) for c in got] == [(2012, True)], [(c.year, c.title) for c in got]
gm = lambda year, doi=None: vb.make_ref("s", "article", "", {"title": GM, "author": "Gilles Seralini", "journal": "Food Chem Toxicol", "year": year, **({"doi": doi} if doi else {})})
r = gm("2014"); r.cands = got + [vb.Cand("Crossref", GM, ["Gilles Seralini"], 2012, "Food and Chemical Toxicology")]
vb.judge(r)
assert any("the year differs (yours 2014, real 2012)" in p for p in r.problems), r.problems  # a 2013 letter no longer hides it
r = gm("2014", doi="10.1016/j.fct.2014.99.999"); r.cands = list(got)  # the republished version, with its own DOI
vb.judge(r)
assert not any("retracted" in p for p in r.problems) and any("retracted version" in n for n in r.notes)

# A database that fails is never listed as searched, so NOT FOUND names only those that answered
r = vb.make_ref("x", "article", "", {"title": "Some Title Nobody Wrote", "author": "Ann Lee"})
vb.lookup_basic(Canned({"semanticscholar": (200, {"data": []}), "crossref": (503, None), "openalex": (200, {"results": []})}), r)
assert r.searched == ["Semantic Scholar", "OpenAlex"] and r.errors == ["Crossref did not answer"], (r.searched, r.errors)
answered = set()
real_sparql, vb.sparql = vb.sparql, lambda http, q: None  # dblp down
assert vb.dblp_find(None, [(0, "Some Title Nobody Wrote")], answered) == [] and not answered
vb.sparql = real_sparql

# A rejected submission is not "the version the entry cites", so it can't block the right BibTeX
r = emp("ICLR"); r.cands = [rej]
assert vb.version_conflict(r, {"title": EMP, "author": "Ann Lee", "booktitle": "ICLR", "year": "2027"}) is None

# The report links every entry to an exact-title Google Scholar search, except websites, which are checked through their URL
rep_path = pathlib.Path(tempfile.mkdtemp()) / "r.report.md"
paper, site = vb.make_ref("p1", "article", "", {"title": 'A "Quoted" Title', "author": "Ann Lee"}), vb.make_ref("w1", "misc", "", {"title": "PyTorch", "url": "https://pytorch.org"})
paper.status, site.status = "OK", "WEB"
vb.write_report(rep_path, [paper, site], {"OK": 1, "CHECK": 0, "NOT FOUND": 0, "WEB": 1, "UNCHECKED": 0}, "r.bib")
links = rep_path.read_text(encoding="utf-8").split("## Google Scholar links", 1)[1]
assert "`p1`: https://scholar.google.com/scholar?hl=en&q=%22A++Quoted++Title%22" in links and "w1" not in links, links
math = vb.make_ref("m", "article", "", {"title": r"Learning with $\epsilon$-greedy Exploration", "author": "Ann Lee"})
assert "%22" not in vb.scholar_link(math)  # LaTeX math leaves holes, so no exact phrase
math.best = vb.Cand("Crossref", "Learning with ε-greedy Exploration", ["Ann Lee"])
math.title = vb.clean_latex(math.fields["title"])
assert vb.title_sim(math.title, math.best.title) < vb.TITLE_OK or "%22" in vb.scholar_link(math)

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
