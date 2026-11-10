# Start Here

New to this project? **Read this file first**, then follow the guides in order.
Each guide is short. Every command has a one-line "what it does".

## Read in this order
1. **[01-prerequisites.md](01-prerequisites.md)** — the machines and tools you need first.
2. **[02-setup.md](02-setup.md)** — zero to a running agent, step by step.
3. **[03-config.md](03-config.md)** — every setting and what it does.
4. **[04-testing.md](04-testing.md)** — prove it works (and is safe).
5. **[05-operations.md](05-operations.md)** — deploy updates, logs, common fixes.
6. **[06-security.md](06-security.md)** — the safety model, in short.
7. **[07-monitoring.md](07-monitoring.md)** — metrics + logs for all VMs (Prometheus/Loki/Grafana + OTel).
8. **[08-glossary.md](08-glossary.md)** — every term in plain English.
9. **[09-faq.md](09-faq.md)** — "I see X → do Y" troubleshooting (check here when stuck).
10. **[10-rag.md](10-rag.md)** — optional memory that makes the model more consistent (how + why + secure).
11. **[11-rag-eval-and-interview.md](11-rag-eval-and-interview.md)** — RAG evaluation deep-dive: metrics, KPIs, interview Q&A, real scenarios.

For the full "how it works" deep dive, see **[../ARCHITECTURE.md](../ARCHITECTURE.md)**.
History of changes: **[../CHANGELOG.md](../CHANGELOG.md)**.

## What this is, in 20 seconds
An **offline** assistant for Kubernetes. You ask in plain English; a **local** AI
picks a **read-only** command; the code runs it and explains the result. It can
**never change anything**. Nothing leaves your network.

## The flow

```mermaid
flowchart LR
    U[User on bastion] -->|HTTP 443| A[Agent on AI server]
    A -->|which read-only tool?| O[(Ollama LLM - local)]
    A -->|WALL 1: policy allowlist| P{allowed?}
    P -->|no| X[refuse + log]
    P -->|yes| E[WALL 2: read-only kubectl]
    E -->|via L4 proxy| K[(Cluster - WALL 3 read-only RBAC)]
    K --> A
    A -->|plain English answer| U
```

Three independent walls (policy → executor → RBAC). All must fail for a write to
happen — and no write tool exists at all.

## The whole system at a glance

```mermaid
flowchart TB
    subgraph Edge["Access"]
      U["User laptop<br/>VPN / ZTNA"]
    end
    subgraph Core["Read-only AI agent path"]
      B["Bastion<br/>only door"] --> A["AI server<br/>agent + Ollama (offline)"]
      A --> PX["Reverse proxy<br/>L4"] --> K[("Kind cluster<br/>read-only RBAC")]
    end
    subgraph Obs["Observability (offline)"]
      OC["OTel collectors<br/>on every VM"] --> MON["Monitoring VM<br/>Prometheus + Loki + Grafana"]
    end
    U --> B
    OC -. metrics+logs .-> MON
    A -. metrics+logs .-> OC
    classDef safe fill:#10331f,stroke:#2ea043,color:#d2f5dd;
    class K,A safe;
```

- **Agent path** (left→right): ask a question → local LLM → read-only kubectl.
- **Observability** (bottom): every VM ships metrics + logs to one monitoring VM.
- Nothing in either path touches the internet.

## The two ways to ask
- **`/query`** — quick facts ("how many pods"). Fast.
- **`/investigate`** — "why is X broken?". A multi-step loop that explains. Slower.

The `ask` client picks the right one for you automatically.
