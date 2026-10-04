export const meta = {
  name: 'bib-check-agent',
  description: 'Research flagged .bib entries, check that citations support their sentences, and have independent reviewers try to refute every decision',
  whenToUse: 'Run by the bib-check-agent skill after verify_bib.py has written its report',
  phases: [
    { title: 'Investigate', detail: 'one agent per flagged entry and per duplicate group' },
    { title: 'Context', detail: 'batches of confirmed entries: does each paper support the sentence citing it?' },
    { title: 'Verify', detail: 'independent reviewers re-open the evidence and try to refute each decision' },
    { title: 'Repair', detail: 'one more attempt for each decision a reviewer refuted' },
    { title: 'Apply', detail: 'one agent edits the files, a second audits them, a third fixes what the audit finds' },
  ],
}

// args (built by SKILL.md step 2):
//   project, bib, report, verified, checker  strings (absolute paths; checker is the command prefix for verify_bib.py)
//   flagged     keys whose status is CHECK, NOT FOUND or UNCHECKED
//   ok          keys whose status is OK or WEB
//   duplicates  groups of keys the report says are the same paper, e.g. [["a", "b"]]
//   apply       false to research only and leave every file alone (default: apply)
const A = args || {}
for (const k of ['project', 'bib', 'report', 'verified', 'checker']) {
  if (typeof A[k] !== 'string' || !A[k]) throw new Error(`args.${k} is required (see SKILL.md step 2)`)
}
const keyList = name => {
  const v = A[name] == null ? [] : A[name]
  if (!Array.isArray(v)) throw new Error(`args.${name} must be an array, not ${typeof v}`)
  return v
}
const flagged = keyList('flagged')
const ok = keyList('ok')
const groups = keyList('duplicates').filter(g => Array.isArray(g) && g.length > 1)
const BATCH = 8 // confirmed entries per context-check agent
const DECISIONS = A.bib.replace(/\.bib$/i, '') + '.decisions.md'
const DECISIONS_NAME = DECISIONS.split(/[\\/]/).pop()
const BIB_DIR = A.bib.replace(/[\\/][^\\/]*$/, '')

// ---------------------------------------------------------------------------------------
// Shared prompt text
// ---------------------------------------------------------------------------------------

const FILES = `Files. Read them, but do not edit, create, move or delete any file: you only report back, and the main session applies the result.
- Original bibliography: ${A.bib}
- Checked copy, where confirmed entries already have clean BibTeX: ${A.verified}
- Checker report: ${A.report}
- LaTeX sources: the .tex files under ${A.project}`

const CITES = `To find where a key is cited, Grep the .tex files for the key, then read the surrounding text. Cite commands vary (\\cite, \\citep, \\citet, \\citeauthor, \\parencite, \\textcite, \\autocite, \\footcite, \\nocite and others) and can hold several keys, as in \\citep{a,b}. Record each citing sentence in full.`

const EVIDENCE = `Evidence rules. Follow them exactly; they are why anyone can trust the result.
- A source counts as real only if you opened a page that shows it: a DOI landing page, publisher page, arXiv abstract page, OpenReview, ACL Anthology, Semantic Scholar, Google Scholar, a library catalog, or an author's page. A search-result snippet alone is not enough. dblp.org pages currently show a bot check; for structured lookups use the Semantic Scholar API (https://api.semanticscholar.org/graph/v1/paper/search?query=...&fields=title,authors,year,venue,externalIds,abstract) or Crossref (https://api.crossref.org/works?query.bibliographic=...). Semantic Scholar rate-limits; on HTTP 429 wait a few seconds and retry, or use another source.
- Take BibTeX only from a source you opened, never from memory. A reference reconstructed from memory is exactly the error this check exists to catch. Get it with the checker, using the Bash tool; it prints the official export, cleaned and under the key you give:
    ${A.checker} --bibtex <DOI, arXiv ID, or URL of a .bib export> --key <citation key>
  It accepts any DOI, any arXiv ID, and .bib export URLs such as https://aclanthology.org/N19-1423.bib. When no export exists (books, theses, reports, web pages), write the entry from the fields shown on the page you opened, and say so in bibtex_source.
- Without a working web-search tool, research with the checker instead: ${A.checker} --search "<title, or title words plus an author's surname>" queries OpenAlex, Semantic Scholar, Crossref and arXiv and prints candidates with abstracts. A record it prints counts as a source you opened. Try several queries before concluding a work does not exist.
- Cite the published version (conference, journal, book) when one exists, not the arXiv preprint. arXiv's own BibTeX (arxiv.org/bibtex/...) gives the year of the latest revision, often years after publication; the checker's --bibtex corrects that to the first version's year, but fetching the page yourself does not.
- Keep the original citation key exactly.
- Use every tool this session offers that helps: WebSearch and WebFetch, and, if ToolSearch shows them, a browser for Google Scholar or publisher pages, a PDF reader, or a reference manager such as Zotero. If a tool is denied, carry on with the others.`

