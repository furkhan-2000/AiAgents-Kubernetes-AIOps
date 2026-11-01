export const meta = {
  name: 'aiops-hardening-review',
  description: 'Review the redundancy-guard + cache + prompt changes for correctness/security/races, verify each finding',
  phases: [{ title: 'Review' }, { title: 'Verify' }],
}
const REPO = 'C:/Users/khan/Documents/ISSUES/AiAgents-Kubernetes-AIOps'

const DIMS = [
  { key: 'cache-correctness', prompt: `Review the NEW TTL cache in ${REPO}/app/main.py (_cache, _cache_get, _cache_put, cache_ttl_seconds, used in /investigate). Find REAL defects: thread-safety/race on the dict+lock under Starlette's threadpool, stale-data risk, cache poisoning (e.g. caching an error/busy/empty response and serving it), unbounded growth, key collisions, interaction with the inflight semaphore. Concrete triggers only.` },
  { key: 'loop-logic', prompt: `Review the NEW redundancy guard + logic in ${REPO}/app/agent_loop.py (listed_all flag, skipping list_pods after list_all_pods, dedup, step/synth timeouts, budget math). Find REAL bugs: cases where the guard wrongly skips a legitimate step, breaks a valid investigation, infinite/empty loops, wrong budget, or lets the model get stuck. Concrete only.` },
  { key: 'security', prompt: `Security review of ${REPO} app/*.py + app/OLLAMA_BRAIN.md after changes. Re-check the three walls still hold (policy allowlist, executor read-only verbs, RBAC), redaction still applied on both paths and the final answer, no new injection/ReDoS/data-leak/DoS from the cache or guard. Report only real, reproducible issues.` },
  { key: 'robustness', prompt: `QA review: simulate many real human questions to ${REPO}'s /investigate and /query (e.g. "is anything broken", "why is X crashing", "show me node cpu", "list everything", typos, cluster-wide vs single-namespace, questions needing describe/logs/events, nonsense). Using the code (app/*.py, config.yaml, OLLAMA_BRAIN.md), find inputs where the logic still makes a 'stupid mistake': wrong scope, gives up wrongly, loops, wrong/empty answer, or a tool it should have used but can't. Concrete input -> expected vs actual.` },
]
const FIND = { type: 'object', properties: { findings: { type: 'array', items: { type: 'object', properties: { severity: { type: 'string', enum: ['critical','high','medium','low'] }, file: { type: 'string' }, line: { type: 'integer' }, summary: { type: 'string' }, failure_scenario: { type: 'string' }, suggested_fix: { type: 'string' } }, required: ['severity','file','summary','failure_scenario','suggested_fix'] } } }, required: ['findings'] }
const VERD = { type: 'object', properties: { real: { type: 'boolean' }, confidence: { type: 'string', enum: ['high','medium','low'] }, reason: { type: 'string' } }, required: ['real','reason'] }

const results = await pipeline(
  DIMS,
  d => agent(d.prompt, { label: `review:${d.key}`, phase: 'Review', schema: FIND, agentType: 'general-purpose' }).then(r => ({ key: d.key, findings: (r && r.findings) || [] })),
  review => parallel((review.findings || []).map(f => () =>
    agent(`Adversarially verify against actual code at ${REPO}. Real, reproducible defect NOW (not hypothetical, not a known infra item like TLS/single-token, not acceptable best-effort redaction)? Read the code; default real=false unless you can show the trigger.\n\n${JSON.stringify(f)}`,
      { label: `verify:${review.key}`, phase: 'Verify', schema: VERD, agentType: 'general-purpose' }).then(v => ({ ...f, dimension: review.key, verdict: v })))))

const confirmed = results.flat().filter(Boolean).filter(f => f.verdict && f.verdict.real)
const order = { critical:0, high:1, medium:2, low:3 }
return { confirmed_count: confirmed.length, rejected_count: results.flat().filter(Boolean).length - confirmed.length, all_clear: confirmed.length === 0, confirmed: confirmed.sort((a,b)=>(order[a.severity]??9)-(order[b.severity]??9)) }
