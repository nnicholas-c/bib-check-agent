# Bib Check Agent

An open-source [Agent Skill](https://agentskills.io) that finds and fixes hallucinated, wrong, or unsupported citations in LaTeX bibliographies. It is built for Claude Code, Claude.ai, Codex and ChatGPT, GitHub Copilot, Cursor, Gemini CLI, OpenCode, Goose and other agents that load `SKILL.md` skills, including agents running open-source models on your own machine. So far it has been tested in Claude Code, with Claude and with a local Qwen model (see [Where it works](#where-it-works)).

AI writing tools invent references that look real. Bib Check Agent checks every entry of your `.bib` file against real paper databases. It then researches each problem, fixes what can be fixed, and tells you plainly which citations it could not find anywhere.

**What it catches:**
- references that don't exist
- wrong authors, years or venues
- DOIs and arXiv IDs that point to a different paper
- duplicate entries
- entries too broken to parse
- citations whose paper doesn't support the sentence citing them

**How it decides:**
- **Every entry is checked** against Semantic Scholar, DBLP, Crossref, OpenAlex and arXiv by a deterministic Python checker. No model is involved in the lookups.
- **Two independent sources, always.** An entry counts as confirmed only when at least two different databases agree on its title and authors. An entry only one database knows is flagged and confirmed in a second place by the agent. Replacement BibTeX must also be backed by a second source, and the checker never swaps your paper for a different version of it, such as the journal reprint of a conference paper.
- **Google Scholar, where it can be used.** Google Scholar has no official API and blocks scripts, so the checker reaches it through [SerpApi](https://serpapi.com) when you set `SERPAPI_KEY` and pass `--scholar`. The agent may also open Google Scholar in its own web tools, but it never tries to get around a CAPTCHA.
- **Each flagged entry is researched** from evidence the agent actually opened, never from memory. New BibTeX comes from official exports (Crossref, arXiv, ACL Anthology and others), under your original citation key.
- **Every decision is re-checked.** In Claude Code with dynamic workflows on, a separate reviewer tries to refute each fix. Calling a reference made up needs two independent searches to fail, and disagreements come back to you as UNRESOLVED instead of a guess.
- **Your files are backed up first**, unless git already holds them. Citation keys are the only thing it changes in your `.tex` files, and only to merge duplicates. It never rewrites your sentences.

## Quick start

1. **Install** the skill for your agent (see [Install](#install)). In Claude Code:
   ```text
   /plugin marketplace add nnicholas-c/bib-check-agent
   /plugin install bib-check-agent@bib-check-agent
   ```
2. **Open your paper's folder** in the agent. Commit to git first if you use it, so you can review the changes as a diff.
3. **Ask:** `check the references in refs.bib`, or run `/bib-check-agent refs.bib`. On a Pro plan, first turn on Dynamic workflows in `/config`; without them Claude Code checks one entry at a time with no separate reviewer. If Claude Code asks whether to run the workflow, choose Yes.
4. **Read `refs.decisions.md`.** It starts with what needs your attention.

You need Python 3 and an internet connection. The first time it runs, the skill installs its three Python packages into a private folder inside itself, so nothing global changes. On Debian or Ubuntu (including WSL), also install `python3-venv` or [uv](https://docs.astral.sh/uv/). On Windows, Claude Code works best with [Git for Windows](https://git-scm.com/downloads/win) installed, because the skill runs its commands through Claude Code's Bash tool.

## Install

Every route installs the same `skills/bib-check-agent/` folder. If Claude Code's `/plugin marketplace add` fails with an SSH host key error, use the HTTPS URL instead: `/plugin marketplace add https://github.com/nnicholas-c/bib-check-agent.git`.

| Agent | Install |
|---|---|
| Claude Code | `/plugin marketplace add nnicholas-c/bib-check-agent`, then `/plugin install bib-check-agent@bib-check-agent` |
| Claude.ai, Claude Desktop, Cowork | Customize > Plugins > Add marketplace > `nnicholas-c/bib-check-agent`. Or download `bib-check-agent.zip` from [Releases](https://github.com/nnicholas-c/bib-check-agent/releases) and upload it under Customize > Skills. |
| Codex CLI and IDE, ChatGPT desktop | `npx skills add nnicholas-c/bib-check-agent -a codex`, or copy the folder to `~/.agents/skills/` |
| ChatGPT web (Business, Enterprise, Healthcare, Edu) | Plugins > Plugin Directory > Skills > Create > Upload, with `bib-check-agent.zip` from Releases |
| Cursor, OpenCode, Goose, Cline and most others | `npx skills add nnicholas-c/bib-check-agent` |
| GitHub Copilot, or any agent `gh` knows | `gh skill install nnicholas-c/bib-check-agent bib-check-agent --agent <agent> --scope user` |
| Gemini CLI | `gemini skills install https://github.com/nnicholas-c/bib-check-agent.git --path skills/bib-check-agent` |
| Manual | Copy `skills/bib-check-agent` into `~/.claude/skills/` (Claude Code; also read by OpenCode, Goose and VS Code) and/or `~/.agents/skills/` (Codex, Gemini CLI, OpenCode, Goose, Copilot, Cursor) |

## How to use it

**Start it** from your paper's folder, in whichever way your agent invokes skills:

```text
check the references in refs.bib                (any agent; it picks the skill up)
/bib-check-agent refs.bib                        (Claude Code; as a plugin, /bib-check-agent:bib-check-agent)
$bib-check-agent refs.bib                        (Codex)
```

If you don't name a file, it finds the `.bib` files your main `.tex` file uses.

**What happens:**
1. **Checking.** The checker looks up every entry and marks each OK (confirmed by two or more databases, listed in the report's "Confirmed by" column), CHECK (a real paper with a wrong detail, or one only a single database confirms), NOT FOUND, WEB (a website or software link) or UNCHECKED (couldn't be parsed). The first run takes a few minutes, because the databases limit how fast you can query them. Later runs reuse a cache.
2. **Research.** The agent researches each flagged entry, fetches official BibTeX for real papers, and checks that each cited paper supports the sentence citing it. In Claude Code this runs in parallel, with a reviewer re-checking every decision. Other agents work through the entries one at a time.
3. **Fixing.** It backs up your files, applies the fixes, merges duplicate keys, runs the checker once more on the result, and writes the decisions file.

**What you get:**
- **`refs.bib`, fixed.** Made-up entries stay in place under a `% FABRICATED` comment, so the paper still compiles.
- **`refs.decisions.md`.** Every researched change, with its reason and an evidence link. It starts like this (from a real test run):

  > **Fabricated entries**
  > `chen2023gradfold`: "Recursive Gradient Folding for Memory-Efficient Transformer Training" (Chen, Martinez, Keller, ICLR 2023). No such paper exists. ICLR's OpenReview records, Google Scholar, arXiv, Crossref and OpenAlex show nothing…
  >
  > **Citations that don't support their sentence**
  > `hochreiter1997lstm` (main.tex line 13): "Fully convolutional networks produce dense pixel-wise predictions…" The LSTM paper covers only recurrent networks. Suggested substitute: Long, Shelhamer and Darrell, CVPR 2015, with its BibTeX…
  >
  > **Entries changed**
  > `kingma2014adam`: FIXED. The paper appeared at ICLR 2015, not ICML 2014.
  > `mnih2015dqn`: FIXED. The DOI 10.1038/nature16961 belongs to the AlphaGo paper; the correct one is 10.1038/nature14236.
- **`refs.report.md`.** The checker's own report from the final rerun.
- **Cleaned-up confirmed entries.** Entries the databases confirm are also replaced with the database's BibTeX, without a line in the decisions file. `git diff` shows these; see [Limits](#limits).

**Review and undo.** With git, run `git diff` to see every change and `git checkout -- refs.bib` to undo. Without git, the originals are in `refs.bib.bak` (and `main.tex.bak` if a duplicate merge touched it). An existing backup is never overwritten; later runs use `.bak2`, `.bak3` and so on.

**What it leaves to you:** your sentences. A made-up citation, or one that doesn't support its sentence, gets a suggested rewording or a real paper to cite, but you make the edit.

## Accuracy

Measured on the 68-entry [benchmark](#development) in October 2026, scored against DOI and arXiv registry records. The benchmark has 38 real works, 18 real works with one planted error each and 12 invented references. OpenAlex was out of quota for the whole run and Semantic Scholar had no API key, so these are close to worst-case conditions.

| | Checker 1.0 | Checker 1.1 | Full skill 1.1, Claude Code |
|---|---|---|---|
| **Faulty references** | **3** | **0** | **0** |
| Real entries correct | 31 of 38 (4 flagged) | 32 of 38 (6 flagged) | 38 of 38 |
| Planted errors | 2 fixed, 16 flagged | 1 fixed, 17 flagged | 18 fixed |
| Invented references | 12 flagged | 12 flagged | 8 marked FABRICATED, 4 replaced with a real paper for the same claim, each disclosed |

The three faults in 1.0 were cleanups that changed what an entry cites:
- the PRISMA statement swapped from *BMJ* for its *Systematic Reviews* co-publication
- a book's publisher dropped
- a thesis's school dropped

Version 1.1 refuses those changes. It also requires two independent databases to agree before it passes an entry or uses their BibTeX. The full run used 85 agents and took 19 minutes. Every one of its decisions on a real paper cites two to five independent sites. Every FABRICATED verdict came from a search of the web and the paper databases, and 5 of the 8 also searched Google Scholar directly. Each one then survived two separate reviewers, one hunting for the paper by its title and one by its authors.

## Where it works

The checker needs outbound HTTPS to `api.semanticscholar.org`, `api.crossref.org`, `export.arxiv.org`, `arxiv.org`, `doi.org`, `data.crosscite.org`, `sparql.dblp.org` and `api.openalex.org`.
- **Core databases blocked:** if Crossref, arXiv or Semantic Scholar is unreachable, the checker stops with "Cannot reach the paper databases" rather than reporting real papers as missing.
- **DBLP or OpenAlex blocked, or out of quota:** the checker still runs and names the missing database at the top of the report. Entries it then couldn't confirm twice are flagged, never passed.
- **Website and software entries:** checking one also opens that entry's own `url`.

| Platform | Status | Network notes |
|---|---|---|
| Claude Code (CLI, IDE, desktop) | Tested on Windows 11: fixed every planted problem in the 19-entry test paper | Works as installed. With the Claude Code sandbox on, add the hosts to `sandbox.network.allowedDomains`. |
| Claude Code with a local model (Ollama) | Tested; see [local models](docs/local-models.md) | Works as installed |
| Claude.ai, Claude Desktop, Cowork | Should work (documented) | Settings > Capabilities: turn on code execution and network egress, and allow the hosts above or all domains. On Team and Enterprise an owner sets this. |
| Codex CLI and IDE, ChatGPT desktop | Should work (documented) | Network is off in the default sandbox. Set `network_access = true` under `[sandbox_workspace_write]` in `~/.codex/config.toml`, or approve the prompt. |
| ChatGPT web (Business, Enterprise, Healthcare, Edu) | Should work where the workspace allows skills and public internet access | Individual Plus and Pro plans don't have skills yet |
| GitHub Copilot (VS Code, CLI, cloud agent) | Should work (documented) | Cloud agent: add the hosts to the firewall allowlist |
| Cursor | Should work (documented) | Allow the hosts in `sandbox.json`, or approve running outside the sandbox |
| Gemini CLI, OpenCode, Goose, Cline, Amp, Junie, Kiro, OpenHands | Should work (documented) | Usually the machine's own network |
| Claude API Skills | Not supported | The container has no network access |

"Should work" means the platform documents the skill format, paths and network access this skill needs, but it hasn't been run there yet. Reports and fixes are welcome.

## Local and open-source models

The checker is plain Python, so any model with reliable tool calling and a large enough context window can drive the skill. In testing on an RTX 5090, Qwen 3.8 27B handled the whole job with no Anthropic account. The smaller Qwen 3.5 9B and 4B models couldn't, but they stopped without changing any files. See **[docs/local-models.md](docs/local-models.md)** for:
- tested models and their results
- setup for Ollama, LM Studio and llama.cpp
- setup for Claude Code, OpenCode, Codex and Goose
- troubleshooting

## Without an agent

The checker also runs on its own. Get it once, then set up a private Python environment for it:

```bash
git clone https://github.com/nnicholas-c/bib-check-agent ~/bib-check-agent
python3 -m venv ~/.bib-check-venv && source ~/.bib-check-venv/bin/activate
pip install requests rapidfuzz "bibtexparser<2"
```

On Windows PowerShell, use `py -3 -m venv $HOME\.bib-check-venv` and `& $HOME\.bib-check-venv\Scripts\Activate.ps1` instead. In a new terminal, activate the environment again before running the checker.

Then, from your paper's folder:

```bash
python ~/bib-check-agent/skills/bib-check-agent/scripts/verify_bib.py refs.bib      # writes refs.verified.bib and refs.report.md
python ~/bib-check-agent/skills/bib-check-agent/scripts/verify_bib.py refs.bib -i   # review flagged entries one by one
python ~/bib-check-agent/skills/bib-check-agent/scripts/verify_bib.py --bibtex 10.18653/v1/N19-1423 --key devlin2019bert
python ~/bib-check-agent/skills/bib-check-agent/scripts/verify_bib.py --search "Dropout: a simple way to prevent neural networks from overfitting"
```

The checker never changes `refs.bib`. Every run, including `-i`, rewrites `refs.verified.bib` from `refs.bib`, so copy that file somewhere else before you run again. With [uv](https://docs.astral.sh/uv/), `uv run ~/bib-check-agent/skills/bib-check-agent/scripts/verify_bib.py refs.bib` skips the environment setup.

Optional environment variables:
- `S2_API_KEY`: a free Semantic Scholar key, for faster and steadier runs. Without one, Semantic Scholar throttles a long bibliography to roughly one entry every 10 to 25 seconds.
- `OPENALEX_API_KEY`: a free OpenAlex key. OpenAlex now limits unauthenticated use per day, and when it says to come back hours later the checker carries on without it and says so in the report.
- `SERPAPI_KEY`: needed for `--scholar`, which also searches Google Scholar through SerpApi for every entry fewer than two databases confirm (`--scholar-all` searches it for all of them). SerpApi is a paid service with a small free tier.
- `VERIFY_BIB_EMAIL`: sent to Crossref for its faster "polite" pool, and only if you set it.

## Privacy and permissions

**What is sent:**
- Titles, author names, DOIs and arXiv IDs from your `.bib` file go to the scholarly APIs above, and to SerpApi if you run with `--scholar`.
- For a website or software entry that matches no paper, the checker opens the entry's `url` to check that the link works.
- Your email goes only to Crossref, and only if you set `VERIFY_BIB_EMAIL`.
- Your paper's text is never sent.

Lookups are cached in `.verify_bib_cache.json` next to your `.bib`.

**What it is allowed to do.** In Claude Code, the skill pre-approves the tools it needs for the turn you invoke it in, so it can work with few prompts. Claude Code may still ask once before starting the workflow. The pre-approved tools:
- reading, editing and writing files
- web search and fetch
- the Workflow tool
- its own checker script, plus creating its private Python environment
- read-only git commands (`status`, `diff`, `log`, `ls-files`)
- `cp`, for backups
- LaTeX build commands

Review the `allowed-tools` line in `SKILL.md` if you want to narrow it. Organizations that only allow managed permission rules will see prompts instead.

## Limits

- **Cost.** In Claude Code the workflow runs 2 to 3 agents per flagged entry, plus one per 8 confirmed entries; an 11-entry test bibliography cost about $6 at API prices. Local models cost nothing but take longer.
- **Judgment varies a little between runs.** For example, whether a citation "partly" supports a broad claim. Clear-cut cases, such as wrong authors, wrong venues and invented papers, have been stable in testing.
- **Database replacements can drop fields.** When the checker replaces a confirmed entry with the DBLP or Crossref record, fields that record lacks, such as your own `url` or `note`, are lost. It refuses a replacement that would drop a book's publisher, a thesis's school or a paper's venue. Venue names may also get longer.
- **No retraction check yet.**
- Files may come back with Unix line endings.

## Development

```bash
pip install -r skills/bib-check-agent/scripts/requirements.txt
python tests/test_verify_bib.py      # checker logic, offline
python tests/test_skill_files.py     # spec compliance, manifests, rule drift between the two paths
node tests/test_investigate.mjs      # workflow control flow with canned agents, offline
```

`tests/e2e/` holds test papers with planted problems (`fixture/` has 19 entries, `fixture-small/` has 7) and a scorer. Copy a fixture, run the skill on it with any agent, then run `python tests/e2e/score.py <copy>` to check the result against the answer key.
- **MUST checks are safety:** keys preserved, only cite keys changed in `.tex`, a backup exists, the invented paper is flagged.
- **SHOULD checks are quality.**

`tests/benchmark/` measures accuracy against ground truth taken from the DOI and arXiv registries, not from any of the checker's sources. Its paper has 68 entries:
- 38 real works, written correctly
- 18 real works with one planted error each (year, author, venue, title, DOI or arXiv ID)
- 12 invented references, one of them a near miss with a title 89% similar to a real paper's

```bash
python tests/benchmark/make_benchmark.py                   # regenerate paper/ from truth.json
cp -r tests/benchmark/paper ~/bench && cd ~/bench          # then run the skill, or just the checker, there
python tests/benchmark/score_truth.py ~/bench --checker    # score the checker's refs.verified.bib
python tests/benchmark/score_truth.py ~/bench              # score the final refs.bib after the skill ran
```

The headline number is FAULTY REFERENCES, which must be 0. It counts:
- a real work left with a wrong detail, or swapped for another version of it
- a real work called made up
- an invented reference left looking real

An entry flagged for the author isn't faulty, only unfinished.

CI runs the offline tests on Linux, macOS and Windows, and validates the skill with [`skills-ref`](https://github.com/agentskills/agentskills/tree/main/skills-ref) and `claude plugin validate --strict`.

## License

MIT