// ---------------------------------------------------------------------------------------
// Schemas
// ---------------------------------------------------------------------------------------

const str = description => ({ type: 'string', description })
const strs = description => ({ type: 'array', items: { type: 'string' }, description })

const DECISION = {
  type: 'object',
  properties: {
    key: str('The citation key, unchanged.'),
    decision: { type: 'string', enum: ['FIXED', 'FOUND', 'KEPT', 'SUBSTITUTED', 'FABRICATED', 'UNRESOLVED'] },
    reason: str('One sentence a researcher could check.'),
    evidence_urls: strs('Pages you opened that show the source. Empty only for FABRICATED.'),
    bibtex: str('The complete replacement entry under the original key for FIXED, FOUND and SUBSTITUTED. Empty string for KEPT and FABRICATED.'),
    bibtex_source: str('The checker command or URL the BibTeX came from, or "written from <url>". Empty when bibtex is empty.'),
    citing_sentences: strs('Every sentence in the .tex files that cites this key.'),
    supports_claim: { type: 'string', enum: ['yes', 'partly', 'no', 'uncited'], description: 'Does the source you settled on support the citing sentences? For FABRICATED, "no".' },
    rewording: str('A suggested rewording for a citing sentence the source does not fully support. Empty otherwise.'),
    searched: str('For FABRICATED: where you searched, and the closest real papers you rejected and why. Empty otherwise.'),
  },
  required: ['key', 'decision', 'reason', 'evidence_urls', 'bibtex', 'bibtex_source', 'citing_sentences', 'supports_claim', 'rewording', 'searched'],
}

const VERDICT = {
  type: 'object',
  properties: {
    upheld: { type: 'boolean' },
    problems: strs('Each specific thing that is wrong or could not be confirmed, with the URL that shows it. Empty if upheld.'),
    evidence_urls: strs('Pages you opened.'),
  },
  required: ['upheld', 'problems', 'evidence_urls'],
}

const FIND = {
  type: 'object',
  properties: {
    found: { type: 'boolean' },
    title: str('Title of the work you found. Empty if none.'),
    url: str('A page you opened that shows it. Empty if none.'),
    note: str('Why it is plausibly the cited work, or where you searched without success.'),
  },
  required: ['found', 'title', 'url', 'note'],
}

const DUP = {
  type: 'object',
  properties: {
    decision: { type: 'string', enum: ['MERGED', 'KEEP_BOTH'] },
    survivor: str('For MERGED: the key to keep. Empty for KEEP_BOTH.'),
    retire: strs('For MERGED: every other key in the group. Empty for KEEP_BOTH.'),
    reason: str('One sentence.'),
    evidence_urls: strs('Pages you opened.'),
  },
  required: ['decision', 'survivor', 'retire', 'reason', 'evidence_urls'],
}

const CONTEXT = {
  type: 'object',
  properties: {
    items: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          key: str('The citation key.'),
          verdict: { type: 'string', enum: ['SUPPORTS', 'MISMATCH', 'UNCITED'] },
          citing_sentences: strs('Every sentence that cites this key.'),
          reason: str('One sentence.'),
          evidence_url: str('The abstract or page you read, if you opened one.'),
          suggestion: str('For MISMATCH: a rewording, or a real paper that does support the claim with a URL you opened. Empty otherwise.'),
        },
        required: ['key', 'verdict', 'citing_sentences', 'reason', 'evidence_url', 'suggestion'],
      },
    },
  },
  required: ['items'],
}

// ---------------------------------------------------------------------------------------
// Prompts
// ---------------------------------------------------------------------------------------

