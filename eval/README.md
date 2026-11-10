# RAG eval metrics — a "metrics-server for the RAG"

Replays a fixed eval set (`rag_eval_set.yaml`: question → expected tools) through the
LIVE RAG store and exposes quality KPIs for Prometheus/Grafana. Deterministic, no LLM
judge, air-gap-safe. See `docs/10-rag.md` for the concept.

Metrics exposed on `/metrics`:
- `rag_eval_hit_rate` — fraction of questions that recalled an expected tool (↑ better)
- `rag_eval_precision` — recalled tools that were expected (↑ = less noise)
- `rag_eval_recall` — expected tools that were recalled
- `rag_eval_avg_latency_seconds` — mean recall() time
- `rag_eval_cases`, `rag_eval_errors`

## Quick check (no monitoring, just see the numbers)
On ai-dev — run it inside the agent image (has the deps + the RAG store):
```bash
docker run --rm --network AI-Internal \
  -e OLLAMA_URL=http://ollama:11434 \
  -v agent_memory:/app/memory \
  -v "$PWD/eval:/app/eval:ro" \
  -v "$PWD/config.yaml:/app/config.yaml:ro" \
  agent:1.0 python -m eval.rag_eval --print
# -> cases=12 hit_rate=0.92 precision=0.80 recall=0.88 avg_latency=0.15s errors=0
```

## Live monitoring (exporter + existing OTel path — NO new firewall rule)

### 1. Run the exporter as a sidecar (ai-dev)
Add to `docker-compose.override.yml` (copy to repo root on ai-dev):
```yaml
services:
  rag-eval:
    image: agent:1.0                       # reuse the agent image (deps + sqlite-vec)
    container_name: rag-eval
    restart: unless-stopped
    command: ["python", "-m", "eval.rag_eval", "--serve", "9108"]
    environment:
      OLLAMA_URL: "http://ollama:11434"
      OLLAMA_MODEL: "qwen2.5:7b-instruct-q5_K_M"
    networks: [AI-Internal]                 # reach Ollama for embeddings
    volumes:
      - "agent_memory:/app/memory"          # the live RAG store (read)
      - "./eval:/app/eval:ro"               # the eval code + eval set
      - "./config.yaml:/app/config.yaml:ro"
    ports:
      - "127.0.0.1:9108:9108"               # localhost only; OTel scrapes it locally
    user: "1000:1000"
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    mem_limit: 256m
```
```bash
docker compose up -d rag-eval
curl -s http://127.0.0.1:9108/metrics | grep rag_eval_hit_rate   # sanity
```

### 2. Let the ai-dev OTel collector scrape it → ships via the existing remote-write
In `/etc/otelcol-contrib/config.yaml` on ai-dev, add a prometheus receiver and put it in
the metrics pipeline (reuses the prometheusremotewrite exporter you already have):
```yaml
receivers:
  prometheus:
    config:
      scrape_configs:
        - job_name: rag-eval
          scrape_interval: 60s
          static_configs:
            - targets: ["127.0.0.1:9108"]
# ...
service:
  pipelines:
    metrics:
      receivers: [hostmetrics, docker_stats, prometheus]   # <-- add prometheus
      processors: [resourcedetection, batch]
      exporters: [prometheusremotewrite]
```
```bash
sudo systemctl restart otelcol-contrib
```
No inbound port is opened on ai-dev — the collector scrapes localhost and pushes out
through the channel that already carries host/container metrics. Air-gap intact.

### 3. Grafana panels (monitoring VM)
Prometheus data source, queries:
| Panel | Query |
|-------|-------|
| RAG hit-rate | `rag_eval_hit_rate` |
| RAG precision | `rag_eval_precision` |
| RAG recall | `rag_eval_recall` |
| RAG eval latency | `rag_eval_avg_latency_seconds` |
Set hit-rate panel thresholds: red < 0.7, green ≥ 0.85. Alert if `rag_eval_hit_rate < 0.7`.

## Maintaining it
- Grow `rag_eval_set.yaml` as you add scenarios — more cases = a sharper KPI.
- Every `expect` tool must be a real allowlist name (`app/tools.py`).
- Run the quick check after any RAG/config change to catch a quality regression.
