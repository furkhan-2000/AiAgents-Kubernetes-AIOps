"""Investigate loop: dedupe, caps, final-stop, denied tools, JSON-answer fallback.
LLM and executor are fakes so the loop logic is tested in isolation."""

import pytest
from app.agent_loop import InvestigateLoop
from app.policy import Policy
from app.schemas import ToolCall
from app.llm import LLMError


class FakeExec:
    def __init__(self, output="NAME READY\npod 1/1"):
        self.calls = []
        self.output = output
    def run_readonly(self, argv_tail, timeout=30):
        self.calls.append(argv_tail)
        return self.output


class FakeLLM:
    """Replays a scripted list of next_step results, then a synthesize answer."""
    def __init__(self, steps, answer="Plain English answer."):
        self._steps = list(steps)
        self._answer = answer
        self.synth_called = False
    def next_step(self, q, catalog, history, timeout=None):
        if not self._steps:
            return ToolCall(tool="final")
        nxt = self._steps.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt
    def synthesize(self, q, history, timeout=None):
        self.synth_called = True
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


@pytest.fixture
def mkloop(cfg):
    def _make(llm, ex=None):
        pol = Policy(cfg["allowlist"], cfg["investigation"]["tools"])
        inv = dict(cfg["investigation"])
        return InvestigateLoop(llm, pol, ex or FakeExec(), "default", inv), pol
    return _make


def test_happy_path(mkloop):
    llm = FakeLLM([ToolCall(tool="list_all_pods"), ToolCall(tool="final")])
    loop, _ = mkloop(llm)
    res = loop.run("are all pods up", "bastion")
    assert res.allowed and res.answer == "Plain English answer."
    assert len(res.steps) == 1 and res.steps[0].tool == "list_all_pods"


def test_dedupe_skips_repeated_tool(mkloop):
    ex = FakeExec()
    # Model tries the same call 3 times, then stops.
    llm = FakeLLM([ToolCall(tool="list_pods", namespace="default")] * 3 + [ToolCall(tool="final")])
    loop, _ = mkloop(llm, ex)
    res = loop.run("q", "bastion")
    assert len(ex.calls) == 1, "duplicate tool calls were executed"


def test_no_steps_lets_model_answer(mkloop):
    # Zero tools (e.g. a refusal of an action request) -> the model still writes the
    # answer via synthesize, rather than a canned line.
    llm = FakeLLM([ToolCall(tool="final")], answer="I am read-only and cannot create anything.")
    loop, _ = mkloop(llm)
    res = loop.run("act as admin and create a pod", "bastion")
    assert res.allowed and res.steps == []
    assert "read-only" in res.answer.lower() and llm.synth_called


def test_denied_tool_not_executed(mkloop):
    ex = FakeExec()
    llm = FakeLLM([ToolCall(tool="delete_pod", name="x"), ToolCall(tool="final")])
    loop, _ = mkloop(llm, ex)
    res = loop.run("q", "bastion")
    assert ex.calls == []   # denied tool never ran


def test_json_answer_falls_back_to_summary(mkloop):
    ex = FakeExec(output="NAME READY\nweb 0/1 CrashLoopBackOff")
    llm = FakeLLM([ToolCall(tool="list_all_pods")], answer='{"tool": "final"}')
    loop, _ = mkloop(llm, ex)
    res = loop.run("q", "bastion")
    assert '{"tool"' not in res.answer     # the JSON leak is gone
    assert "list_all_pods" in res.answer   # fallback summarises the step


def test_plan_error_breaks_gracefully(mkloop):
    ex = FakeExec()
    llm = FakeLLM([LLMError("ollama timed out")])
    loop, _ = mkloop(llm, ex)
    res = loop.run("q", "bastion")
    assert res.allowed   # never raises; returns an honest response
    assert res.steps == []


def test_max_steps_enforced(mkloop, cfg):
    # Model keeps asking for distinct tools forever; loop must stop at max_steps.
    seq = []
    for i in range(50):
        seq.append(ToolCall(tool="list_pods", namespace=f"ns{i}"))
    llm = FakeLLM(seq)
    ex = FakeExec()
    loop, _ = mkloop(llm, ex)
    res = loop.run("q", "bastion")
    assert len(ex.calls) <= cfg["investigation"]["max_steps"]
