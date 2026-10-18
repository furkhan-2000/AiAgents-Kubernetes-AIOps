"""FastAPI app. One endpoint: POST /query. Runs the pipeline:
auth -> llm (Ollama) -> policy (allowlist) -> executor (kubectl) -> audit."""

import os
import yaml
from fastapi import FastAPI, Depends, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from .schemas import QueryRequest, QueryResponse
from .auth import verify
from .llm import OllamaClient, LLMError
from .policy import Policy
from .executor import Executor
from .audit import audit


def _load_config() -> dict:
    path = os.environ.get("AGENT_CONFIG", "/app/config.yaml")
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


cfg = _load_config()

# Environment variables override config values.
ollama_url = os.environ.get("OLLAMA_URL", cfg["ollama"]["url"])
ollama_model = os.environ.get("OLLAMA_MODEL", cfg["ollama"]["model"])

llm = OllamaClient(ollama_url, ollama_model, cfg["ollama"]["timeout_seconds"])
policy = Policy(cfg["allowlist"])
executor = Executor(cfg["kubernetes"]["kubeconfig"], cfg["kubernetes"]["default_namespace"])

app = FastAPI(title="K8s AI Agent", docs_url=None, redoc_url=None, openapi_url=None)

# Rate-limit backstop (per caller IP). The primary limit should be at Nginx;
# this protects the agent and Ollama even if traffic bypasses the proxy.
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


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

    # 3. Execute the approved, read-only action.
    output = executor.run(action)
    audit("action.executed", caller=caller, action=action.model_dump())

    return QueryResponse(allowed=True, action=action, output=output)
