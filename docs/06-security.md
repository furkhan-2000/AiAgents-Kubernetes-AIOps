# 6. Security (in short)

## The three walls
```
Question → [WALL 1 policy allowlist] → [WALL 2 executor read-only] → [WALL 3 cluster RBAC]
```
- **WALL 1** (`policy.py`): only allowlisted read-only actions pass; blocks injection.
- **WALL 2** (`executor.py`): refuses any non-read-only verb; no shell (argv only).
- **WALL 3** (`k8s/rbac`): the cluster grants read-only, never secrets/configmaps.

All three must fail for a write — and no write tool exists anywhere.

## Other protections
- **Auth** fail-closed bearer token (constant-time compare).
- **Redaction** masks secret-looking output on every path.
- **Exact counts** computed in code (model never trusted for numbers).
- **Audit** one JSON line per event → SIEM.
- **Limits** rate-limit, concurrency cap, time/output/memory caps (anti-DoS).
- **Air-gap** Ollama on an internal-only network; nothing leaves.

## The golden rule (for contributors)
> **Never add a tool, verb, or endpoint that can create, change, or delete
> anything.** The entire safety model depends on the agent being read-only. If you
> need an action, that belongs in a separate, human-approved automation (e.g. n8n),
> never in this agent.

## Known gaps (on the hardening backlog)
- The agent speaks **plain HTTP** today → add **TLS, then mTLS**.
- A **single shared token** → move to **per-user identity** (mTLS) + a **vault**
  (OpenBao) for secrets.
These are infra tasks, not code — tracked and planned.

## Report an issue
Do not open cluster details publicly. Share findings with the platform owner
directly.
