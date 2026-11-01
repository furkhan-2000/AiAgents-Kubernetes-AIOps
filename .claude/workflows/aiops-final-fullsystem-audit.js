export const meta = {
  name: 'aiops-final-fullsystem-audit',
  description: 'Final full-system audit (counts, routing, cache, english, security walls, robustness) with verification',
  phases: [{ title: 'Audit' }, { title: 'Verify' }],
}
const REPO = 'C:/Users/khan/Documents/ISSUES/AiAgents-Kubernetes-AIOps'
const DIMS = [
  { key: 'counts', prompt: `Review ${REPO}/app/counts.py and how it is wired in agent_loop.py and main.py. Find REAL defects in deterministic counting: wrong row/status counts for real kubectl output shapes (list_all_pods with NAMESPACE col and 'RESTARTS 9 (5m ago)', list_pods, get nodes, describe output, empty, error), index misalignment, the injected [EXACT COUNT] being wrong or exploitable. Concrete input -> wrong output.` },
  { key: 'security', prompt: `Security re-check of ${REPO} app/*.py, OLLAMA_BRAIN.md, config.yaml. Confirm the three walls (policy allowlist, executor read-only verbs, RBAC) still hold after the counts/cache/routing/english changes. Look for injection, ReDoS, data leak via the [EXACT COUNT] line or cache, action-request bypass, redaction still on both paths + final answer. Report only real reproducible issues.` },
  { key: 'cache-concurrency', prompt: `Review the TTL cache + inflight semaphore + sync handlers in ${REPO}/app/main.py for races, poisoning (caching busy/error/empty), staleness, leaks, key issues, under Starlette threadpool concurrency. Real triggers only.` },
  { key: 'robustness', prompt: `QA: simulate many real human questions (status, counts, why-broken, logs, nodes cpu, typos, non-English, action requests, nonsense) against ${REPO} code (app/*.py, client/ask.py routing, OLLAMA_BRAIN.md). Find where routing picks the wrong endpoint, the loop makes a stupid mistake, counts are wrong, or English-only could break. Concrete input -> expected vs actual.` },
  { key: 'tests', prompt: `Review ${REPO}/tests/*.py vs app/*.py. Find tests asserting wrong behavior, vacuous tests, mocks hiding bugs, or important new logic (counts.py, routing, cache, redundancy guard, english) left untested. Concrete gaps only.` },
]
const FIND = { type: 'object', properties: { findings: { type: 'array', items: { type: 'object', properties: { severity: { type: 'string', enum: ['critical','high','medium','low'] }, file: { type: 'string' }, line: { type: 'integer' }, summary: { type: 'string' }, failure_scenario: { type: 'string' }, suggested_fix: { type: 'string' } }, required: ['severity','file','summary','failure_scenario','suggested_fix'] } } }, required: ['findings'] }
const VERD = { type: 'object', properties: { real: { type: 'boolean' }, confidence: { type: 'string', enum: ['high','medium','low'] }, reason: { type: 'string' } }, required: ['real','reason'] }
const results = await pipeline(
  DIMS,
  d => agent(d.prompt, { label: `audit:${d.key}`, phase: 'Audit', schema: FIND, agentType: 'general-purpose' }).then(r => ({ key: d.key, findings: (r && r.findings) || [] })),
  review => parallel((review.findings||[]).map(f => () =>
    agent(`Verify against actual code at ${REPO}. Real reproducible defect NOW (not hypothetical, not known infra like TLS/single-token/GPU, not acceptable best-effort redaction)? Read code; default real=false unless you show the trigger.\n${JSON.stringify(f)}`,
      { label: `verify:${review.key}`, phase: 'Verify', schema: VERD, agentType: 'general-purpose' }).then(v => ({ ...f, dimension: review.key, verdict: v })))))
const confirmed = results.flat().filter(Boolean).filter(f => f.verdict && f.verdict.real)
const order = { critical:0, high:1, medium:2, low:3 }
return { confirmed_count: confirmed.length, rejected_count: results.flat().filter(Boolean).length - confirmed.length, all_clear: confirmed.length===0, confirmed: confirmed.sort((a,b)=>(order[a.severity]??9)-(order[b.severity]??9)) }
