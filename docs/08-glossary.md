# 8. Glossary

Plain-English meaning of every term used in these docs.

| Term | In simple words |
|------|------------------|
| **Agent** | The small program that answers your Kubernetes questions (read-only). |
| **Air-gapped** | No internet. Everything runs locally; data never leaves your network. |
| **Allowlist** | The exact list of things the agent is permitted to do. Everything else = denied. |
| **Bastion** | The single hardened "front door" VM. All access goes through it. |
| **Bar gauge** | A Grafana panel: colored bars that fill up (good for % per host). |
| **Blast radius** | How far an attacker gets if one box is hacked. We keep it tiny. |
| **Collector (OTel)** | Small agent on each VM that gathers metrics + logs and sends them to the monitoring VM. |
| **Container** | A running app packaged by Docker (e.g. ollama, agent, prometheus). |
| **Data source** | Where Grafana reads from (Prometheus for metrics, Loki for logs). |
| **Digest pin** | Locking an image by its `@sha256:…` hash so it can never change. |
| **docker_stats** | The OTel part that reads per-container CPU/RAM. |
| **Fail-closed** | If something is missing/wrong, it refuses — never guesses. |
| **Grafana** | The web UI where you see dashboards and logs. |
| **hostmetrics** | The OTel part that reads the whole VM's CPU/RAM/disk. |
| **Kind** | Kubernetes running inside Docker (a test cluster). |
| **kubeconfig** | The agent's login file for the cluster (read-only). Secret. |
| **Least privilege** | Give only the exact access needed, nothing more. |
| **Loki** | Stores logs (like a database for log lines). |
| **LogQL** | The query language for Loki (e.g. `{service_name=~".+"}`). |
| **mTLS** | Both sides prove identity with certificates (strongest auth). |
| **OTel / OpenTelemetry** | Open standard for collecting metrics, logs, traces. |
| **Prometheus** | Stores metrics (numbers over time) and runs alert rules. |
| **PromQL** | The query language for Prometheus (e.g. `system_memory_usage_bytes`). |
| **Policy** | The agent's safety gate (`policy.py`) that allows/denies each request. |
| **Proxy (L4)** | Passes network traffic through untouched (keeps the secure login intact). |
| **RBAC** | Kubernetes permissions. Ours grants read-only, never secrets. |
| **remote-write** | How the collector pushes metrics into Prometheus. |
| **Redaction** | Masking secret-looking text (passwords, tokens) before it's shown. |
| **Stat panel** | A Grafana panel showing one big number (e.g. "VMs: 4"). |
| **Three walls** | policy → executor → RBAC: three independent checks before anything runs. |
| **Token** | The shared password the bastion sends to the agent. |
| **Vault (OpenBao)** | A secure store for secrets + certificates (planned). |
