# CLAUDE.md — operating contract (read once; act, don't re-derive)

Offline, air-gapped **READ-ONLY** Kubernetes AIOps agent. FastAPI + local Ollama +
kubectl over an allowlist. Python 3.12. Fintech-grade: least-privilege, 3 walls,
audited. Detail lives in `docs/` + `ARCHITECTURE.md` — load ONLY when the task needs it.

## STOP — hard refusals (never do, no exceptions)
- Add any tool/verb/endpoint/RBAC that can **write** (create/apply/patch/delete/scale/exec/cp/drain). Read-only only.
- Add **secrets** or **configmaps** to the allowlist/RBAC.
- Commit a secret (token, kubeconfig, cert/key, `.env`).
- Use a **cloud LLM** or make any outbound internet call from app code (breaks air-gap).
- Introduce `shell=True` / string-built commands. Always argv.
- Claim tests pass without running them.

## The 3 walls (keep all three; never weaken)
`policy.py` allowlist → `executor.py` hard read-only verbs (no shell) → cluster RBAC.
LLM only suggests; code decides; output is text, never executed.

## ALWAYS before "done"
```
SV="C:/Users/khan/AppData/Local/Temp/claude/C--Users-khan/990c7fab-6c1e-411a-9a9c-cc106ed9f5d1/scratchpad/v_pin"
"$SV/Scripts/python" -m py_compile app/*.py && "$SV/Scripts/python" -m pytest tests/ -p no:cacheprovider
```
Must be green. `tests/test_security_invariants.py` is the executable gate — if it
fails, a wall was weakened; fix the code, never the test. Behaviour change = add a test.

## Decision playbook (if X → do Y, one hop)
- add a read-only tool → `tools.py` (CATALOG+build_argv) + `config.yaml` tools/allowlist + confirm in `k8s/rbac/clusterrole.yaml` → run security tests.
- new behaviour → code + test + update the matching doc, same change.
- `/investigate` slow / `synth_error timed out` → Ollama cold/slow on CPU, warm it; do NOT "fix" in code.
- exact numbers → `counts.py` (never trust the LLM to count).
- secret-looking output → `redact.py` (both paths); never log raw.
- security-relevant change → read `ARCHITECTURE.md` §5 first, then edit.
- RAG → tool-names only, fail-open, never re-inject free-text (`memory.py`).

## Context efficiency (token discipline)
- Don't open files without a reason; don't re-read a file already read this session.
- Prefer the file map below over grepping for "where is X".
- Delegate broad multi-file searches to a sub-agent; keep only the conclusion.
- Output = what changed + tests/result + security impact + residual risk. No narration.

## File map (app/) — jump straight here
main (wiring, limiter, concurrency cap, cache, 422, RAG build) · auth (bearer, fail-closed)
· schemas · llm (route/interpret/next_step/synthesize/embed) · policy (WALL1 allowlist + `_is_safe`)
· tools (read-only catalog) · executor (WALL2 argv, read-only verbs, caps) · counts (exact
numbers) · redact (mask secrets, ReDoS-safe) · agent_loop (the loop + guards + RAG) ·
memory/memory_store (RAG, ON by default, pre-seeded, persistent volume) · audit (JSON stdout). config.yaml at repo root.

## Deploy (ai-dev 192.168.255.30)
`git pull && export AGENT_TOKEN=... && docker compose up -d --build`  (--build or code won't update).
Warm after: `docker exec ollama ollama run <model> "ok"` (or install `infra/systemd/warm-models.service` to warm on boot).

## Gotchas (full list: docs/09-faq.md)
invalid-token→mismatch · 422→oversized q · plan_error timed out→cold Ollama (warm) ·
collector "debug" config→restart didn't apply (validate first) · no host_name label→need
`resource_to_telemetry_conversion: enabled: true` · localhost empty→ports bound to LAN IP ·
Loki crash→monitoring disk full (≥40GB).

## Pointers (load on demand only)
setup→docs/02 · config→docs/03 · test→docs/04 · ops→docs/05 · security→docs/06 ·
monitoring→docs/07 · faq→docs/09 · rag→docs/10 · deep→ARCHITECTURE.md · history→CHANGELOG.md.

## Self-maintenance
Changed behaviour? Update CLAUDE.md + the doc + a test in the SAME change. Stale > missing.

## Backlog (infra, not code): TLS→mTLS · OpenBao vault · grow monitoring VM · SIEM · GPU for speed.
