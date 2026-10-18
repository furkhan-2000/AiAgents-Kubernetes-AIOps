"""Talks to Ollama. Sends the user question with a strict system prompt and gets
back a JSON action. The LLM only SUGGESTS; policy.py decides if it is allowed."""

import json
import httpx
from pydantic import ValidationError
from .schemas import Action

_SYSTEM = (
    "You translate a user's question about a Kubernetes cluster into a single "
    "JSON action. Reply with ONLY a JSON object, no prose, with keys: "
    "verb, resource, namespace (optional), name (optional). "
    "verb must be one of: get, describe, logs, top. "
    "resource examples: pods, nodes, services, deployments, namespaces. "
    "Never invent destructive actions. If unsure, use verb=get resource=pods."
)


class LLMError(Exception):
    pass


class OllamaClient:
    def __init__(self, url: str, model: str, timeout: int):
        self._url = url.rstrip("/")
        self._model = model
        self._timeout = timeout

    def interpret(self, question: str) -> Action:
        """Return a validated Action parsed from the model's JSON reply."""
        payload = {
            "model": self._model,
            "system": _SYSTEM,
            "prompt": question,
            "format": "json",   # ask Ollama to return strict JSON
            "stream": False,
        }
        try:
            resp = httpx.post(f"{self._url}/api/generate", json=payload, timeout=self._timeout)
            resp.raise_for_status()
            raw = resp.json().get("response", "")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise LLMError("model did not return a JSON object")
            # Pydantic validates the shape; anything unexpected is rejected here.
            return Action(**{k: data.get(k) for k in ("verb", "resource", "namespace", "name")})
        except (httpx.HTTPError, json.JSONDecodeError, ValueError, ValidationError) as exc:
            raise LLMError(f"could not interpret question: {exc}") from exc
