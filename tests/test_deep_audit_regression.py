"""Regression tests for the deep-audit round (ReDoS, unbounded output, synthesis
budget, dedup, no-shell, client, e2e investigate)."""

import json
import subprocess
import types
import time
import pytest
import httpx

from app.redact import redact, _MAX
from app.executor import Executor, _MAX_OUTPUT_BYTES
from app.policy import Policy
from app.schemas import Action, ToolCall
from app.agent_loop import InvestigateLoop
from app.llm import LLMError


# ---- CRITICAL: redaction must be linear time (no ReDoS)

def test_redact_is_linear_on_long_nonmatching_input():
    payload = ("a1b2c3." * 40000)   # ~280k chars, the old O(n^2) regex hung on this
    t0 = time.monotonic()
    out = redact(payload)
    assert time.monotonic() - t0 < 2.0           # must be fast
    assert len(out) <= _MAX + 32                  # and length-capped


def test_redact_masks_json_and_suffixed_keys():
    assert "s3cr3t" not in redact('{"password": "s3cr3t"}')
    assert "hunter2" not in redact("DB_PASSWORD=hunter2")
    assert "abc.def" not in redact("DATABASE_URL: postgres://u:abc.def@host/db")


# ---- unbounded output: logs are bounded; output hard-capped

def _cap(monkeypatch, stdout="ok"):
    cap = {}
    def fake_run(argv, **kw):
        cap["argv"] = argv
        cap["kwargs"] = kw
        return types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    return cap


def test_query_logs_are_bounded(monkeypatch):
    cap = _cap(monkeypatch)
    Executor("/tmp/kc", "default").run(Action(verb="logs", resource="pod", name="p"))
    a = cap["argv"]
    assert "--tail" in a and "--limit-bytes" in a and "--all-containers=true" in a


def test_output_hard_capped(monkeypatch):
    _cap(monkeypatch, stdout="x" * (_MAX_OUTPUT_BYTES + 5000))
    out = Executor("/tmp/kc", "default").run(Action(verb="get", resource="pods", namespace="ns"))
    assert len(out) <= _MAX_OUTPUT_BYTES + 32 and out.endswith("[truncated]")


# ---- no-shell guarantee (argv list, never shell=True, kubeconfig pinned)

def test_executor_never_uses_shell(monkeypatch):
    cap = _cap(monkeypatch)
    Executor("/tmp/kc", "default").run(Action(verb="get", resource="pods", namespace="ns"))
    assert isinstance(cap["argv"], list)
    assert cap["kwargs"].get("shell", False) is False
    assert any("--kubeconfig=/tmp/kc" == x for x in cap["argv"])


# ---- loop: synthesis gets its reserved budget; dedup treats default ns as same

class _Exec:
    def __init__(self, out="NAME\npod 1/1"):
        self.calls = []
        self.out = out
    def run_readonly(self, argv, timeout=30):
        self.calls.append((argv, timeout))
        return self.out


class _LLM:
    def __init__(self, steps, answer="prose"):
        self._steps = list(steps)
        self._answer = answer
        self.synth_timeout = None
    def next_step(self, q, c, h, timeout=None):
        return self._steps.pop(0) if self._steps else ToolCall(tool="final")
    def synthesize(self, q, h, timeout=None):
        self.synth_timeout = timeout
        return self._answer


def _loop(cfg, llm, ex, **over):
    inv = dict(cfg["investigation"]); inv.update(over)
    return InvestigateLoop(llm, Policy(cfg["allowlist"], cfg["investigation"]["tools"]),
                           ex, "default", inv)


def test_synthesis_gets_reserved_budget(cfg):
    llm = _LLM([ToolCall(tool="list_all_pods")])
    loop = _loop(cfg, llm, _Exec(), synth_reserve_seconds=45, call_cap_seconds=300)
    loop.run("q", "bastion")
    assert llm.synth_timeout is not None and 45 <= llm.synth_timeout <= 300


def test_step_timeout_never_exceeds_call_cap(cfg):
    # Per-call timeouts must never exceed the Ollama ceiling even with a huge budget.
    class CapLLM(_LLM):
        def __init__(self):
            super().__init__([ToolCall(tool="list_all_pods")])
            self.step_timeout = None
        def next_step(self, q, c, h, timeout=None):
            self.step_timeout = timeout
            return super().next_step(q, c, h, timeout)
    llm = CapLLM()
    loop = _loop(cfg, llm, _Exec(), loop_timeout_seconds=100000,
                 synth_reserve_seconds=60, call_cap_seconds=120)
    loop.run("q", "bastion")
    assert llm.step_timeout is not None and llm.step_timeout <= 120


def test_long_url_credentials_are_redacted():
    from app.redact import redact
    secret = "p" * 400
    assert secret not in redact(f"redis://user:{secret}@host:6379")


def test_dedup_treats_default_namespace_as_same(cfg):
    ex = _Exec()
    # explicit 'default' then no-namespace list_pods -> same effective call -> 1 run
    llm = _LLM([ToolCall(tool="list_pods", namespace="default"),
                ToolCall(tool="list_pods"), ToolCall(tool="final")])
    loop = _loop(cfg, llm, ex)
    loop.run("q", "bastion")
    assert len(ex.calls) == 1


def test_redundant_list_pods_after_list_all_is_skipped(cfg):
    ex = _Exec()
    # list_all_pods, then model redundantly re-lists namespaces, then final.
    llm = _LLM([ToolCall(tool="list_all_pods"),
                ToolCall(tool="list_pods", namespace="kube-system"),
                ToolCall(tool="list_pods", namespace="prd"),
                ToolCall(tool="final")])
    loop = _loop(cfg, llm, ex)
    res = loop.run("are all pods up", "bastion")
    # only list_all_pods actually ran; the redundant list_pods calls were skipped
    assert len(ex.calls) == 1 and len(res.steps) == 1


def test_loop_output_is_redacted(cfg):
    ex = _Exec(out="Environment:\n  API_TOKEN: supersecretvalue")
    loop = _loop(cfg, _LLM([ToolCall(tool="list_all_pods")], answer=""), ex)
    res = loop.run("q", "bastion")
    assert "supersecretvalue" not in res.steps[0].observation