function investigatePrompt(key, objection) {
  return `You are checking one entry of a LaTeX bibliography. An automated checker flagged it, and your decision will be applied to the paper, so base it only on evidence you open yourself.

Entry key: ${key}

${FILES}

1. Read the entry for "${key}" in the original bibliography, and its section in the checker report if it has one (headed "### CHECK: \`${key}\`" or "### NOT FOUND: \`${key}\`"). The section lists the problems found, the closest real paper, links, and possibly a suggested replacement.
2. ${CITES} If the key is never cited, note that and still decide.
3. Research the reference until you can make exactly one of these decisions:
   - FIXED: a real paper with wrong details in the entry. Use the checker's suggested replacement only after confirming it against a page you open, and fetch a better export when one exists.
   - FOUND: a real source the databases missed, such as a book, thesis, workshop paper, technical report, or very new preprint. Locate it and write the entry from it.
   - KEPT: the original entry is right after all and the checker was wrong. Say exactly why.
   - SUBSTITUTED: the reference does not exist, but a different real paper clearly supports every sentence that cites this key. Confirm that by reading its abstract or the relevant section, not just its title. The new entry goes under the same key, so the .tex files need no edits.
   - FABRICATED: the reference does not exist and nothing suitable supports the sentences. Before choosing this, search hard: the exact title, fragments of it, each author's publication list, and the stated venue's proceedings for that year. Record where you looked in "searched". This needs a web search: if you only had the checker's --search, choose UNRESOLVED instead, with "likely fabricated" in the reason.
   - UNRESOLVED: you could not confirm any of the above. The entry is left as it is, flagged for the author.
4. Judge whether the source you settled on supports each citing sentence (supports_claim). If it only partly does, or does not, suggest a rewording. Never edit the sentences yourself.

${EVIDENCE}
${objection ? `
An independent reviewer disputed an earlier decision on this entry:
${objection}
Re-examine from scratch. Accept the objection only where the evidence you open supports it.
` : ''}
Return your decision.`
}

const CHECKS = {
  FIXED: `- The original entry really refers to this paper: the same work, not merely a similar title.`,
  FOUND: `- The source really is the work the original entry meant to cite.`,
  SUBSTITUTED: `- The original reference really does not exist: search for its exact title and its authors yourself.
- Read the substitute's abstract, or the relevant section, and confirm it clearly supports every citing sentence listed. A related topic is not enough.`,
  KEPT: `- Open a page showing the real paper and confirm every field of the ORIGINAL entry (read it from the original bibliography) matches it, so the checker's complaint in the report is wrong.`,
}

function verifyPrompt(d) {
  const fields = d.decision === 'KEPT' ? '' : `- Compare every field of the proposed BibTeX with the source page: title, each author's name, spelling and order, year, venue, volume, number, pages, publisher and DOI. The entry type fits the source (inproceedings, article, book, phdthesis, techreport, or misc for a preprint or web page), and it cites the published version if one exists.
- The key in the BibTeX is exactly "${d.key}".
`
  return `An investigator reached the decision below about one entry of a LaTeX bibliography. You are an independent reviewer: try to refute it. Open the evidence yourself and look for anything wrong.

${FILES}

Decision under review:
${JSON.stringify(d, null, 2)}

Check:
${CHECKS[d.decision]}
${fields}- Every evidence URL really shows what the investigator says. Open them.
- The supports_claim judgment and any rewording are fair, given what the source says.

${EVIDENCE}

Set upheld to true only if you confirmed all of this with pages you opened. If you cannot confirm something, set upheld to false and say what. Formatting choices such as braces, field order, or abbreviated versus full venue names are not problems; wrong or missing authors, or a wrong title, year, venue, volume, pages or DOI, are.`
}

const HUNTS = [
  { name: 'title', how: `Search by title: the exact title in quotes, then distinctive fragments and likely variants (reordered words, a changed subtitle, different spelling). Use WebSearch, Google Scholar, the Semantic Scholar and Crossref APIs, arXiv search (https://export.arxiv.org/api/query?search_query=ti:...), OpenReview and the ACL Anthology.` },
  { name: 'authors', how: `Search by people and venue: look up each listed author's publication list (Semantic Scholar author search, Google Scholar profiles, homepages) for any work resembling this one, then browse the stated venue's proceedings or table of contents for the stated year and the years around it.` },
]

