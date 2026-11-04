"""FastAPI app. Routes: /healthz, POST /query, POST /investigate. Pipeline:
auth -> llm (Ollama) -> policy (allowlist) -> executor (kubectl) -> audit."""

import os
import threading
import time
import yaml
from fastapi import FastAPI, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from .schemas import QueryRequest, QueryResponse, InvestigateRequest, InvestigateResponse
from .auth import verify
from .llm import OllamaClient, LLMError
from .policy import Policy
from .executor import Executor
from .agent_loop import InvestigateLoop
from .audit import audit
from .redact import redact
from .counts import table_facts


def _load_config() -> dict:
    path = os.environ.get("AGENT_CONFIG", "/app/config.yaml")
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _load_brain_prompt(cfg: dict) -> str:
    """Load the strict brain system prompt (OLLAMA_BRAIN.md). Missing file is fatal:
    we never run the loop without its anti-hallucination rules."""
    path = os.environ.get(
        "AGENT_BRAIN", cfg.get("brain", {}).get("system_prompt_file", "/app/OLLAMA_BRAIN.md")
    )
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


cfg = _load_config()

# Environment variables override config values.
ollama_url = os.environ.get("OLLAMA_URL", cfg["ollama"]["url"])
ollama_model = os.environ.get("OLLAMA_MODEL", cfg["ollama"]["model"])

brain_prompt = _load_brain_prompt(cfg)
_inv_cfg = cfg.get("investigation", {})

_rag_cfg = cfg.get("rag", {})
llm = OllamaClient(ollama_url, ollama_model, cfg["ollama"]["timeout_seconds"], brain_prompt,
                   embed_model=_rag_cfg.get("embed_model", "nomic-embed-text"))
policy = Policy(cfg["allowlist"], _inv_cfg.get("tools", []), cfg.get("allowed_namespaces", []))
executor = Executor(cfg["kubernetes"]["kubeconfig"], cfg["kubernetes"]["default_namespace"])


def _build_memory():
    """Build RAG memory, fail-open: any error (missing extension, bad path) -> None,
    and the agent runs exactly as if RAG were off."""
    if not _rag_cfg.get("enabled", False):
        return None
    try:
        from .memory import RagMemory
        from .memory_store import SqliteVecStore
        store = SqliteVecStore(_rag_cfg.get("db_path", "/app/memory/rag.db"),
                               dim=int(_rag_cfg.get("embed_dim", 768)))
        # Bounded embed timeout so a slow embed model can never stall /investigate.
        et = int(_rag_cfg.get("embed_timeout_seconds", 20))
        rag = RagMemory(
            embed_fn=lambda q: llm.embed(q, timeout=et),
            store=store,
            allowed_tools=_inv_cfg.get("tools", []),
            top_k=int(_rag_cfg.get("top_k", 3)),
            min_similarity=float(_rag_cfg.get("min_similarity", 0.75)),
            max_rows=int(_rag_cfg.get("max_rows", 5000)),
        )
        # Pre-seed once when the store is empty, so RAG helps from the first query.
        try:
            if store.count() == 0:
                for item in (_rag_cfg.get("seed") or []):
                    q, tools = item.get("q"), item.get("tools")
                    if q and tools:
                        rag.remember(q, tools)
                audit("rag.seeded", rows=store.count())
        except Exception as exc:
            audit("rag.seed_error", error=str(exc))
        audit("rag.enabled", db=_rag_cfg.get("db_path", "/app/memory/rag.db"))
        return rag
    except Exception as exc:
        audit("rag.disabled", error=str(exc))
        return None


memory = _build_memory()
loop = InvestigateLoop(llm, policy, executor, cfg["kubernetes"]["default_namespace"],
                       {**_inv_cfg, "call_cap_seconds": cfg["ollama"]["timeout_seconds"]},
                       memory=memory)

app = FastAPI(title="K8s AI Agent", docs_url=None, redoc_url=None, openapi_url=None)

