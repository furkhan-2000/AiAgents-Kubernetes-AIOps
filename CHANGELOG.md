# Changelog

What changed, newest first. Dates are when the work landed.

## 2026-11-10 — LLM router + cluster-metadata tools
- **LLM routing:** new `POST /ask` endpoint — a small, constrained LLM call (`llm.route`,
  `num_predict:5`, validated, defaults to `investigate`) decides query-vs-investigate by
  MEANING, replacing the client's keyword routing that wrongly refused questions with no
  trigger word (e.g. "what k8s version"). Belt-and-suspenders: a query the model can't map
  falls back to the investigate loop. Both paths still pass the same 3 read-only walls, so a
  misroute is never unsafe. `/query` + `/investigate` kept for back-compat; `client/ask.py`
  thinned to just post `/ask`. Audit: `route.decided`, `route.fallback`.
- **+18 read-only tools:** cluster metadata (`get_version`, `cluster_info`, `get_api_resources`,
  `get_crds`, `get_component_statuses`) + workloads (daemonsets/statefulsets/replicasets/jobs/
  cronjobs/hpa + describes) + networking/storage (networkpolicies/storageclasses/resourcequotas).
  Wired into tools.py + config (investigation.tools + allowlist); RBAC +componentstatuses.
  So "which CNI?" → `list_daemonsets` → kindnet. Secrets/configmaps still forbidden.
- Tests: 213 green (router parse/default, /ask dispatch + fallback, new tools read-only,
  config-tools-have-builders, cluster-scoped tools pass under a namespace allowlist).
- Fix: 6 new cluster-scoped metadata tools were missing from policy._CLUSTER_SCOPED_TOOLS
  (would be denied if allowed_namespaces were ever set); added + regression test.

## 2026-11-09 — RAG eval KPI (hit-rate/precision → Prometheus/Grafana)
- Added `eval/rag_eval.py` + `eval/rag_eval_set.yaml`: replays a fixed eval set through
  the LIVE RAG store, computes hit-rate/precision/recall/latency, serves `/metrics`
  (deterministic, no LLM judge, air-gap-safe). Baked into the image (`COPY eval ./eval`).
- Added `rag-eval` sidecar to `docker-compose.yml` (serves `127.0.0.1:9108`); the ai-dev
  OTel collector scrapes it and ships via the existing remote-write path → Grafana KPI,
  no new firewall rule. Measured live: hit-rate 0.92, recall 0.92, latency 0.06s.
- Docs: `docs/11-rag-eval-and-interview.md` (eval metrics + interview Q&A + scenarios),
  `eval/README.md` (setup), `docs/10-rag.md` (why no reranker; eval = "metrics-server for
  the RAG"; auto-generating eval sets at scale).

## 2026-11-09 — Investigate-loop robustness + CPU-safe tuning
- **All-namespace sweep:** investigate LIST/GET tools now search every namespace when
  the model names none (was silently defaulting to `default`, giving wrong
  default-only answers). `app/tools.py` `_ns_scope()`; +security-invariant test.
- **Synthesis retry:** on a synth timeout the loop retries once with a hard-trimmed
  history before degrading, so broad questions still summarise on a slow CPU; honest
  fallback otherwise (never a fabricated answer). Audit: `loop.synth_retry_ok/failed`.
- **Count accuracy:** exact `[EXACT COUNT]` is now computed on the FULL table before
  truncation (a lowered `max_output_bytes` was undercounting large tables).
- **CPU-safe config:** `max_concurrent: 2→1` (two 7B calls on CPU saturated Ollama →
  "Connection refused"), `max_steps: 4→3`, `synth_reserve_seconds: 200→240`,
  `max_output_bytes` kept at 3000 (lower truncated prd pods off the list).
- Tests: 206 green (added retry-success + count-under-truncation regressions).

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