function huntPrompt(d, hunt) {
  return `An investigator concluded that a bibliography entry is FABRICATED, meaning the cited work does not exist. You are an independent reviewer. Try to prove them wrong by finding the work. Do not edit any file.

Entry key: ${d.key}. Read the entry in ${A.bib}; the checker's notes on it are in ${A.report}.
Where the investigator searched: ${d.searched || '(not stated)'}

Your search strategy: ${hunt.how}

Set found to true only if you opened a page showing a real work that is plausibly the one this entry meant to cite (the same work, possibly with garbled details), and give that page's URL. A different paper on a similar topic is not a find.`
}

function dupPrompt(keys) {
  return `The checker matched these citation keys to the same paper: ${keys.join(', ')}. Decide whether they really are the same work.

${FILES}

Read each entry in the original bibliography and its row in the report's "All entries" table, then open the paper's page to confirm.
- MERGED: they are the same work. A preprint and its published version count as the same work. Keep as survivor the key cited most often in the .tex files (on a tie, the one whose entry cites the published version, then the first listed), and list every other key in "retire".
- KEEP_BOTH: they are distinct works, for example a conference paper and its extended journal version, or two editions of a book.

${CITES}

Return your decision.`
}

function verifyDupPrompt(keys, g) {
  return `A reviewer decided that the citation keys ${keys.join(', ')} refer to the same work and should be merged: keep "${g.survivor}", retire ${g.retire.map(k => `"${k}"`).join(', ')}. Reason given: ${g.reason}

You are an independent reviewer: try to refute this. Read each entry in ${A.bib}, open the pages for the works, and check whether they really are one work. A preprint and its published version count as one work; a conference paper and its extended journal version, or two editions of a book, do not. Do not edit any file.

Set upheld to true if they are the same work.`
}

function contextPrompt(keys) {
  return `An automated checker confirmed that these bibliography entries are real papers: ${keys.join(', ')}. Check that each one supports the sentences that cite it.

${FILES}

For each key:
1. Read its entry in the checked copy. ${CITES}
2. Compare the paper (title, venue, year) with what each sentence claims. If they plainly fit, the verdict is SUPPORTS. If the paper looks unrelated, or the claim is specific (a number, "the first to", a method the title doesn't name), open its abstract (Semantic Scholar, arXiv or the DOI page) before judging.
3. Use MISMATCH only when, after reading the abstract or the relevant section, the paper really does not support the claim. Give the reason and the URL you read, and in "suggestion" propose a rewording, or a real paper that does support the claim with a URL you opened, never from memory.
4. Use UNCITED if no .tex file cites it.

${EVIDENCE}

Return one item for every key listed.`
}

function mismatchPrompt(item) {
  return `A reviewer flagged a citation as a MISMATCH: the cited paper does not seem to support the sentence citing it. You are an independent second reviewer. Do not edit any file.

Key: ${item.key}. Its entry is in ${A.verified}.
Citing sentences:
${item.citing_sentences.map(s => '- ' + s).join('\n')}
First reviewer's reason: ${item.reason}

Open the paper (its abstract, and the relevant section if needed) and look for support for each sentence. Citing a paper for background, for a method it introduced, or for a result it reports are all legitimate uses.

Set upheld to true if the paper really does not support the sentence, false if it does. If you cannot tell, set upheld to true and explain, so the author takes a look.`
}

const APPLIED = {
  type: 'object',
  properties: {
    backup: str('Every file you changed in place and where its original is: "<file> -> <backup path>" or "<file> -> git", separated by semicolons.'),
    changed_keys: strs('Keys whose entries you changed or deleted.'),
    tex_files_changed: strs('The .tex files you edited.'),
    compile: str('"ok", "no LaTeX installed", or the errors that remain.'),
    rerun: str('The counts line the final checker run printed.'),
    rerun_flags: strs('Each entry the final run still flags, and why that is expected or what you fixed.'),
    decisions_file: str('Path of the decisions file.'),
    problems: strs('Anything you could not do, and why.'),
  },
  required: ['backup', 'changed_keys', 'tex_files_changed', 'compile', 'rerun', 'rerun_flags', 'decisions_file', 'problems'],
}

const resultsOnly = r => JSON.stringify({ ...r, applied: undefined, audit: undefined, fixup: undefined, audit2: undefined, apply_error: undefined }, null, 1)