# Rate-limit backstop (per caller IP). The primary limit should be at Nginx;
# this protects the agent and Ollama even if traffic bypasses the proxy.
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Global cap on concurrent investigations. Each /investigate can hold a worker for
# the whole loop budget; without a cap, a handful of concurrent requests (even from
# several IPs, which the per-IP rate limit does not aggregate) could exhaust the
# threadpool and starve /healthz and /query. Non-blocking: over the cap -> 429.
_MAX_INFLIGHT = int(_inv_cfg.get("max_concurrent", 2))
_inflight = threading.BoundedSemaphore(_MAX_INFLIGHT)

# Short-TTL cache for identical investigate questions. Kept SHORT on purpose:
# cluster state changes, so a long cache could hide a pod that just broke.
# 0 disables it.
_CACHE_TTL = int(_inv_cfg.get("cache_ttl_seconds", 30))
_cache: dict[str, tuple[float, InvestigateResponse]] = {}
_cache_lock = threading.Lock()


def _cache_get(key: str):
    if _CACHE_TTL <= 0:
        return None
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] <= _CACHE_TTL:
            return hit[1]
        if hit:
            _cache.pop(key, None)   # expired
    return None


def _cache_put(key: str, resp: InvestigateResponse) -> None:
    if _CACHE_TTL <= 0:
        return
    with _cache_lock:
        # Bound the cache so it cannot grow without limit.
        if len(_cache) > 256:
            _cache.clear()
        _cache[key] = (time.monotonic(), resp)

# Bounded timeout for the single /query LLM call (keeps a /query from holding a
# worker for the full Ollama budget and contending with /investigate).
_QUERY_TIMEOUT = int(cfg["ollama"].get("query_timeout_seconds", 90))


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    # Friendly message instead of the raw pydantic error list (still HTTP 422).
    return JSONResponse(
        status_code=422,
        content={"allowed": False,
                 "message": "Your question is empty or too long. Please send a short, "
                            "plain question about the cluster."},
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
@limiter.limit("10/minute")
def query(request: Request, req: QueryRequest, caller: str = Depends(verify)) -> QueryResponse:
    audit("request.received", caller=caller, question=req.q)

    # 1. LLM interprets the question into a structured action (suggestion only).
    # Bounded timeout so a /query cannot hold a worker for the full Ollama budget.
    try:
        action = llm.interpret(req.q, timeout=_QUERY_TIMEOUT)
    except LLMError as exc:
        audit("llm.error", caller=caller, error=str(exc))
        return QueryResponse(allowed=False, message="could not understand the request")

    audit("llm.action", caller=caller, action=action.model_dump())

    # 2. Policy decides. The LLM's suggestion means nothing until it passes here.
    allowed, reason = policy.check(action)
    if not allowed:
        audit("policy.denied", caller=caller, action=action.model_dump(), reason=reason)
        return QueryResponse(allowed=False, action=action, message=reason)

    # 3. Execute the approved, read-only action. Redact secret-looking output on
    # this path too (same as the investigate loop).
    output = redact(executor.run(action))
    fact = table_facts(output)
    if fact:
        output = f"[EXACT COUNT: {fact}]\n{output}"
    audit("action.executed", caller=caller, action=action.model_dump())

    return QueryResponse(allowed=True, action=action, output=output)


@app.post("/investigate", response_model=InvestigateResponse)
@limiter.limit("6/minute")
def investigate(request: Request, req: InvestigateRequest, caller: str = Depends(verify)) -> InvestigateResponse:
    """Conversational, multi-step, read-only investigation. The brain runs several
    read-only tools, then explains in plain English. It never changes the cluster."""
    audit("investigate.received", caller=caller, question=req.q)

    key = req.q.strip().lower()
    cached = _cache_get(key)
    if cached is not None:
        audit("investigate.cache_hit", caller=caller)
        return cached

    if not _inflight.acquire(blocking=False):
        audit("investigate.busy", caller=caller)
        return InvestigateResponse(
            allowed=False,
            message="The assistant is busy with other investigations. Please retry shortly.",
        )
    try:
        resp = loop.run(req.q, caller)
        _cache_put(key, resp)
        return resp
    finally:
        _inflight.release()
