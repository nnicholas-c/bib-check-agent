---
name: bib-check-agent
description: Checks every entry in a LaTeX .bib file against Semantic Scholar, DBLP, Crossref, arXiv, OpenAlex, Europe PMC and other open databases, and Google Scholar with the user's help, then researches and fixes the problems, with every fix independently re-checked. Catches hallucinated or made-up citations, wrong authors, years or venues, broken DOIs or arXiv IDs, duplicate entries, and citations that don't support the sentence citing them. Use it whenever the user wants to check, verify, validate, audit, fix or clean up references, citations, a bibliography or a .bib file, or worries that citations might be fake, even if they don't name this skill.
license: MIT
compatibility: Needs Python 3 with the packages in scripts/requirements.txt, and outbound HTTPS to api.semanticscholar.org, api.crossref.org, export.arxiv.org, arxiv.org, doi.org, data.crosscite.org and sparql.dblp.org; OpenAlex, Europe PMC, DataCite, Open Library, OpenReview and CORE are used when reachable. Google Scholar needs a browser the user can see. The parallel workflow needs Claude Code; any other agent follows references/procedure.md.
metadata:
  version: "1.3.0"
allowed-tools: 'Read Edit Write Glob Grep WebSearch WebFetch Workflow Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/verify_bib.py" *) Bash(python "${CLAUDE_SKILL_DIR}/scripts/verify_bib.py" *) Bash(py -3 "${CLAUDE_SKILL_DIR}/scripts/verify_bib.py" *) Bash(uv run "${CLAUDE_SKILL_DIR}/scripts/verify_bib.py" *) Bash("${CLAUDE_SKILL_DIR}/.venv/bin/python" "${CLAUDE_SKILL_DIR}/scripts/verify_bib.py" *) Bash("${CLAUDE_SKILL_DIR}/.venv/Scripts/python.exe" "${CLAUDE_SKILL_DIR}/scripts/verify_bib.py" *) Bash(python3 -m venv "${CLAUDE_SKILL_DIR}/.venv") Bash(python -m venv "${CLAUDE_SKILL_DIR}/.venv") Bash(py -3 -m venv "${CLAUDE_SKILL_DIR}/.venv") Bash("${CLAUDE_SKILL_DIR}/.venv/bin/python" -m pip install -r "${CLAUDE_SKILL_DIR}/scripts/requirements.txt") Bash("${CLAUDE_SKILL_DIR}/.venv/Scripts/python.exe" -m pip install -r "${CLAUDE_SKILL_DIR}/scripts/requirements.txt") Bash(git status *) Bash(git diff *) Bash(git log *) Bash(git ls-files *) Bash(cp *) Bash(latexmk *) Bash(pdflatex *) Bash(xelatex *) Bash(lualatex *) Bash(bibtex *) Bash(biber *)'
---

# Bib Check Agent

Check and fix a LaTeX bibliography so that every reference is real, correct, and supports the sentence that cites it. You own the task from start to finish: make the judgment calls yourself, keep going without check-ins, and report at the end. Every decision has to rest on evidence someone actually opened. ultrathink before each judgment call.

**Paths.** The skill folder is `${CLAUDE_SKILL_DIR}`. If that still shows literally as `${CLAUDE_SKILL_DIR}` instead of a path, your agent doesn't fill it in. Use the absolute path of the folder that holds this SKILL.md everywhere below instead.

**Commands.** In Claude Code, run shell commands with the Bash tool, and write them exactly as shown, with the script path in double quotes. This skill's pre-approved commands match only that form. If there is no Bash tool (Windows without Git for Windows), use PowerShell and put `& ` in front of a command that starts with a quoted path; the user will then be asked to approve each command.

## 1. Run the checker

The .bib file the user named, if any: $ARGUMENTS

If that is blank or shows literally as `$ARGUMENTS`, take the file from the user's request. File names the user gives refer to their project, which is your current working directory, never to the skill folder. Find the file there, for example by searching the working directory for `*.bib`. If they named none, find the .bib files that the main .tex file uses, through `\bibliography{...}` or `\addbibresource{...}`, and check each one. Only ask the user if no .bib file exists anywhere in the working directory.

