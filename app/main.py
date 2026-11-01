"""FastAPI app. One endpoint: POST /query. Runs the pipeline:
auth -> llm (Ollama) -> policy (allowlist) -> executor (kubectl) -> audit."""

import os
import threading
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

llm = OllamaClient(ollama_url, ollama_model, cfg["ollama"]["timeout_seconds"], brain_prompt)
policy = Policy(cfg["allowlist"], _inv_cfg.get("tools", []), cfg.get("allowed_namespaces", []))
executor = Executor(cfg["kubernetes"]["kubeconfig"], cfg["kubernetes"]["default_namespace"])
loop = InvestigateLoop(llm, policy, executor, cfg["kubernetes"]["default_namespace"], _inv_cfg)

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
    try:
        action = llm.interpret(req.q)
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
    audit("action.executed", caller=caller, action=action.model_dump())

    return QueryResponse(allowed=True, action=action, output=output)


@app.post("/investigate", response_model=InvestigateResponse)
@limiter.limit("6/minute")
def investigate(request: Request, req: InvestigateRequest, caller: str = Depends(verify)) -> InvestigateResponse:
    """Conversational, multi-step, read-only investigation. The brain runs several
    read-only tools, then explains in plain English. It never changes the cluster."""
    audit("investigate.received", caller=caller, question=req.q)
    if not _inflight.acquire(blocking=False):
        audit("investigate.busy", caller=caller)
        return InvestigateResponse(
            allowed=False,
            message="The assistant is busy with other investigations. Please retry shortly.",
        )
    try:
        return loop.run(req.q, caller)
    finally:
        _inflight.release()
