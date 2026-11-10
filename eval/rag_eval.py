#!/usr/bin/env python3
"""RAG eval exporter — a 'metrics-server for the RAG'.

Replays a fixed eval set (question -> expected tools) through the LIVE RAG store and
computes deterministic quality KPIs (hit-rate, precision, recall, latency). No second
model, no LLM judge -> cheap and air-gap-safe. Reuses the app's own RAG code so the
eval sees exactly what /investigate sees.

Two modes:
  python -m eval.rag_eval --print          one-shot: print the numbers + Prometheus text
  python -m eval.rag_eval --serve 9108     expose GET /metrics on :9108 for Prometheus

Env (same as the agent): OLLAMA_URL, OLLAMA_MODEL, AGENT_CONFIG (default /app/config.yaml).
Runs happily inside the agent image (it has the deps + sqlite-vec + the RAG store).
"""

import os
import sys
import time
import yaml

from app.llm import OllamaClient
from app.memory import RagMemory
from app.memory_store import SqliteVecStore


def _load_cfg() -> dict:
    with open(os.environ.get("AGENT_CONFIG", "/app/config.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _build_memory(cfg: dict) -> RagMemory:
    rc = cfg.get("rag", {})
    inv = cfg.get("investigation", {})
    url = os.environ.get("OLLAMA_URL", cfg["ollama"]["url"])
    model = os.environ.get("OLLAMA_MODEL", cfg["ollama"]["model"])
    llm = OllamaClient(url, model, cfg["ollama"]["timeout_seconds"], "",
                       embed_model=rc.get("embed_model", "nomic-embed-text"))
    store = SqliteVecStore(rc.get("db_path", "/app/memory/rag.db"),
                           dim=int(rc.get("embed_dim", 768)))
    et = int(rc.get("embed_timeout_seconds", 20))
    return RagMemory(
        embed_fn=lambda q: llm.embed(q, timeout=et),
        store=store,
        allowed_tools=inv.get("tools", []),
        top_k=int(rc.get("top_k", 3)),
        min_similarity=float(rc.get("min_similarity", 0.75)),
        max_rows=int(rc.get("max_rows", 5000)),
    )


def _load_eval_set() -> list[dict]:
    path = os.environ.get("RAG_EVAL_SET",
                          os.path.join(os.path.dirname(__file__), "rag_eval_set.yaml"))
    with open(path, encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("cases", [])


def evaluate(memory: RagMemory, cases: list[dict]) -> dict:
    """Return aggregate KPIs over the eval set. Pure comparison, no model judgement."""
    n = len(cases)
    hits = 0                 # recall returned at least one EXPECTED tool
    prec_sum = 0.0           # |got ∩ expect| / |got|
    recall_sum = 0.0         # |got ∩ expect| / |expect|
    lat_sum = 0.0
    errors = 0
    for c in cases:
        q = c.get("q", "")
        expect = set(c.get("expect", []))
        t0 = time.monotonic()
        try:
            got = set(memory.recall(q))      # fail-open: returns [] on any error
        except Exception:
            errors += 1
            got = set()
        lat_sum += time.monotonic() - t0
        inter = got & expect
        if inter:
            hits += 1
        prec_sum += (len(inter) / len(got)) if got else 0.0
        recall_sum += (len(inter) / len(expect)) if expect else 0.0
    denom = n or 1
    return {
        "cases": n,
        "hit_rate": hits / denom,
        "precision": prec_sum / denom,
        "recall": recall_sum / denom,
        "avg_latency_seconds": lat_sum / denom,
        "errors": errors,
    }


def prometheus_text(kpi: dict) -> str:
    """Render KPIs in Prometheus exposition format."""
    lines = [
        "# HELP rag_eval_hit_rate Fraction of eval questions that recalled an expected tool.",
        "# TYPE rag_eval_hit_rate gauge",
        f"rag_eval_hit_rate {kpi['hit_rate']:.4f}",
        "# HELP rag_eval_precision Mean precision of recalled tools vs expected.",
        "# TYPE rag_eval_precision gauge",
        f"rag_eval_precision {kpi['precision']:.4f}",
        "# HELP rag_eval_recall Mean recall of expected tools.",
        "# TYPE rag_eval_recall gauge",
        f"rag_eval_recall {kpi['recall']:.4f}",
        "# HELP rag_eval_avg_latency_seconds Mean recall() latency per question.",
        "# TYPE rag_eval_avg_latency_seconds gauge",
        f"rag_eval_avg_latency_seconds {kpi['avg_latency_seconds']:.4f}",
        "# HELP rag_eval_cases Number of eval questions.",
        "# TYPE rag_eval_cases gauge",
        f"rag_eval_cases {kpi['cases']}",
        "# HELP rag_eval_errors Recall calls that raised during eval.",
        "# TYPE rag_eval_errors gauge",
        f"rag_eval_errors {kpi['errors']}",
    ]
    return "\n".join(lines) + "\n"


def _serve(port: int) -> None:
    from http.server import BaseHTTPRequestHandler, HTTPServer
    cfg = _load_cfg()
    cases = _load_eval_set()
    memory = _build_memory(cfg)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/metrics", "/"):
                self.send_response(404); self.end_headers(); return
            # Recompute on each scrape; the eval set is tiny so this is cheap.
            body = prometheus_text(evaluate(memory, cases)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):   # quiet
            pass

    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


def main() -> None:
    if "--serve" in sys.argv:
        i = sys.argv.index("--serve")
        port = int(sys.argv[i + 1]) if i + 1 < len(sys.argv) else 9108
        _serve(port)
        return
    kpi = evaluate(_build_memory(_load_cfg()), _load_eval_set())
    print(f"cases={kpi['cases']} hit_rate={kpi['hit_rate']:.2f} "
          f"precision={kpi['precision']:.2f} recall={kpi['recall']:.2f} "
          f"avg_latency={kpi['avg_latency_seconds']:.2f}s errors={kpi['errors']}")
    print("---")
    print(prometheus_text(kpi), end="")


if __name__ == "__main__":
    main()