// git commands in this exact form match the skill's pre-approved Bash(git status *) etc.; "git -C" would not.
const GIT_FORM = `Run git with the Bash tool exactly as git status --porcelain -- "<file>", git ls-files -- "<file>" and git diff -- "<file>", without cd or -C. If git reports no repository, or the command is denied, treat the file as not held by git.`

const APPLY_RULES = `- Delete the "% Checked by verify_bib.py" header lines at the top of the checked copy; their counts are out of date.
- decisions with FIXED, FOUND or SUBSTITUTED: replace the whole entry, including the "% [verify_bib]" lines above it, with the decision's bibtex, copied exactly. If its key differs from the original, change the key and nothing else.
- decisions with KEPT: delete the "% [verify_bib]" lines above the entry.
- decisions with FABRICATED: replace the "% [verify_bib]" lines with "% FABRICATED: no real source found, see ${DECISIONS_NAME}". Leave the entry in place so the paper still compiles.
- unresolved, and missing items whose job is "investigate": keep the entry and its "% [verify_bib]" lines, and add "% UNRESOLVED: <one-line reason>, see ${DECISIONS_NAME}" above them.
- An entry may still carry a "% FABRICATED" or "% UNRESOLVED" line from an earlier run. Replace it rather than adding a second one, and delete it when the entry is now FIXED, FOUND, SUBSTITUTED or KEPT.
- merges: delete each retired key's entry. In the .tex files, replace each retired key with the survivor inside cite commands only, matching whole keys, and if a command then names the survivor twice, keep one. Citation keys are the only text you may change in .tex files.
- Don't add new entries. A suggested substitute for a MISMATCH or FABRICATED citation goes in the decisions file, with its BibTeX, for the author to adopt.
- Change nothing else, and never rewrite the author's sentences.`

function applyPrompt(r) {
  return `You are applying the results of a bibliography check to a LaTeX project. Each decision below was researched and then independently reviewed, so apply them exactly; do not research them again or second-guess them. Use the Bash tool for shell commands.

Files:
- Original bibliography, to be replaced: ${A.bib}
- Checked copy to edit: ${A.verified}. Confirmed entries already have clean BibTeX. Flagged entries keep their original text under "% [verify_bib]" comment lines.
- LaTeX sources: the .tex files under ${A.project}
- Decisions file to write: ${DECISIONS}

Results:
${resultsOnly(r)}

1. Before editing anything, back up every file you will change in place: ${A.bib}, and each .tex file that cites a key a merge retires. ${GIT_FORM} If git holds the file tracked and unchanged (ls-files lists it and status prints nothing), git is its backup. Otherwise copy it with cp to <file>.bak. If that already exists, it holds an earlier original, so never overwrite it: use <file>.bak2, .bak3 and so on.
2. Edit the checked copy, and the .tex files for merges:
${APPLY_RULES}
3. Copy the edited checked copy over ${A.bib} with cp.
4. Check for LaTeX by running latexmk -v (or pdflatex --version). If it is installed, compile the main .tex file from its folder (for example latexmk -pdf main.tex) and check the log for undefined citations and BibTeX errors. Fix any that your edits caused. If the command is not found, say that no LaTeX is installed.
5. Run the checker on the replaced file: ${A.checker} "${A.bib}". This rewrites the checked copy and the report, which is expected. Every entry should come back OK or WEB, except FABRICATED, UNRESOLVED and KEPT entries, which it flags again by design, and FOUND sources the databases don't cover. If it flags an entry you wrote, compare that entry with the decision's bibtex and fix any copying slip in ${A.bib}. Don't change an entry just to satisfy the checker.
6. Write the decisions file. If it already exists from an earlier run, put this run's results at the top under a new heading and keep the earlier runs below. Start with a "Needs your attention" section listing FABRICATED entries, UNRESOLVED entries with both sides' arguments, MISMATCH citations with each citing sentence and the suggested rewording or substitute, and any missing items (keys no agent covered, by job). Then give one line per entry you changed: key, decision, a one-sentence reason, and the evidence URL. End with merges, groups kept apart and why, uncited keys, the backups, and the final checker counts.

Return what you did.`
}

