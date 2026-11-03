# CLAUDE.md — agent working notes (read first, keep short)

Offline, air-gapped **read-only** Kubernetes AIOps agent (FastAPI + local Ollama +
kubectl over an allowlist). Fintech-grade: least-privilege, defence-in-depth,
audited. Python 3.12.

## THE GOLDEN RULE (never violate)
**The agent is READ-ONLY. Never add a tool, verb, endpoint, or RBAC permission that
can create/change/delete anything.** Three walls enforce it — keep all three:
`policy.py` allowlist → `executor.py` hard read-only verb check → cluster RBAC.
The LLM only *suggests*; the code decides. Output is always text; nothing it says
is executed.

## Run tests (ALWAYS before claiming done)
Deps live in a scratch venv (pydantic/fastapi not on the host):
```
SV="C:/Users/khan/AppData/Local/Temp/claude/C--Users-khan/990c7fab-6c1e-411a-9a9c-cc106ed9f5d1/scratchpad/v_pin"
"$SV/Scripts/python" -m py_compile app/*.py && "$SV/Scripts/python" -m pytest tests/ -p no:cacheprovider
```
Must stay green (186+). Every behaviour change needs a test. First run of a fresh
venv: `pip install -r requirements.txt -r requirements-dev.txt`.

## Two endpoints
- `POST /query` — one question → one read-only kubectl → output (fast).
- `POST /investigate` — bounded think→act→observe loop (`agent_loop.py`) → plain-English answer (slow, CPU).
- `client/ask.py` (bastion) auto-routes: why/logs/crash/event → investigate, else query.

## File map (app/)
main (wiring, rate-limit, concurrency cap, cache, 422 handler) · auth (bearer,
fail-closed) · schemas (pydantic) · llm (Ollama: interpret/next_step/synthesize/embed)
· policy (WALL1 allowlist + `_is_safe` injection guard + ns scope) · tools (read-only
tool catalog) · executor (WALL2 argv no-shell, hard read-only verbs, output caps) ·
counts (exact numbers in code, LLM must not count) · redact (mask secrets, ReDoS-safe,
both paths) · agent_loop (the loop + guards) · memory/memory_store (optional RAG,
off by default, tool-names-only, fail-open) · audit (JSON to stdout).

## Add a read-only tool = 3 places in sync
`tools.py` (CATALOG+build_argv) + `config.yaml investigation.tools` + `k8s/rbac/clusterrole.yaml`. Then test.

## Hard constraints
- Air-gapped: no internet calls from app code. Ollama is local.
- CPU-only 7B: slow by design (seconds–minutes). Don't "fix" slowness in code.
- Exact numbers come from `counts.py`, never the LLM.
- RAG stores tool NAMES only, never data; fail-open; never re-inject free-text.

## Never commit secrets
`AGENT_TOKEN`, kubeconfig, certs/keys, `.env` — all git-ignored. `SECRETS.local.md`
holds the token locally (ignored). `.env.example` is the committed template.

## Deploy (on AI server ai-dev 192.168.255.30)
```
git pull && export AGENT_TOKEN=... && docker compose up -d --build   # --build or code won't update
```
Warm model after: `docker exec ollama ollama run <model> "ok"`.

## Common gotchas (see docs/09-faq.md)
`invalid token`→token mismatch · 422→oversized q · `plan_error timed out`→cold/slow
Ollama (warm it) · collector "debug" config→restart didn't apply (validate first) ·
no host_name label→need `resource_to_telemetry_conversion: enabled: true` ·
localhost empty→ports bound to LAN IP · Loki crash→monitoring disk full (grow to 40GB).

## Docs
README (overview) · ARCHITECTURE.md (deep) · docs/00-START-HERE → 10 (setup→rag) ·
CHANGELOG.md. Keep docs accurate to code when you change behaviour.

## Open backlog (infra, not code): TLS→mTLS, OpenBao vault, grow monitoring VM, SIEM.
