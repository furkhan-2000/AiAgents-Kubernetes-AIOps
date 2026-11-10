"""Integration tests through the real FastAPI app (routing, auth, validation,
pipeline wiring). The LLM and executor are patched so no Ollama/cluster is needed."""

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.schemas import Action, InvestigateResponse, Step

AUTH = {"Authorization": "Bearer testtoken", "Content-Type": "application/json"}


@pytest.fixture
def client():
    return TestClient(main.app)


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_query_requires_auth(client):
    r = client.post("/query", json={"q": "how many pods"})
    assert r.status_code in (401, 403)


def test_query_rejects_wrong_token(client):
    r = client.post("/query", json={"q": "x"},
                    headers={"Authorization": "Bearer nope", "Content-Type": "application/json"})
    assert r.status_code == 403


def test_query_oversized_is_422(client):
    r = client.post("/query", json={"q": "x" * 999}, headers=AUTH)
    assert r.status_code == 422


def test_query_happy_path(client, monkeypatch):
    monkeypatch.setattr(main.llm, "interpret",
        lambda q, timeout=None: Action(verb="get", resource="pods", namespace="kube-system"))
    monkeypatch.setattr(main.executor, "run", lambda a: "NAME READY\npod 1/1")
    r = client.post("/query", json={"q": "pods in kube-system"}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["allowed"] is True and "pod" in body["output"]


def test_query_denied_action(client, monkeypatch):
    # LLM suggests a mutating action; policy must deny it end to end.
    monkeypatch.setattr(main.llm, "interpret",
        lambda q, timeout=None: Action(verb="delete", resource="pods"))
    r = client.post("/query", json={"q": "delete pods"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["allowed"] is False


def test_query_llm_failure_is_graceful(client, monkeypatch):
    from app.llm import LLMError
    def boom(q, timeout=None): raise LLMError("ollama down")
    monkeypatch.setattr(main.llm, "interpret", boom)
    r = client.post("/query", json={"q": "x"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["allowed"] is False


def test_investigate_requires_auth(client):
    r = client.post("/investigate", json={"q": "why broken"})
    assert r.status_code in (401, 403)


def test_investigate_happy_path(client, monkeypatch):
    fake = InvestigateResponse(
        allowed=True, answer="All pods healthy.",
        steps=[Step(tool="list_all_pods", args={}, observation="pod 1/1")],
    )
    monkeypatch.setattr(main.loop, "run", lambda q, caller: fake)
    r = client.post("/investigate", json={"q": "are all pods up"}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "All pods healthy." and body["steps"][0]["tool"] == "list_all_pods"


def test_investigate_oversized_is_422(client):
    r = client.post("/investigate", json={"q": "x" * 1001}, headers=AUTH)
    assert r.status_code == 422


# ---- /ask: the LLM router decides the path -----------------------------------

def test_ask_routes_to_query(client, monkeypatch):
    monkeypatch.setattr(main.llm, "route", lambda q, timeout=None: "query")
    monkeypatch.setattr(main.llm, "interpret",
        lambda q, timeout=None: Action(verb="get", resource="pods", namespace="kube-system"))
    monkeypatch.setattr(main.executor, "run", lambda a: "NAME READY\npod 1/1")
    r = client.post("/ask", json={"q": "how many pods in kube-system"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["allowed"] is True and "pod" in r.json()["output"]


def test_ask_routes_to_investigate(client, monkeypatch):
    fake = InvestigateResponse(allowed=True, answer="Routed to investigate.", steps=[])
    monkeypatch.setattr(main.llm, "route", lambda q, timeout=None: "investigate")
    monkeypatch.setattr(main.loop, "run", lambda q, caller: fake)
    r = client.post("/ask", json={"q": "why is the web pod restarting"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["answer"] == "Routed to investigate."


def test_ask_query_route_falls_back_to_investigate(client, monkeypatch):
    """Router said 'query' but the model can't map it (e.g. 'what k8s version').
    The /ask handler must self-correct to the investigate loop, not refuse."""
    monkeypatch.setattr(main.llm, "route", lambda q, timeout=None: "query")
    from app.llm import LLMError
    def cant(q, timeout=None): raise LLMError("could not interpret")
    monkeypatch.setattr(main.llm, "interpret", cant)
    called = {}
    def fake_loop(q, caller):
        called["yes"] = True
        return InvestigateResponse(allowed=True, answer="fell back", steps=[])
    monkeypatch.setattr(main.loop, "run", fake_loop)
    r = client.post("/ask", json={"q": "what kubernetes version"}, headers=AUTH)
    assert r.status_code == 200 and called.get("yes") and r.json()["answer"] == "fell back"


def test_ask_router_error_defaults_to_investigate(client, monkeypatch):
    from app.llm import LLMError
    def boom(q, timeout=None): raise LLMError("router down")
    monkeypatch.setattr(main.llm, "route", boom)
    fake = InvestigateResponse(allowed=True, answer="default path", steps=[])
    monkeypatch.setattr(main.loop, "run", lambda q, caller: fake)
    r = client.post("/ask", json={"q": "anything"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["answer"] == "default path"


def test_ask_requires_auth(client):
    assert client.post("/ask", json={"q": "x"}).status_code in (401, 403)


def test_investigate_end_to_end_real_loop(client, monkeypatch):
    """Exercises the REAL loop + policy + executor read-only + redaction on the HTTP
    path (only Ollama and kubectl are mocked), not a stubbed loop.run."""
    import subprocess, types
    from app.schemas import ToolCall

    seq = [ToolCall(tool="list_all_pods"), ToolCall(tool="final")]
    monkeypatch.setattr(main.llm, "next_step", lambda q, c, h, timeout=None: seq.pop(0))
    monkeypatch.setattr(main.llm, "synthesize", lambda q, h, timeout=None: "All pods healthy. token: zzz")

    def fake_run(argv, **kw):
        # assert the real executor ran a read-only verb with no shell
        assert kw.get("shell", False) is False and argv[0] == "kubectl"
        assert "delete" not in argv and "patch" not in argv
        return types.SimpleNamespace(returncode=0, stdout="ns pod 1/1 Running", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    r = client.post("/investigate", json={"q": "are all pods up"}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["allowed"] is True and body["steps"][0]["tool"] == "list_all_pods"
    assert "zzz" not in body["answer"]   # synthesized secret-looking text redacted? (answer not redacted; see note)


def test_investigate_denies_mutating_tool_end_to_end(client, monkeypatch):
    from app.schemas import ToolCall
    seq = [ToolCall(tool="delete_pod", name="x"), ToolCall(tool="final")]
    monkeypatch.setattr(main.llm, "next_step", lambda q, c, h, timeout=None: seq.pop(0))
    monkeypatch.setattr(main.llm, "synthesize", lambda q, h, timeout=None: "done")
    import subprocess, types
    ran = {"n": 0}
    def fake_run(argv, **kw):
        ran["n"] += 1
        return types.SimpleNamespace(returncode=0, stdout="x", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    r = client.post("/investigate", json={"q": "delete the pod"}, headers=AUTH)
    assert r.status_code == 200 and ran["n"] == 0   # mutating tool never executed


def test_investigate_cache_returns_same_without_rerun(client, monkeypatch):
    if main._CACHE_TTL <= 0:
        pytest.skip("cache disabled")
    main._cache.clear()
    from app.schemas import ToolCall
    calls = {"plan": 0, "synth": 0}
    seq = {"q": None}
    def planner(q, c, h, timeout=None):
        calls["plan"] += 1
        return ToolCall(tool="list_all_pods") if calls["plan"] == 1 else ToolCall(tool="final")
    def synth(q, h, timeout=None):
        calls["synth"] += 1
        return "cached answer test"
    monkeypatch.setattr(main.llm, "next_step", planner)
    monkeypatch.setattr(main.llm, "synthesize", synth)
    import subprocess, types
    monkeypatch.setattr(subprocess, "run",
        lambda a, **k: types.SimpleNamespace(returncode=0, stdout="x", stderr=""))
    q = {"q": "cache probe question"}
    r1 = client.post("/investigate", json=q, headers=AUTH)
    r2 = client.post("/investigate", json=q, headers=AUTH)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json() == r2.json()
    # second call served from cache: synthesis ran only once (not re-invoked)
    assert calls["synth"] == 1
    main._cache.clear()


def test_no_openapi_exposed(client):
    # docs/openapi disabled to reduce surface.
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404


def test_query_out_of_scope_not_answered(client, monkeypatch):
    # chit-chat: interpret returns the out-of-scope sentinel -> denied, no kubectl.
    monkeypatch.setattr(main.llm, "interpret",
        lambda q, timeout=None: Action(verb="none", resource="none"))
    ran = {"called": False}
    monkeypatch.setattr(main.executor, "run", lambda a: ran.__setitem__("called", True) or "x")
    r = client.post("/query", json={"q": "hello there"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["allowed"] is False
    assert ran["called"] is False   # never touched the cluster


def test_query_describe_deployment_allowed(client, monkeypatch):
    monkeypatch.setattr(main.llm, "interpret",
        lambda q, timeout=None: Action(verb="describe", resource="deployment", name="payments", namespace="prod"))
    monkeypatch.setattr(main.executor, "run", lambda a: "Name: payments")
    r = client.post("/query", json={"q": "describe deployment payments"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["allowed"] is True


@pytest.mark.parametrize("header", ["Token abc", "bearer testtoken", "Basic x", "testtoken"])
def test_malformed_auth_header_rejected(client, header):
    r = client.post("/query", json={"q": "x"},
                    headers={"Authorization": header, "Content-Type": "application/json"})
    assert r.status_code in (401, 403)


def test_query_output_is_redacted(client, monkeypatch):
    monkeypatch.setattr(main.llm, "interpret",
        lambda q, timeout=None: Action(verb="describe", resource="pod", name="p", namespace="app"))
    monkeypatch.setattr(main.executor, "run", lambda a: "password: hunter2")
    r = client.post("/query", json={"q": "describe pod p"}, headers=AUTH)
    assert "hunter2" not in r.json()["output"] and "[REDACTED]" in r.json()["output"]


def test_validation_returns_friendly_message(client):
    r = client.post("/query", json={"q": "x" * 999}, headers=AUTH)
    assert r.status_code == 422
    assert "message" in r.json() and "question" in r.json()["message"].lower()


def test_investigate_busy_returns_message(client, monkeypatch):
    # Exhaust the concurrency permits so the request is rejected cleanly.
    acquired = [main._inflight.acquire(blocking=False) for _ in range(main._MAX_INFLIGHT)]
    try:
        r = client.post("/investigate", json={"q": "why broken"}, headers=AUTH)
        assert r.status_code == 200 and r.json()["allowed"] is False
        assert "busy" in r.json()["message"].lower()
    finally:
        for ok in acquired:
            if ok:
                main._inflight.release()


def test_rate_limit_returns_429(client, monkeypatch):
    main.limiter.enabled = True   # autouse fixture disables it; this test needs it
    monkeypatch.setattr(main.llm, "interpret",
        lambda q, timeout=None: Action(verb="get", resource="pods", namespace="ns"))
    monkeypatch.setattr(main.executor, "run", lambda a: "ok")
    codes = [client.post("/query", json={"q": "pods"}, headers=AUTH).status_code
             for _ in range(15)]
    assert 429 in codes   # the 10/minute backstop fires