function auditPrompt(r) {
  return `Another agent applied the results of a bibliography check to a LaTeX project. Audit its work against the results it was given. Do not edit any file. Use the Bash tool for shell commands.

Results it was given:
${resultsOnly(r)}

What it reported:
${JSON.stringify(r.fixup ? { applied: r.applied, fixup: r.fixup } : r.applied, null, 1)}

Files: the final bibliography is ${A.bib}, the .tex files are under ${A.project}, and the decisions file is ${DECISIONS}. The originals are here: ${(r.fixup || r.applied).backup}. For a file whose original is in git, compare with git diff. ${GIT_FORM}

Check each of these, and list every failure in problems:
- Each FIXED, FOUND or SUBSTITUTED entry in the final bibliography matches its decision's bibtex field for field, under the original key.
- Each FABRICATED entry is still present, with one "% FABRICATED" line above it.
- Each UNRESOLVED entry, and each missing "investigate" key, still has its original text.
- No "% [verify_bib]" lines remain except above UNRESOLVED entries. Entries the report marked OK may have been replaced with clean BibTeX by the checker, which is expected.
- Each retired key's entry is gone, and no .tex file still cites a retired key.
- Compared with the originals, the .tex files differ only in citation keys inside cite commands.
- The decisions file exists and lists every FABRICATED, UNRESOLVED and MISMATCH item.

Set upheld to true only if everything checks out.`
}

function fixPrompt(r) {
  return `An audit found problems in how the results of a bibliography check were applied to a LaTeX project. Fix exactly these problems and nothing else. Use the Bash tool for shell commands.

Problems:
${r.audit.problems.map(p => '- ' + p).join('\n')}

Results that were being applied:
${resultsOnly(r)}

The rules they follow:
${APPLY_RULES}

Files: the final bibliography is ${A.bib}, the .tex files are under ${A.project}, and the decisions file is ${DECISIONS}. The originals are here: ${r.applied.backup}. Don't back those files up again. Before changing any other file in place, back it up the way the apply step did: ${GIT_FORM} If git holds it tracked and unchanged, git is its backup; otherwise copy it with cp to <file>.bak, or .bak2, .bak3 and so on if that exists. Report as backup that same list with any new files added.

Return what you did.`
}

// ---------------------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------------------

const investigate = (key, objection, phase) =>
  agent(investigatePrompt(key, objection), { label: `${objection ? 'reinvestigate' : 'investigate'}:${key}`, phase, schema: DECISION })

// FABRICATED stands only if every hunt ran and none found the work; everything else needs one reviewer.
async function review(d, phase) {
  if (d.decision === 'FABRICATED') {
    const finds = (await parallel(HUNTS.map(h => () =>
      agent(huntPrompt(d, h), { label: `find-by-${h.name}:${d.key}`, phase, schema: FIND })))).filter(Boolean)
    if (finds.length < HUNTS.length) return { upheld: false, problems: ['a search reviewer did not return a result, so the search for this work is incomplete'], evidence_urls: [] }
    const hits = finds.filter(f => f.found)
    return {
      upheld: hits.length === 0,
      problems: hits.map(f => `A reviewer found a possible source: "${f.title}" at ${f.url}. ${f.note}`),
      evidence_urls: hits.map(f => f.url),
    }
  }
  const v = await agent(verifyPrompt(d), { label: `verify:${d.key}`, phase, schema: VERDICT })
  return v || { upheld: false, problems: ['the reviewer did not return a verdict'], evidence_urls: [] }
}

const objectionText = v => v.problems.map(p => '- ' + p).join('\n') || '- (no details given)'

async function settleEntry(key) {
  const d = await investigate(key, null, 'Investigate')
  if (!d) return { kind: 'missing', job: 'investigate', keys: [key] }
  // Nothing to refute: the investigator itself could not confirm, so the author decides.
  if (d.decision === 'UNRESOLVED') return { kind: 'unresolved', key, attempts: [d], objections: [[`investigator: ${d.reason}`]] }
  const v = await review(d, 'Verify')
  if (v.upheld) return { kind: 'decision', ...d, key, rounds: 1, review_notes: v.problems }
  const d2 = await investigate(key, objectionText(v), 'Repair')
  if (!d2 || d2.decision === 'UNRESOLVED') return { kind: 'unresolved', key, attempts: d2 ? [d, d2] : [d], objections: [v.problems] }
  const v2 = await review(d2, 'Repair')
  if (v2.upheld) return { kind: 'decision', ...d2, key, rounds: 2, review_notes: v.problems }
  return { kind: 'unresolved', key, attempts: [d, d2], objections: [v.problems, v2.problems] }
}

