# Verify references step by step (any agent)

Follow this when you can't run the Claude Code workflow: in Claude.ai, ChatGPT, Codex, Copilot, Cursor, Gemini CLI, OpenCode, with a local model, or in Claude Code without the Workflow tool. It does the same work as the workflow, one entry at a time.

`SKILL_DIR` is the folder that holds `SKILL.md`. `CHECKER` is the command that ran the checker in step 1 of `SKILL.md`, for example `python3 "SKILL_DIR/scripts/verify_bib.py"` (`python` or `py -3` on Windows). If your shell is PowerShell and CHECKER starts with a quoted path, such as the private environment's `python.exe`, put `& ` in front of it every time. Run the scripts, but don't read their source: it is long, and everything you need is in this file.

## Rules

Follow these exactly. They are why anyone can trust the result.

- A source counts as real only if you saw a record of it yourself: a page you opened (DOI landing page, publisher, arXiv, OpenReview, ACL Anthology, Semantic Scholar, Google Scholar, a library catalog or an author's page), or a database record that `CHECKER --search` or `CHECKER --bibtex` printed. A search-result snippet alone is not enough.
- Take BibTeX only from such a source, never from memory. A reference reconstructed from memory is exactly the error this check exists to catch. Get it with `CHECKER --bibtex <DOI, arXiv ID, or URL of a .bib export> --key <citation key>`, which prints the official export, cleaned and under the right key, and copy its output exactly. When no export exists (books, theses, reports, web pages), write the entry from the fields shown in the record you saw.
- Cite the published version (conference, journal, book) when one exists, not the arXiv preprint. arXiv's own BibTeX gives the year of the latest revision; `CHECKER --bibtex` already corrects that to the first version's year.
- Keep every original citation key exactly, except when merging duplicates.
- Never rewrite the author's sentences. Suggest rewordings in the decisions file instead.
- If you cannot confirm a decision either way, mark the entry UNRESOLVED and say what you tried. Honest partial work beats complete work that guesses.
- Create no files in the user's project except the backups and `FILE.decisions.md`. Research with the checker's commands or your own web tools, not with helper scripts.

## 1. Research each flagged entry

Work through every key the report gives as CHECK, NOT FOUND or UNCHECKED, one at a time. Add each finished decision to `FILE.decisions.md` as you go, so nothing is lost if you stop early.

1. Read the entry in the original .bib file, and its section in the report if it has one. The section lists the problems, the closest real paper, links, and possibly a suggested replacement.
2. Find every sentence that cites the key. Search the .tex files for the key; cite commands vary (`\cite`, `\citep`, `\citet`, `\parencite`, `\textcite`, `\autocite` and others), may have options such as `\citep[see][p.~3]{key}`, and can hold several keys, as in `\citep{a,b}`.
3. Research it. With a web-search tool, search and open pages. Without one, run `CHECKER --search "<exact title>"`, then try title words plus the first author's surname, and the venue name. Try at least three queries before concluding anything does not exist. dblp.org pages and its .bib links sit behind a bot check, so don't rely on them; the checker already queried dblp.
4. Decide exactly one:
   - FIXED: a real paper with wrong details. Use the report's suggested replacement only after confirming it against a record you saw.
   - FOUND: a real source the databases missed, such as a book, thesis, workshop paper, technical report or very new preprint.
   - KEPT: the original entry is right after all. Say why the checker was wrong.
   - SUBSTITUTED: the reference does not exist, but a different real paper clearly supports every sentence citing this key. Confirm that from its abstract, not just its title.
   - FABRICATED: the reference does not exist and nothing suitable supports the sentences. Before choosing this, search by the exact title, by fragments of it, by each author's name, and in the stated venue for that year. This needs a web search. If you only have `CHECKER --search`, mark the entry UNRESOLVED instead, with "likely fabricated" and the queries you ran in the reason: its databases miss some real reports, theses and old books.
   - UNRESOLVED: you could not confirm any of the above.
5. Take a second look before moving on. Reread the evidence and ask what would make the decision wrong. For FABRICATED, search once more by the authors' names. For SUBSTITUTED, check the abstract against each citing sentence again. For FIXED and FOUND, compare every field of the BibTeX with the record: title, each author and their order, year, venue, volume, pages and DOI.
6. Note whether the source supports each citing sentence. If it only partly does, write a suggested rewording.

## 2. Duplicates

For keys the report says are the "same paper as" another key, open the paper's record and confirm they are one work. A preprint and its published version are one work. A conference paper and its extended journal version, or two editions of a book, are not. To merge, keep the key cited most often (on a tie, the one whose entry cites the published version) and retire the others. Don't merge a group if section 1 decided any of its keys FABRICATED, SUBSTITUTED, FOUND or UNRESOLVED; keep the keys apart and say why in the decisions file.

## 3. Citation context for confirmed entries

For each OK or WEB entry, compare the paper's title with the sentences citing it. If it looks unrelated, or the claim is specific (a number, "the first to", a method the title doesn't name), read its abstract (`CHECKER --search "<title>"` prints abstracts). Record a MISMATCH only when the abstract really does not support the claim. Citing a paper for background, for a method it introduced, or for a result it reports is legitimate. For a MISMATCH, suggest a rewording, or a real paper that supports the claim (from a record you saw). Note keys that no .tex file cites.

