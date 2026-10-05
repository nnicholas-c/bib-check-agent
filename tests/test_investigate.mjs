// Offline check of investigate.js control flow, with canned agents. Run: node test_investigate.mjs
import { readFileSync } from 'node:fs'
import assert from 'node:assert/strict'

const src = readFileSync(new URL('../skills/bib-check-agent/workflows/investigate.js', import.meta.url), 'utf8').replace(/^export const meta/m, 'const meta')
const run = new Function('args', 'agent', 'parallel', 'log', `return (async () => {\n${src}\n})()`)

const dec = (key, decision, extra = {}) => ({
  key, decision, reason: 'r', evidence_urls: ['https://example.org/' + key], bibtex: decision === 'FABRICATED' || decision === 'KEPT' ? '' : `@misc{${key}, title={T}}`,
  bibtex_source: '', citing_sentences: ['s'], supports_claim: decision === 'FABRICATED' ? 'no' : 'yes', rewording: '', searched: '', ...extra,
})
const yes = { upheld: true, problems: [], evidence_urls: [] }
const no = { upheld: false, problems: ['wrong year'], evidence_urls: [] }
const none = { found: false, title: '', url: '', note: 'nothing' }
const item = (key, verdict) => ({ key, verdict, citing_sentences: ['s'], reason: verdict, evidence_url: '', suggestion: '' })
const applied = { backup: 'r.bib.bak', changed_keys: [], tex_files_changed: [], compile: 'no LaTeX installed', rerun: '', rerun_flags: [], decisions_file: 'r.decisions.md', problems: [] }
const canned = {
  'investigate:fixed': () => dec('fixed', 'FIXED'),
  'verify:fixed': () => yes,
  'investigate:fake': () => dec('fake', 'FABRICATED'),
  'find-by-title:fake': () => none,
  'find-by-authors:fake': () => none,
  'investigate:sub': () => dec('sub', 'FABRICATED'),
  'find-by-title:sub': () => ({ found: true, title: 'Real paper', url: 'https://example.org/real', note: 'same work' }),
  'find-by-authors:sub': () => none,
  'reinvestigate:sub': () => dec('sub', 'FOUND'),
  'verify:sub': () => yes,
  'investigate:halfhunt': () => dec('halfhunt', 'FABRICATED'),
  'find-by-title:halfhunt': n => (n === 1 ? null : none),  // first title search dies
  'find-by-authors:halfhunt': () => none,
  'reinvestigate:halfhunt': () => dec('halfhunt', 'FABRICATED'),
  'investigate:stuck': () => dec('stuck', 'FOUND'),
  'verify:stuck': () => no,
  'reinvestigate:stuck': () => dec('stuck', 'FOUND'),
  'investigate:partly': () => dec('partly', 'FIXED', { supports_claim: 'partly', rewording: 'softer claim' }),
  'verify:partly': () => yes,
  'investigate:boom': () => { throw new Error('agent crashed') },
  'investigate:noweb': () => dec('noweb', 'UNRESOLVED', { reason: 'likely fabricated; no web search' }),
  'investigate:dupb': () => dec('dupb', 'FIXED', { supports_claim: 'no', rewording: 'r2' }),
  'verify:dupb': () => yes,
  'duplicates:dupa+dupb': () => ({ decision: 'MERGED', survivor: 'dupa', retire: ['dupb'], reason: 'same', evidence_urls: [] }),
  'verify-duplicates:dupa+dupb': () => yes,
  'duplicates:c1+c2': () => ({ decision: 'MERGED', survivor: 'c1', retire: ['c1'], reason: 'bad', evidence_urls: [] }),
  'duplicates:fake+fakedup': () => ({ decision: 'MERGED', survivor: 'fakedup', retire: ['fake'], reason: 'same', evidence_urls: [] }),
  'verify-duplicates:fake+fakedup': () => yes,
  'duplicates:ok6+ok7': () => ({ decision: 'MERGED', survivor: 'ok6', retire: ['ok7'], reason: 'same', evidence_urls: [] }),
  'verify-duplicates:ok6+ok7': () => yes,
  'verify-mismatch:ok7': () => yes,
  'context:ok1..(8)': () => ({ items: [
    item('ok1', 'SUPPORTS'), item('ok2', 'MISMATCH'), item('ok2', 'MISMATCH'), item('ok4', 'MISMATCH'),
    item('ok5', 'UNCITED'), item('ok6', 'SUPPORTS'), item('ok7', 'MISMATCH'), item('ok8', 'SUPPORTS'), item('stray', 'UNCITED'),
  ] }),  // ok3 left out
  'context:ok9..(1)': () => null,                      // a context agent that dies
  'verify-mismatch:ok2': () => yes,
  'verify-mismatch:ok4': () => { throw new Error('budget spent') },
  apply: () => applied,
  audit: () => no,
  fix: () => applied,
  're-audit': () => yes,
}
function harness() {
  const seen = {}
  const agent = async (prompt, { label, schema }) => {
    seen[label] = (seen[label] || 0) + 1
    assert.ok(schema, `agent ${label} has no schema`)
    assert.ok(canned[label], `unexpected agent ${label}`)
    return canned[label](seen[label], prompt)
  }
  const parallel = thunks => Promise.all(thunks.map(t => t().catch(() => null)))
  return { seen, agent, parallel }
}
const ARGS = {
  project: '/p', bib: '/p/r.bib', report: '/p/r.report.md', verified: '/p/r.verified.bib', checker: 'python3 "v.py"',
  flagged: ['fixed', 'fake', 'sub', 'halfhunt', 'stuck', 'partly', 'boom', 'dupb', 'noweb'],
  ok: ['ok1', 'ok2', 'ok3', 'ok4', 'ok5', 'ok6', 'ok7', 'ok8', 'ok9'],
  duplicates: [['dupa', 'dupb'], ['c1', 'c2'], ['fake', 'fakedup'], ['ok6', 'ok7'], ['lonely']],
}

