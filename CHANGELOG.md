# Changelog

What changed, newest first. Dates are when the work landed.

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
