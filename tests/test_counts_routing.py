"""Deterministic counting + client posts to the agent's unified /ask endpoint.

Routing (query vs investigate) now happens in the AGENT via an LLM call, not in the
client by keywords, so the client just posts every question to /ask (see app/llm.py
route() and app/main.py /ask)."""

import json
import pytest
from app.counts import table_facts
import client.ask as ask


POD_TABLE = """NAMESPACE   NAME      READY   STATUS    RESTARTS   AGE
kube-system a         1/1     Running   9          5d
kube-system b         1/1     Running   1          8h
prd         web       1/1     Running   7          5d"""


def test_counts_rows_and_status():
    f = table_facts(POD_TABLE)
    assert "3 rows" in f and "Running=3" in f


def test_counts_mixed_status():
    t = POD_TABLE + "\nprd         bad       0/1     CrashLoopBackOff   5   1h"
    f = table_facts(t)
    assert "4 rows" in f and "Running=3" in f and "CrashLoopBackOff=1" in f


def test_counts_no_resources():
    assert table_facts("No resources found in default namespace.") == "0 rows"


def test_counts_non_table_returns_none():
    assert table_facts("some prose, not a table") is None
    assert table_facts("") is None


def test_counts_error_output():
    assert table_facts("error: forbidden") == "0 rows"


def test_client_posts_every_question_to_ask(monkeypatch):
    """The thin client no longer routes by keywords — it always posts to /ask and the
    agent decides. Verify the endpoint is /ask and the body carries the question."""
    import urllib.request

    seen = {}
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"output": "ok"}).encode()
    def fake_urlopen(req, timeout=None, context=None):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode())
        return _Resp()
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    for q in ["how many pods are running", "why is the web pod crashing",
              "what kubernetes version", "which CNI plugin is used"]:
        ask._ask(q, "tok")
        assert seen["url"].endswith("/ask")       # always the unified endpoint
        assert seen["body"]["q"] == q             # question forwarded verbatim
