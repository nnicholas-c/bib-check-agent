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