async function settleDup(keys) {
  const g = await agent(dupPrompt(keys), { label: `duplicates:${keys.join('+')}`, phase: 'Investigate', schema: DUP })
  if (!g) return { kind: 'missing', job: 'duplicates', keys }
  const valid = g.decision === 'MERGED' && keys.includes(g.survivor) && g.retire.length > 0 &&
    g.retire.every(k => keys.includes(k) && k !== g.survivor)
  if (!valid) return { kind: 'keep_both', keys, reason: g.decision === 'MERGED' ? `invalid merge returned: ${JSON.stringify(g)}` : g.reason }
  const v = await agent(verifyDupPrompt(keys, g), { label: `verify-duplicates:${keys.join('+')}`, phase: 'Verify', schema: VERDICT })
  if (v && v.upheld) return { kind: 'merge', keys, survivor: g.survivor, retire: g.retire, reason: g.reason, evidence_urls: g.evidence_urls }
  return { kind: 'keep_both', keys, reason: `merge disputed by reviewer: ${v ? objectionText(v) : 'no verdict returned'}` }
}

async function checkContext(keys) {
  const c = await agent(contextPrompt(keys), { label: `context:${keys[0]}..(${keys.length})`, phase: 'Context', schema: CONTEXT })
  if (!c) return { kind: 'context', items: [], mismatches: [], missing: keys }
  const items = c.items.filter((i, n) => keys.includes(i.key) && c.items.findIndex(j => j.key === i.key) === n)
  const missing = keys.filter(k => !items.some(i => i.key === k))
  // A second reviewer that fails keeps the flag: only a reviewer who read the paper can clear it.
  const upheld = await parallel(items.filter(i => i.verdict === 'MISMATCH').map(i => () =>
    agent(mismatchPrompt(i), { label: `verify-mismatch:${i.key}`, phase: 'Verify', schema: VERDICT })
      .catch(() => null)
      .then(v => (!v || v.upheld ? { ...i, second_opinion: v ? v.problems : ['no verdict returned'] } : null))))
  return { kind: 'context', items, mismatches: upheld.filter(Boolean), missing }
}

// ---------------------------------------------------------------------------------------
// Run
// ---------------------------------------------------------------------------------------

const chunks = []
for (let i = 0; i < ok.length; i += BATCH) chunks.push(ok.slice(i, i + BATCH))
const work = [
  ...flagged.map(key => () => settleEntry(key)),
  ...groups.map(keys => () => settleDup(keys)),
  ...chunks.map(keys => () => checkContext(keys)),
]
log(`${flagged.length} flagged entries, ${groups.length} duplicate groups, ${ok.length} confirmed entries in ${chunks.length} context batches`)
const results = await parallel(work)

const out = { decisions: [], unresolved: [], merges: [], kept_both: [], mismatches: [], uncited: [], missing: [] }
results.forEach((r, i) => {
  if (!r) { // the thunk threw: report which job and keys were not covered
    if (i < flagged.length) out.missing.push({ job: 'investigate', keys: [flagged[i]] })
    else if (i < flagged.length + groups.length) out.missing.push({ job: 'duplicates', keys: groups[i - flagged.length] })
    else out.missing.push({ job: 'context', keys: chunks[i - flagged.length - groups.length] })
  } else if (r.kind === 'decision') out.decisions.push(r)
  else if (r.kind === 'unresolved') out.unresolved.push(r)
  else if (r.kind === 'merge') out.merges.push(r)
  else if (r.kind === 'keep_both') out.kept_both.push(r)
  else if (r.kind === 'missing') out.missing.push({ job: r.job, keys: r.keys })
  else if (r.kind === 'context') {
    out.mismatches.push(...r.mismatches.map(m => ({ key: m.key, citing_sentences: m.citing_sentences, reason: m.reason, evidence_url: m.evidence_url, suggestion: m.suggestion, second_opinion: m.second_opinion })))
    out.uncited.push(...r.items.filter(i => i.verdict === 'UNCITED').map(i => i.key))
    if (r.missing.length) out.missing.push({ job: 'context', keys: r.missing })
  }
})

