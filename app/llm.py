"""Talks to Ollama. The LLM only SUGGESTS; policy.py and the executor decide what
actually runs. Two modes:
  - interpret(): single-command mode for /query (returns one Action).
  - next_step() / synthesize(): the investigate loop for /investigate.
The brain system prompt for the loop is loaded from OLLAMA_BRAIN.md."""

import json
import httpx
from pydantic import ValidationError
from .schemas import Action, ToolCall

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
    def __init__(self, url: str, model: str, timeout: int, brain_prompt: str = ""):
        self._url = url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._brain = brain_prompt  # loaded from OLLAMA_BRAIN.md for the loop

    # ----- low-level call ------------------------------------------------------

    def _generate(self, system: str, prompt: str, as_json: bool) -> str:
        payload = {
            "model": self._model,
            "system": system,
            "prompt": prompt,
            "stream": False,
            # Low temperature keeps the model grounded and reduces invention.
            "options": {"temperature": 0.1},
        }
        if as_json:
            payload["format"] = "json"
        try:
            resp = httpx.post(f"{self._url}/api/generate", json=payload, timeout=self._timeout)
            resp.raise_for_status()
            return resp.json().get("response", "")
        except httpx.HTTPError as exc:
            raise LLMError(f"ollama call failed: {exc}") from exc

    # ----- single-command mode (/query) ---------------------------------------

    def interpret(self, question: str) -> Action:
        """Return a validated Action parsed from the model's JSON reply."""
        raw = self._generate(_SYSTEM, question, as_json=True)
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise LLMError("model did not return a JSON object")
            return Action(**{k: data.get(k) for k in ("verb", "resource", "namespace", "name")})
        except (json.JSONDecodeError, ValueError, ValidationError) as exc:
            raise LLMError(f"could not interpret question: {exc}") from exc

    # ----- investigate loop (/investigate) ------------------------------------

    def next_step(self, question: str, catalog: dict[str, str], history: str) -> ToolCall:
        """Ask the brain for the next read-only tool to run (or 'final' to stop)."""
        tools_text = "\n".join(f"- {name}: {desc}" for name, desc in catalog.items())
        prompt = (
            f"User question:\n{question}\n\n"
            f"Tools you may call (names only):\n{tools_text}\n\n"
            f"What you have gathered so far:\n{history or '(nothing yet)'}\n\n"
            "Reply with ONLY the JSON for your next tool call, or {\"tool\":\"final\"} "
            "if you have enough evidence to answer."
        )
        raw = self._generate(self._brain, prompt, as_json=True)
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise LLMError("model did not return a JSON object")
            allowed_keys = ("tool", "namespace", "name", "tail_lines", "previous", "reason")
            return ToolCall(**{k: data.get(k) for k in allowed_keys})
        except (json.JSONDecodeError, ValueError, ValidationError) as exc:
            raise LLMError(f"could not plan next step: {exc}") from exc

    def synthesize(self, question: str, history: str) -> str:
        """Ask the brain for the final plain-English answer, grounded in the evidence."""
        prompt = (
            f"User question:\n{question}\n\n"
            f"Evidence you gathered (this is the ONLY source of truth):\n{history}\n\n"
            "Write the final answer now, following your rules: what is happening, "
            "the most likely cause clearly labelled as likely, and a suggested next "
            "step for a human. Base every statement only on the evidence above. If "
            "the evidence is insufficient, say exactly what was missing. Be concise."
        )
        # Plain text, not JSON, for the human-facing explanation.
        return self._generate(self._brain, prompt, as_json=False).strip()