## 4. Apply

1. Back up every file you will change in place: the .bib, and each .tex file that cites a key you retire. If git holds a file tracked and unchanged (`git ls-files -- "<file>"` lists it and `git status --porcelain -- "<file>"` prints nothing), git is its backup. Otherwise copy it to `<file>.bak`, or to `.bak2`, `.bak3` and so on if that exists. Never overwrite an existing backup.
2. Edit `FILE.verified.bib`:
   - Delete the `% Checked by verify_bib.py` header lines at the top.
   - FIXED, FOUND, SUBSTITUTED: replace the whole entry, including the `% [verify_bib]` lines above it, with the new BibTeX under the original key.
   - KEPT: delete the `% [verify_bib]` lines above the entry.
   - FABRICATED: replace the `% [verify_bib]` lines with `% FABRICATED: no real source found, see FILE.decisions.md`, and leave the entry so the paper still compiles.
   - UNRESOLVED: keep the entry and its `% [verify_bib]` lines, and add `% UNRESOLVED: <one-line reason>, see FILE.decisions.md` above them.
   - Retired duplicate keys: delete their entries. In the .tex files, replace each retired key with the kept key inside cite commands only, matching whole keys (retiring `smith2020` must not touch `smith2020a`), and if a command then names the kept key twice, keep one. Citation keys are the only text you may change in .tex files.
   - Replace any `% FABRICATED` or `% UNRESOLVED` line left by an earlier run instead of adding a second one.
   - Don't add new entries. A suggested substitute for a MISMATCH or FABRICATED citation goes in the decisions file, with its BibTeX, for the author to adopt.
3. Copy `FILE.verified.bib` over the original .bib.
4. Check for LaTeX by running `latexmk -v` (or `pdflatex --version`). If it is installed, compile the paper (for example `latexmk -pdf main.tex`) and check the log for undefined citations or BibTeX errors that your edits caused. If the command is not found, say that the paper was not compiled.
5. Run `CHECKER FILE.bib` once more. Everything should be OK or WEB except FABRICATED, UNRESOLVED and KEPT entries, which it flags again by design, and FOUND sources the databases don't cover. If it flags an entry you wrote, compare it with your evidence and fix any copying slip.

## 5. Decisions file and report

`FILE.decisions.md` starts with a "Needs your attention" section: FABRICATED entries, UNRESOLVED entries with what you tried, and MISMATCH citations with each citing sentence and the suggested rewording or substitute. Then give one line per entry you changed: key, decision, a one-sentence reason and the evidence URL or record. End with merges, groups kept apart, uncited keys, the backups and the final checker counts. If the file exists from an earlier run, put this run at the top under a new heading.

Then tell the user what needs their attention first, give counts for each decision, and say where the backups and the decisions file are.
