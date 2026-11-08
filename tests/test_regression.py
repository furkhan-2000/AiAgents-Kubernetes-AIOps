"""Regression tests for findings confirmed by the adversarial review. Each test
maps to a specific fixed defect so a reintroduction fails loudly."""

import subprocess
import types
import json
import pytest
import httpx

from app.executor import Executor
from app.policy import Policy, _norm_resource
from app.schemas import Action, ToolCall
from app import tools
from app.llm import OllamaClient, LLMError
from app import agent_loop
from app.agent_loop import InvestigateLoop, _redact


# ---- executor: OSError no longer crashes (finding: subprocess only caught Timeout)

def test_executor_missing_binary_returns_error(monkeypatch):
    def boom(argv, **kw):
        raise FileNotFoundError("kubectl not found")
    monkeypatch.setattr(subprocess, "run", boom)
    ex = Executor("/tmp/kc", "default")
    assert ex.run_readonly(["get", "pods"]).startswith("error:")
    assert ex.run(Action(verb="get", resource="pods")).startswith("error:")


# ---- executor.run: read-only wall + argv branches + all-namespaces default

def _capture(monkeypatch):
    captured = {}
    def fake_run(argv, **kw):
        captured["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    return captured


def test_executor_run_refuses_mutating_verb():
    ex = Executor("/tmp/kc", "default")
    assert ex.run(Action(verb="delete", resource="pods")).startswith("error: refused")


def test_executor_run_all_namespaces_when_no_ns(monkeypatch):
    cap = _capture(monkeypatch)
    Executor("/tmp/kc", "default").run(Action(verb="get", resource="pods"))
    assert "--all-namespaces" in cap["argv"]


def test_executor_run_uses_named_ns(monkeypatch):
    cap = _capture(monkeypatch)
    Executor("/tmp/kc", "default").run(Action(verb="get", resource="pods", namespace="prod"))
    assert "-n" in cap["argv"] and "prod" in cap["argv"] and "--all-namespaces" not in cap["argv"]


def test_executor_run_named_object_uses_default_ns(monkeypatch):
    cap = _capture(monkeypatch)
    Executor("/tmp/kc", "default").run(Action(verb="get", resource="pods", name="web-1"))
    assert "web-1" in cap["argv"] and "default" in cap["argv"]


def test_executor_top_nodes_has_no_namespace_flag(monkeypatch):
    # Regression: `kubectl top nodes --all-namespaces` is invalid.
    cap = _capture(monkeypatch)
    Executor("/tmp/kc", "default").run(Action(verb="top", resource="nodes"))
    assert "--all-namespaces" not in cap["argv"] and "-n" not in cap["argv"]


def test_executor_get_nodes_has_no_namespace_flag(monkeypatch):
    cap = _capture(monkeypatch)
    Executor("/tmp/kc", "default").run(Action(verb="get", resource="nodes"))
    assert "--all-namespaces" not in cap["argv"] and "-n" not in cap["argv"]


def test_executor_top_pods_uses_all_namespaces(monkeypatch):
    cap = _capture(monkeypatch)
    Executor("/tmp/kc", "default").run(Action(verb="top", resource="pods"))
    assert "--all-namespaces" in cap["argv"]   # valid for `top pods`


def test_executor_run_describe_and_logs(monkeypatch):
    cap = _capture(monkeypatch)
    ex = Executor("/tmp/kc", "default")
    ex.run(Action(verb="describe", resource="pod", name="p", namespace="ns"))
    assert cap["argv"][2:] == ["describe", "pod", "p", "-n", "ns"]
    ex.run(Action(verb="logs", resource="pod", name="p"))
    assert "logs" in cap["argv"] and "p" in cap["argv"] and "default" in cap["argv"]


# ---- policy: singular/plural, out-of-scope, namespace allowlist

def test_norm_resource_singular_plural():
    assert _norm_resource("pod") == "pods" == _norm_resource("pods")


def test_describe_singular_matches_plural_prompt(policy):
    # model may emit plural 'pods' for describe; allowlist stores 'pod'. Both pass.
    assert policy.check(Action(verb="describe", resource="pods", name="x"))[0]
    assert policy.check(Action(verb="describe", resource="pod", name="x"))[0]


def test_query_out_of_scope_denied(policy):
    ok, reason = policy.check(Action(verb="none", resource="none"))
    assert not ok and "cluster" in reason


def test_namespace_allowlist_enforced():
    p = Policy([{"verb": "get", "resource": "pods"}], ["list_pods", "list_all_pods"],
               allowed_namespaces=["app"])
    assert p.check(Action(verb="get", resource="pods", namespace="app"))[0]
    assert not p.check(Action(verb="get", resource="pods", namespace="kube-system"))[0]
    # tool gate too
    assert p.check_tool(ToolCall(tool="list_pods", namespace="app"))[0]
    assert not p.check_tool(ToolCall(tool="list_pods", namespace="kube-system"))[0]
    # list_all_pods is exempt (cluster-wide by design)
    assert p.check_tool(ToolCall(tool="list_all_pods"))[0]


def test_container_arg_injection_blocked(policy):
    assert not policy.check_tool(ToolCall(tool="get_logs", name="p", container="../x"))[0]


# ---- tools: multi-container logs

def test_get_logs_multicontainer_default():
    argv = tools.build_argv(ToolCall(tool="get_logs", namespace="ns", name="p"), "default")
    assert "--all-containers=true" in argv


def test_get_logs_specific_container():
    argv = tools.build_argv(ToolCall(tool="get_logs", namespace="ns", name="p", container="app"), "default")
    assert "-c" in argv and "app" in argv and "--all-containers=true" not in argv


# ---- llm: non-JSON 200 body and error paths become LLMError

def _fake_post(text=None, exc=None):
    def post(url, json=None, timeout=None):
        if exc:
            raise exc
        return types.SimpleNamespace(raise_for_status=lambda: None, json=_jf(text))
    return post


def _jf(text):
    def j():
        if text is None:
            raise ValueError("not json")   # mimic resp.json() on non-JSON body
        return {"response": text}
    return j


def test_generate_non_json_body_is_llmerror(monkeypatch):
    monkeypatch.setattr(httpx, "post", _fake_post(text=None))
    with pytest.raises(LLMError):
        OllamaClient("http://x", "m", 5).interpret("q")


@pytest.mark.parametrize("bad", ["not json", "[]", "{}"])
def test_next_step_bad_output_is_llmerror(monkeypatch, bad):
    monkeypatch.setattr(httpx, "post", _fake_post(text=bad))
    with pytest.raises(LLMError):
        OllamaClient("http://x", "m", 5, brain_prompt="b").next_step("q", {}, "")


def test_synthesize_http_error_is_llmerror(monkeypatch):
    monkeypatch.setattr(httpx, "post", _fake_post(exc=httpx.ConnectError("down")))
    with pytest.raises(LLMError):
        OllamaClient("http://x", "m", 5, brain_prompt="b").synthesize("q", "ev")


# ---- agent_loop: redaction, timeout branch, synth failure, empty answer, recovery

def test_redact_masks_secrets():
    assert "hunter2" not in _redact("password: hunter2")
    assert "[REDACTED]" in _redact("api_key=ABCDEF123456")
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV"
    assert jwt not in _redact(f"token {jwt}")


def test_redact_masks_whole_authorization_value():
    # The regex must mask the token, not just the scheme word 'Bearer'.
    out = _redact("Authorization: Bearer abc123opaquetoken")
    assert "abc123opaquetoken" not in out and "[REDACTED]" in out


def test_redact_masks_basic_auth():
    out = _redact("authorization = Basic dXNlcjpwYXNzd29yZA==")
    assert "dXNlcjpwYXNzd29yZA" not in out


# ---- namespace allowlist must NOT block cluster-scoped tools/resources

def _p_ns():
    return Policy(
        [{"verb": "get", "resource": "nodes"}, {"verb": "get", "resource": "pods"},
         {"verb": "get", "resource": "namespaces"}],
        ["list_nodes", "top_nodes", "list_namespaces", "describe_node", "list_pods", "list_all_pods"],
        allowed_namespaces=["app"],
    )


@pytest.mark.parametrize("tool", ["list_nodes", "top_nodes", "list_namespaces"])
def test_cluster_scoped_tools_allowed_under_ns_allowlist(tool):
    assert _p_ns().check_tool(ToolCall(tool=tool))[0]


def test_cluster_scoped_resources_allowed_under_ns_allowlist():
    assert _p_ns().check(Action(verb="get", resource="nodes"))[0]
    assert _p_ns().check(Action(verb="get", resource="namespaces"))[0]


def test_namespaced_no_ns_denied_under_allowlist():
    # Would hit --all-namespaces and bypass the allowlist -> must be denied.
    assert not _p_ns().check(Action(verb="get", resource="pods"))[0]
    assert _p_ns().check(Action(verb="get", resource="pods", namespace="app"))[0]


class _Exec:
    def __init__(self, out="NAME\npod 1/1"):
        self.calls = []
        self.out = out
    def run_readonly(self, argv, timeout=30):
        self.calls.append(argv)
        return self.out


class _LLM:
    def __init__(self, steps, answer="prose"):
        self._steps = list(steps)
        self._answer = answer
    def next_step(self, q, c, h, timeout=None):
        if not self._steps:
            return ToolCall(tool="final")
        n = self._steps.pop(0)
        if isinstance(n, Exception):
            raise n
        return n
    def synthesize(self, q, h, timeout=None):
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


def _loop(cfg, llm, ex):
    return InvestigateLoop(llm, Policy(cfg["allowlist"], cfg["investigation"]["tools"]),
                           ex, "default", dict(cfg["investigation"]))


def test_loop_timeout_stops_before_work(cfg):
    ex = _Exec()
    inv = dict(cfg["investigation"]); inv["loop_timeout_seconds"] = 0
    loop = InvestigateLoop(_LLM([ToolCall(tool="list_all_pods")]),
                           Policy(cfg["allowlist"], cfg["investigation"]["tools"]),
                           ex, "default", inv)
    res = loop.run("q", "bastion")
    assert res.allowed and ex.calls == []   # deadline fired before any tool ran


def test_loop_synth_failure_recovers(cfg):
    ex = _Exec()
    loop = _loop(cfg, _LLM([ToolCall(tool="list_all_pods")], answer=LLMError("x")), ex)
    res = loop.run("q", "bastion")
    # Synth fails on both the first try AND the trimmed retry -> graceful degrade:
    # allowed, tools still ran (1 step), and the answer points to the raw findings.
    assert res.allowed and "timed out" in res.answer and len(res.steps) == 1


class _LLMRetry:
    """synthesize raises once, then succeeds — exercises the loop.synth_retry_ok path
    (first synth times out on a slow CPU; the trimmed retry returns prose)."""
    def __init__(self, steps, retry_answer="recovered summary"):
        self._steps = list(steps)
        self._retry_answer = retry_answer
        self._synth_calls = 0
    def next_step(self, q, c, h, timeout=None):
        if not self._steps:
            return ToolCall(tool="final")
        n = self._steps.pop(0)
        if isinstance(n, Exception):
            raise n
        return n
    def synthesize(self, q, h, timeout=None):
        self._synth_calls += 1
        if self._synth_calls == 1:
            raise LLMError("first synth timed out")
        return self._retry_answer


def test_loop_synth_retry_succeeds(cfg):
    ex = _Exec()
    llm = _LLMRetry([ToolCall(tool="list_all_pods")])
    res = _loop(cfg, llm, ex).run("q", "bastion")
    assert res.allowed
    assert "recovered summary" in res.answer   # the trimmed retry's prose is returned
    assert "timed out" not in res.answer        # did NOT fall through to the degrade branch
    assert llm._synth_calls == 2                 # proves the retry actually fired
    assert len(res.steps) == 1


def test_exact_count_is_from_full_table_not_truncated(cfg):
    """The authoritative EXACT COUNT must reflect the FULL table even when the body
    is truncated for the prompt (counting must happen before truncation)."""
    header = "NAME READY STATUS RESTARTS AGE"
    rows = [f"pod-{n} 1/1 Running 0 1d" for n in range(50)]
    big = "\n".join([header] + rows)
    inv = dict(cfg["investigation"]); inv["max_output_bytes"] = 150   # forces truncation
    ex = _Exec(); ex.out = big
    loop = InvestigateLoop(_LLM([ToolCall(tool="list_all_pods")], answer="ok"),
                           Policy(cfg["allowlist"], cfg["investigation"]["tools"]), ex, "default", inv)
    res = loop.run("how many pods are running", "bastion")
    obs = res.steps[0].observation
    assert "50 rows" in obs and "Running=50" in obs   # full count, not the ~3 truncated rows


def test_loop_empty_answer_falls_back(cfg):
    ex = _Exec()
    loop = _loop(cfg, _LLM([ToolCall(tool="list_all_pods")], answer="   "), ex)
    res = loop.run("q", "bastion")
    assert "list_all_pods" in res.answer


def test_loop_plan_error_continues_not_aborts(cfg):
    ex = _Exec()
    loop = _loop(cfg, _LLM([LLMError("bad json"), ToolCall(tool="list_all_pods")]), ex)
    res = loop.run("q", "bastion")
    assert len(ex.calls) == 1   # recovered after the hiccup instead of aborting


def test_loop_missing_name_recovers_with_note(cfg):
    ex = _Exec()
    # describe_pod passes policy (name optional) but build_argv raises -> note + continue
    loop = _loop(cfg, _LLM([ToolCall(tool="describe_pod", namespace="ns")], answer="no data gathered"), ex)
    res = loop.run("q", "bastion")
    # build_argv failed (missing name) -> no tool ran, loop recovered without crashing
    assert ex.calls == [] and res.allowed and res.steps == []