// Research only
let h = harness()
let out = await run({ ...ARGS, apply: false }, h.agent, h.parallel, () => {})
const by = Object.fromEntries(out.decisions.map(d => [d.key, d]))
assert.deepEqual(Object.keys(by).sort(), ['fake', 'fixed', 'halfhunt', 'partly', 'sub'])
assert.equal(by.fixed.rounds, 1)
assert.equal(by.fake.decision, 'FABRICATED'); assert.equal(by.fake.rounds, 1)       // both hunts ran and found nothing
assert.equal(by.sub.decision, 'FOUND'); assert.equal(by.sub.rounds, 2)              // a hunter's find forced a repair round
assert.equal(by.halfhunt.decision, 'FABRICATED'); assert.equal(by.halfhunt.rounds, 2) // a dead hunt is not a pass
assert.deepEqual(out.unresolved.map(u => u.key).sort(), ['noweb', 'stuck'])          // refuted twice, or the investigator could not confirm
assert.ok(!h.seen['verify:noweb'] && !h.seen['find-by-title:noweb'])                // nothing to refute, so no reviewer runs
assert.equal(out.unresolved.find(u => u.key === 'stuck').attempts.length, 2)
assert.deepEqual(out.merges.map(m => m.survivor), ['dupa', 'ok6'])
assert.deepEqual(out.superseded, ['dupb'])                                          // merged away, so its own fix is moot
assert.deepEqual(out.kept_both.map(k => k.keys.join('+')).sort(), ['c1+c2', 'fake+fakedup']) // invalid merge; merge into a fabricated entry
assert.deepEqual(out.mismatches.map(m => m.key).sort(), ['dupa', 'ok2', 'ok4', 'ok6', 'partly']) // ok4's reviewer crashed, so the flag stays
assert.match(out.mismatches.find(m => m.key === 'ok6').reason, /cited as ok7, now merged into ok6/) // so does a context finding
assert.match(out.mismatches.find(m => m.key === 'dupa').reason, /cited as dupb, now merged into dupa/) // a retired key's finding moves to the survivor
assert.deepEqual(out.mismatches.find(m => m.key === 'ok4').second_opinion, ['no verdict returned'])
assert.deepEqual(out.uncited, ['ok5'])                                              // "stray" was not in the batch
assert.deepEqual(out.missing, [{ job: 'investigate', keys: ['boom'] }, { job: 'context', keys: ['ok3'] }, { job: 'context', keys: ['ok9'] }])
assert.equal(out.counts.MISSING, 3)
assert.equal(h.seen['verify-mismatch:ok2'], 1)                                     // the duplicated row was verified once
assert.equal(h.seen['verify:stuck'], 2)
assert.ok(!h.seen.apply && !out.applied)
assert.equal(out.status, 'research only: nothing applied')

