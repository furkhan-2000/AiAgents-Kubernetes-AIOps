# Changelog

What changed, newest first. Dates are when the work landed.

## 2026-11-04 — Docs accuracy pass + RAG pre-seed + boot warm-up
- RAG now **pre-seeds** from `config.yaml rag.seed` on first start (empty DB) so it
  helps from the first question; emits `rag.seeded` / `rag.seed_error` (fail-open).
  Config: added `seed` key (docs 03 + 10). Test: `test_seed_tools_are_all_allowlisted`.
- Added `infra/systemd/warm-models.service` (oneshot, warms chat + embed models on
  boot); documented in `docs/05-operations.md`.
- Docs-accuracy sweep (agent-audited): fixed README `/query` JSON example (`verb`/
  `get`, not `action`/`list`), per-endpoint rate limits (10 vs 6/min), repo layout
  (added tests/ docs/ infra/monitoring infra/systemd), allowlist↔RBAC wording (RBAC
  is a read-only superset), model name precision (`qwen2.5:7b-instruct-q5_K_M`),
  compose Q4→Q5 comment, and `app/main.py` route docstring.

## 2026-11-03 — RAG memory (optional, off by default)
- Added RAG memory: remembers which read-only tools answered similar past questions
  and suggests them, to make the weak CPU model more consistent.
- Security-hardened: stores tool NAMES only (no cluster data/secrets), no free-text
  re-injected (no prompt-injection surface), allowlist-gated, similarity threshold,
  size cap, fully fail-open; the three walls still gate every tool.
- Files: `app/memory.py`, `app/memory_store.py` (sqlite-vec), `llm.embed()`, wired
  into the investigate loop; `config.yaml rag:` block; `agent_memory` volume.
- Data never goes stale: stores the method, runs tools live every time.
- Docs: `10-rag.md`. Tests: +11 (186 total, all green).

## 2026-11-03 — Monitoring + docs
- Added full observability stack: OTel collectors on every VM → Prometheus + Loki +
  Grafana on a dedicated monitoring VM (offline, digest-pinned, hardened).
- Host metrics, container metrics (`docker_stats`), and logs flowing; fleet +
  container dashboards built on OTel metrics.
- New docs: `07-monitoring.md`, `08-glossary.md`, `09-faq.md`; architecture diagrams
  added; `infra/monitoring/` stack files committed.

## 2026-11-01/02 — Agent hardening + quality
- Deep multi-agent review rounds; fixed all confirmed findings.
- Security: 3 walls (policy → executor read-only → RBAC); secret redaction on both
  paths; ReDoS-safe redaction; flag-injection blocked; prompt-injection mitigations;
  concurrency cap + rate limit + time/output/memory caps.
- Correctness: deterministic counts in code (no LLM miscounts); plural/singular
  normalization; out-of-scope refusal; all-namespaces support; multi-container logs;
  redundancy + dedupe guards in the investigate loop; English-only answers.
- Dependencies upgraded past CVEs (fastapi 0.162, starlette 1.10, etc.); kubectl +
  base image pinned/verified.
- Model: qwen2.5:7b-instruct → q5_K_M; CPU tuning (num_ctx, keep_alive, budgets).
- Client: `ask` TUI with auto-routing (`/query` vs `/investigate`), plain-text output,
  token from a 600 file (no re-exporting).
- Tests: full pytest suite (unit + integration + security), 175 passing.

## Earlier — initial build
- Offline AIOps agent: FastAPI + Ollama + read-only kubectl over an allowlist.
- Two endpoints: `/query` (one command) and `/investigate` (think-act-observe loop).
- Air-gapped VM topology (VPN → bastion → AI server → proxy → Kind cluster).
