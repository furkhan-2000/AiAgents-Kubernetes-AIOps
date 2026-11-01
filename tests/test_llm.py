"""LLM client: JSON parsing is strict; bad model output never crashes the app."""

import json
import types
import pytest
import httpx
from app.llm import OllamaClient, LLMError


def _fake_post(response_text):
    def post(url, json=None, timeout=None):
        return types.SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"response": response_text},
        )
    return post


def test_interpret_parses_valid(monkeypatch):
    monkeypatch.setattr(httpx, "post",
        _fake_post(json.dumps({"verb": "get", "resource": "pods", "namespace": "ns"})))
    c = OllamaClient("http://x", "m", 5)
    a = c.interpret("how many pods")
    assert a.verb == "get" and a.resource == "pods"


@pytest.mark.parametrize("bad", ["not json", "[]", "{}", '{"verb":"get"}'])
def test_interpret_bad_output_raises_llmerror(monkeypatch, bad):
    monkeypatch.setattr(httpx, "post", _fake_post(bad))
    c = OllamaClient("http://x", "m", 5)
    with pytest.raises(LLMError):
        c.interpret("q")


def test_interpret_http_error_raises_llmerror(monkeypatch):
    def boom(url, json=None, timeout=None):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(httpx, "post", boom)
    c = OllamaClient("http://x", "m", 5)
    with pytest.raises(LLMError):
        c.interpret("q")


def test_next_step_parses_toolcall(monkeypatch):
    monkeypatch.setattr(httpx, "post",
        _fake_post(json.dumps({"tool": "list_pods", "namespace": "ns"})))
    c = OllamaClient("http://x", "m", 5, brain_prompt="rules")
    call = c.next_step("q", {"list_pods": "desc"}, "")
    assert call.tool == "list_pods" and call.namespace == "ns"


def test_next_step_ignores_unknown_keys(monkeypatch):
    monkeypatch.setattr(httpx, "post",
        _fake_post(json.dumps({"tool": "final", "evil": "rm -rf", "verb": "delete"})))
    c = OllamaClient("http://x", "m", 5, brain_prompt="rules")
    call = c.next_step("q", {}, "")
    assert call.tool == "final"
    assert not hasattr(call, "evil")


def test_next_step_passes_through_container(monkeypatch):
    monkeypatch.setattr(httpx, "post",
        _fake_post(json.dumps({"tool": "get_logs", "name": "p", "container": "app"})))
    c = OllamaClient("http://x", "m", 5, brain_prompt="rules")
    call = c.next_step("q", {}, "")
    assert call.container == "app"   # the loop can target a specific container


def test_synthesize_returns_text(monkeypatch):
    monkeypatch.setattr(httpx, "post", _fake_post("The pod is healthy."))
    c = OllamaClient("http://x", "m", 5, brain_prompt="rules")
    assert "healthy" in c.synthesize("q", "evidence")
