"""White-box tests for the policy gate: the security core."""

import pytest
from app.policy import Policy, _is_safe
from app.schemas import Action, ToolCall


# ----- _is_safe (input sanitisation) ------------------------------------------

@pytest.mark.parametrize("value", [
    "kube-system", "web-7c9f", "coredns-66bc5c9577-jxkw4", "a.b.c", "x1",
])
def test_is_safe_accepts_valid_names(value):
    assert _is_safe(value)


@pytest.mark.parametrize("value", [
    "a; rm -rf /",        # shell injection
    "ns && kubectl",      # command chaining
    "../../etc/passwd",   # path traversal
    "a/b",                # slash
    "a b",                # space
    "a|b", "a`b`", "a$b", "a$(id)", "a\nb", "a'b", 'a"b',
    "-rf",                # leading dash flag-injection style
    "x" * 254,            # too long (>253)
])
def test_is_safe_rejects_dangerous(value):
    assert not _is_safe(value)


# ----- simple /query allowlist -------------------------------------------------

def test_check_allows_listed_readonly(policy):
    ok, _ = policy.check(Action(verb="get", resource="pods", namespace="kube-system"))
    assert ok


@pytest.mark.parametrize("verb,resource", [
    ("delete", "pods"), ("create", "pods"), ("patch", "deployments"),
    ("apply", "services"), ("edit", "pods"), ("scale", "deployments"),
    ("exec", "pods"), ("cp", "pods"), ("drain", "nodes"),
])
def test_check_blocks_mutating_verbs(policy, verb, resource):
    ok, reason = policy.check(Action(verb=verb, resource=resource))
    assert not ok


def test_check_blocks_unlisted_resource(policy):
    ok, _ = policy.check(Action(verb="get", resource="secrets"))
    assert not ok


def test_check_blocks_injection_in_args(policy):
    ok, _ = policy.check(Action(verb="get", resource="pods", namespace="a; rm -rf /"))
    assert not ok


# ----- investigate-loop tool gate ---------------------------------------------

def test_check_tool_allows_listed_tool(policy):
    ok, _ = policy.check_tool(ToolCall(tool="list_all_pods"))
    assert ok


@pytest.mark.parametrize("name", ["final", "done"])
def test_check_tool_final_stops(policy, name):
    ok, reason = policy.check_tool(ToolCall(tool=name))
    assert ok and reason == "stop"


@pytest.mark.parametrize("tool", [
    "delete_pod", "exec_pod", "patch_deployment", "apply", "scale_deployment",
    "evil", "", "get_secrets", "read_configmap",
])
def test_check_tool_blocks_unlisted(policy, tool):
    ok, _ = policy.check_tool(ToolCall(tool=tool))
    assert not ok


def test_check_tool_blocks_injection_args(policy):
    ok, _ = policy.check_tool(ToolCall(tool="describe_pod", namespace="ns", name="../../x"))
    assert not ok


def test_empty_allowlist_denies_everything():
    p = Policy([], [])
    assert not p.check(Action(verb="get", resource="pods"))[0]
    assert not p.check_tool(ToolCall(tool="list_pods"))[0]