// With the apply stage: the audit fails once, the fix agent runs, and the re-audit passes
h = harness()
out = await run(ARGS, h.agent, h.parallel, () => {})
assert.deepEqual([h.seen.apply, h.seen.audit, h.seen.fix, h.seen['re-audit']], [1, 1, 1, 1])
assert.equal(out.audit2.upheld, true)
assert.equal(out.status, 'applied and audited')

// An audit agent that dies without throwing must not pass as audited
h = harness(); const real0 = h.agent
out = await run(ARGS, (p, o) => (o.label === 'audit' ? Promise.resolve(null) : real0(p, o)), h.parallel, () => {})
assert.match(out.status, /^applied but not fully audited/)

// A throw in the apply stage keeps the research and reports the error
const crash = { ...canned, apply: () => { throw new Error('token budget spent') } }
h = harness(); const real = h.agent
out = await run(ARGS, (p, o) => (o.label === 'apply' ? crash.apply() : real(p, o)), h.parallel, () => {})
assert.equal(out.apply_error, 'token budget spent'); assert.equal(out.decisions.length, 5)
assert.match(out.status, /^not applied/)
assert.deepEqual(Object.keys(out).slice(0, 3), ['counts', 'status', 'apply_error'])  // summary fields survive truncation

// Google Scholar results gathered with the user reach every agent judging that entry, and only that entry
h = harness(); const real1 = h.agent, prompts = {}
await run({ ...ARGS, apply: false, scholar: { fake: 'no match: "did not match any articles"' } },
  (p, o) => { prompts[o.label] = p; return real1(p, o) }, h.parallel, () => {})
for (const label of ['investigate:fake', 'find-by-title:fake', 'find-by-authors:fake'])
  assert.match(prompts[label], /searched by the main session in the user's browser: no match/, label)
assert.match(prompts['investigate:fixed'], /Nobody searched Google Scholar/)
assert.match(prompts['verify:fixed'], /Never try to get around it/)
assert.match(prompts['investigate:fake'], /A \[CITATION\] item never counts/)                   // a listing is a lead, a stub never a source
assert.match(prompts['investigate:fixed'], /Don't use the browser tools/)                          // the user's browser stays with the main session
h = harness(); const real2 = h.agent, prompts2 = {}
await run({ ...ARGS, apply: false, scholar: { fake: 'not searched', fixed: { title: 'X' }, constructor: 'junk' } },
  (p, o) => { prompts2[o.label] = p; return real2(p, o) }, h.parallel, () => {})
assert.match(prompts2['investigate:fake'], /Nobody searched Google Scholar/)                      // "not searched" is not a search
assert.match(prompts2['investigate:fixed'], /user's browser: \{"title":"X"\}/)                     // non-text values are shown as JSON
await assert.rejects(run({ ...ARGS, scholar: ['fake'] }, h.agent, h.parallel, () => {}), /args.scholar must be an object/)
assert.match(prompts['find-by-title:fake'], /Never try to get around a CAPTCHA/)                    // the hunters carry the rule too
assert.match(prompts['find-by-title:fake'], /Don't use the browser tools/)
h = harness(); const real3 = h.agent, prompts3 = {}
await run({ ...ARGS, apply: false, scholar_skipped: true }, (p, o) => { prompts3[o.label] = p; return real3(p, o) }, h.parallel, () => {})
assert.match(prompts3['investigate:fixed'], /The user stopped the Google Scholar searches/)          // after Skip, agents leave Scholar alone
assert.doesNotMatch(prompts3['investigate:fixed'], /You may open https:\/\/scholar/)

// Bad input fails loudly instead of checking nothing
await assert.rejects(run({ ...ARGS, flagged: 'fixed,fake' }, h.agent, h.parallel, () => {}), /must be an array/)
await assert.rejects(run({ ...ARGS, bib: '' }, h.agent, h.parallel, () => {}), /args.bib is required/)
console.log('ok', JSON.stringify(out.counts))