// Merge only when no key's own investigation says it is a different or doubtful work.
const doubtful = new Set([
  ...out.decisions.filter(d => ['FABRICATED', 'SUBSTITUTED', 'FOUND'].includes(d.decision)).map(d => d.key),
  ...out.unresolved.map(u => u.key),
  ...out.missing.filter(m => m.job === 'investigate').flatMap(m => m.keys),
])
out.merges = out.merges.filter(m => {
  const bad = m.keys.filter(k => doubtful.has(k))
  if (bad.length) out.kept_both.push({ kind: 'keep_both', keys: m.keys, reason: `merge not applied, because the investigation of ${bad.join(', ')} did not confirm the matched paper. ${m.reason}` })
  return !bad.length
})

// A merge retires keys. A retired key's sentences now cite the survivor, so its support findings
// carry over to the survivor; the rest of its decision is moot.
const survivorOf = Object.fromEntries(out.merges.flatMap(m => m.retire.map(k => [k, m.survivor])))
for (const m of out.mismatches) if (survivorOf[m.key]) { m.reason = `cited as ${m.key}, now merged into ${survivorOf[m.key]}; ${m.reason}`; m.key = survivorOf[m.key] }
out.uncited = out.uncited.filter(k => !survivorOf[k])
for (const d of out.decisions) {
  if (d.decision !== 'FABRICATED' && (d.supports_claim === 'no' || d.supports_claim === 'partly')) {
    const via = survivorOf[d.key] ? `cited as ${d.key}, now merged into ${survivorOf[d.key]}; ` : ''
    out.mismatches.push({ key: survivorOf[d.key] || d.key, citing_sentences: d.citing_sentences, reason: `${via}supports the sentence: ${d.supports_claim}. ${d.reason}`, evidence_url: d.evidence_urls[0] || '', suggestion: d.rewording })
  }
  if (d.supports_claim === 'uncited' && !survivorOf[d.key]) out.uncited.push(d.key)
}
out.superseded = out.decisions.filter(d => survivorOf[d.key]).map(d => d.key)
out.decisions = out.decisions.filter(d => !survivorOf[d.key])

const count = {}
for (const d of out.decisions) count[d.decision] = (count[d.decision] || 0) + 1
out.counts = { ...count, UNRESOLVED: out.unresolved.length, MERGED: out.merges.length, MISMATCH: out.mismatches.length, MISSING: out.missing.reduce((n, m) => n + m.keys.length, 0) }
log(`Research done: ${JSON.stringify(out.counts)}`)
for (const m of out.missing) log(`No ${m.job} result for: ${m.keys.join(', ')}`)
// The completion notice truncates long results, so a one-line status and the small fields come first.
const summaryFirst = ({ counts, status, apply_error, audit2, audit, missing, applied, fixup, ...rest }) =>
  ({ counts, status, apply_error, audit2, audit, missing, applied, fixup, ...rest })
if (A.apply === false) return summaryFirst({ ...out, status: 'research only: nothing applied' })

// Applying runs inside the workflow because its agents keep the skill's pre-approved tools,
// while the main session's approvals end when this workflow's completion notice arrives.
// A throw here (for example a spent token budget) must not lose the research above.
try {
  out.applied = await agent(applyPrompt(out), { label: 'apply', phase: 'Apply', schema: APPLIED })
  if (out.applied) {
    out.audit = await agent(auditPrompt(out), { label: 'audit', phase: 'Apply', schema: VERDICT })
    if (out.audit && !out.audit.upheld && out.audit.problems.length) {
      out.fixup = await agent(fixPrompt(out), { label: 'fix', phase: 'Apply', schema: APPLIED })
      if (out.fixup) out.audit2 = await agent(auditPrompt(out), { label: 're-audit', phase: 'Apply', schema: VERDICT })
    }
  }
} catch (e) {
  out.apply_error = String(e && e.message || e)
  log(`Apply stage stopped: ${out.apply_error}`)
}
const finalAudit = out.fixup ? out.audit2 : out.audit
if (out.applied && !out.apply_error && !finalAudit) out.apply_error = 'an audit agent returned nothing, so the edits were not fully audited'
out.status = !out.applied ? 'not applied: the apply step did not report back'
  : out.apply_error ? `applied but not fully audited: ${out.apply_error}`
  : finalAudit.upheld ? 'applied and audited'
  : 'applied, but the audit still lists problems'
log(`Status: ${out.status}`)
return summaryFirst(out)