From the folder that holds the .bib file, run the checker, allowing it up to 10 minutes:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/verify_bib.py" FILE.bib
```

- If `python3` isn't found, or on Windows opens the Microsoft Store, use `python` in its place, then `py -3`.
- If it reports missing packages, make a private environment inside the skill folder so nothing global changes. Run `python3 -m venv "${CLAUDE_SKILL_DIR}/.venv"`, then `"${CLAUDE_SKILL_DIR}/.venv/bin/python" -m pip install -r "${CLAUDE_SKILL_DIR}/scripts/requirements.txt"`, then run the checker with `"${CLAUDE_SKILL_DIR}/.venv/bin/python"` in place of `python3`. On Windows, the environment's Python is `"${CLAUDE_SKILL_DIR}/.venv/Scripts/python.exe"`. Outside Claude Code, a Windows agent may run commands in PowerShell, which needs `& ` in front of a command that starts with a quoted path.
- If creating the environment fails (on Debian and Ubuntu, including WSL, it needs the python3-venv package), check whether `uv` is installed. If it is, run the checker as `uv run "${CLAUDE_SKILL_DIR}/scripts/verify_bib.py" FILE.bib` from then on; uv installs the packages itself. If it isn't, stop and ask the user to install python3-venv or uv. Never use sudo, `--break-system-packages` or a global pip install.
- If it stops with "Cannot reach the paper databases", this environment has no internet access to them. Stop, and tell the user which hosts to allow; the message lists them. Never judge any entry without the check, and never call one made up because a lookup failed.
- If it stops with "is not UTF-8", tell the user and stop. Don't convert the file yourself, because the paper's LaTeX setup may depend on its encoding.
- Don't pass `--email`; the checker reads `VERIFY_BIB_EMAIL` if the user set it. Never pass `-i`, which waits for keyboard input.
- A first run takes minutes because the databases are rate limited. Lookups are cached in `.verify_bib_cache.json`, which makes reruns fast. Add that file to `.gitignore` if the project uses git.

The checker writes `FILE.verified.bib` and `FILE.report.md`. In the verified copy, confirmed entries already have clean BibTeX, and flagged entries keep their original text under `% [verify_bib]` comment lines. The report's "All entries" table gives each key's status:

- OK: confirmed by at least two independent databases that agree with each other. The report's "Confirmed by" column names them.
- WEB: a website or software entry whose link was checked.
- CHECK: a real paper with a wrong detail, or an entry only one database confirms.
- NOT FOUND: no database had it.
- UNCHECKED: the entry could not be parsed.

A note saying "same paper as ..." marks duplicates.

## 2. Research and fix

Choose one path:

- **Claude Code with the Workflow tool:** use the workflow in section 3. It researches entries in parallel and has independent reviewers check every decision.
- **Anything else:** read `references/procedure.md` in the skill folder and follow it. That covers Claude Code without the Workflow tool, Claude.ai, Codex or ChatGPT, Copilot, Cursor, Gemini CLI, OpenCode, Goose, any local model, or a user who declined the workflow. It does the same work one entry at a time.

## 3. The Claude Code workflow

### Google Scholar, with the user's help

Google Scholar covers more than any database the checker can reach, including many books, theses and new papers. It has no API and shows a CAPTCHA to automated searches, so search it in a browser the user can also see, and let the user solve every CAPTCHA. Search every entry, one at a time in the same tab.

1. The entries to search are those in the report's "Google Scholar links" section: every entry with a title except websites and software, which the checker checked through their own link.
2. Look for a browser the user can see: the Claude desktop app's browser (tools named `mcp__Claude_Browser__...`) or Claude in Chrome (`mcp__claude-in-chrome__...`). Load them with ToolSearch if they are deferred. This skill doesn't pre-approve them, so Claude Code asks the user before you browse; that keeps the browser to this step, away from the workflow's agents. If neither is available, skip this part; the workflow's agents try Scholar themselves.
3. For each entry, open its link from that section, which searches for the exact title, and read the start of the page: in the desktop app's browser call get_page_text with max_chars 2000; in Claude in Chrome, use find for "did not match any articles" and for the first result's title rather than reading the whole page. Record for that key the link and what you saw: the matching result's title line, with any [PDF], [HTML], [BOOK] or [CITATION] marker in front of it, and its authors, venue and year line, copied exactly. Or record "no match" when Scholar says it "did not match any articles" or lists only other works.
4. If you get a CAPTCHA or block page instead (an address on google.com/sorry, or text such as "unusual traffic", "not a robot" or "automated queries"), stop. Never solve, click through or try to get around a CAPTCHA yourself. Ask the user with the AskUserQuestion tool: "Google Scholar is showing a CAPTCHA in the browser. Please solve it there, then choose Done." Offer the options "Done" and "Skip Google Scholar". After Done, reload the same search and carry on, and ask again each time a CAPTCHA appears. If Scholar refuses without a CAPTCHA to solve (for example, it says to try again later), say so in the same question, and retry after Done. Stop only when the user chooses to skip. Then leave the remaining entries out of what you recorded, and set `"scholar_skipped": true` in the workflow input, so its agents don't go back to Google Scholar either.
5. For an OK entry, if Scholar shows no match, a different first author, or a year two or more years off, move its key from `ok` to `flagged` in the workflow input below, so it gets researched.
6. Ask the user only through AskUserQuestion, and don't end your turn before the workflow starts. A question answered this way keeps this skill's approvals for the workflow, but a new chat message from the user would end them.

### Start the workflow

Build the workflow's input from the report, using absolute paths with forward slashes:

```json
{
  "project": "folder holding the main .tex file",
  "bib": "FILE.bib",
  "verified": "FILE.verified.bib",
  "report": "FILE.report.md",
  "checker": "the checker command from step 1, without its arguments, e.g. python3 \"${CLAUDE_SKILL_DIR}/scripts/verify_bib.py\"",
  "flagged": ["every key whose status is CHECK, NOT FOUND or UNCHECKED"],
  "ok": ["every key whose status is OK or WEB"],
  "duplicates": [["keyA", "keyB"]],
  "scholar": {"keyA": "what you recorded in Google Scholar for it"}
}
```

Copy the keys exactly. Every key in the table belongs in `flagged` or `ok`. List each duplicate group once, with all its keys. Leave `scholar` out if you didn't search Google Scholar.

Call the Workflow tool with `scriptPath` set to `${CLAUDE_SKILL_DIR}/workflows/investigate.js` and `args` set to that object, passed as a JSON object rather than a string. With several .bib files, run the checker on each, then call the Workflow tool once per file in this same turn, before any completion notice arrives; a workflow started after a notice doesn't get this skill's approvals. In the background, the workflow:

- Sends one agent to research each flagged entry and decide FIXED, FOUND, KEPT, SUBSTITUTED or FABRICATED, fetching official BibTeX with the checker's `--bibtex` mode so nothing is typed from memory. Each agent gets what you recorded in Google Scholar, and tries Scholar itself for an entry you didn't search, stopping at any CAPTCHA.
- Decides, for each duplicate group, whether the keys really are the same work.
- Checks, in batches, that each confirmed paper supports the sentences citing it.
- Has an independent reviewer reopen the evidence for every decision and try to refute it. A FABRICATED verdict gets two reviewers who each try to find the work. A refuted decision gets one more round, and if that is refuted too, the entry comes back UNRESOLVED.
- Applies the results to the .bib and .tex files after backing up anything git can't restore, compiles the paper if LaTeX is installed, reruns the checker, writes `FILE.decisions.md`, then audits its own edits and fixes what the audit finds.

The workflow's agents carry this skill's pre-approved tools, but your own approvals end when the completion notice arrives, so leave the file changes to the workflow. Wait for every completion notification. Don't research entries yourself while a workflow runs, and never guess at its results.

When a notification arrives, read the result's first fields: `counts`, `status`, `apply_error`, `audit2`, `audit` and `missing`. They come first because the notice may cut a long result short. Then read `FILE.decisions.md` with the Read tool, which has everything else. Don't run scripts to parse the result file, since they need approval. Act on `status`:

- `applied and audited`: go on to the summary.
- `applied, but the audit still lists problems`: fix the problems listed in `audit2`, or in `audit` if there's no `audit2`, following `APPLY_RULES` in `workflows/investigate.js`. Report any that need the author. The user will be asked to approve your edits.
- `applied but not fully audited: ...`: audit the files yourself as `auditPrompt` describes, then fix what you find the same way.
- `not applied: ...`: the apply step never reported back and may have edited some files before stopping. Compare the .bib and .tex files with their `.bak` copies or `git diff` first, then finish applying the results yourself, following `applyPrompt` and `APPLY_RULES`.

If `missing` lists keys, no agent covered them, and the apply step left them marked UNRESOLVED. Tell the user to run this skill on that file again in a new message, which checks them afresh. Don't start a workflow yourself from this turn, because its agents would lack this skill's approvals.

## 4. Summary for the user

Start with what needs their attention: FABRICATED entries, SUBSTITUTED entries (each now cites a different paper from the one the user wrote), UNRESOLVED entries with both sides' arguments, and MISMATCH citations with the suggested rewording or substitute. For a FABRICATED or UNRESOLVED entry that nobody could search in Google Scholar, give its Scholar link from the report so the user can check it. Then give counts for each decision, and say where the backup, the git diff and the decisions file are.
