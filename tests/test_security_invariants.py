"""Executable security contract. These are the invariants that make the agent safe.
If any fails, a wall was weakened — fix the CODE, never the test. Deterministic,
no model, no network. This is the real guard, stronger than any prose rule."""

import pathlib
import re
import yaml
import pytest

from app import tools
from app.executor import Executor, _HARD_READ_ONLY_VERBS
from app.policy import Policy
from app.schemas import Action, ToolCall

ROOT = pathlib.Path(__file__).resolve().parents[1]
READ_ONLY_VERBS = {"get", "list", "watch", "describe", "logs", "top", "events",
                   "api-resources", "explain", "version", "cluster-info"}
FORBIDDEN_VERBS = {"create", "apply", "patch", "delete", "edit", "replace", "scale",
                   "exec", "cp", "drain", "cordon", "uncordon", "annotate", "label",
                   "rollout", "set", "expose", "run", "attach", "port-forward"}
FORBIDDEN_RESOURCES = {"secrets", "secret", "configmaps", "configmap"}


def _cfg():
    return yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))


# ---- WALL 1: the allowlist is read-only only ---------------------------------

def test_query_allowlist_is_read_only():
    for item in _cfg()["allowlist"]:
        assert item["verb"] in READ_ONLY_VERBS, f"mutating verb in allowlist: {item}"
        assert item["resource"] not in FORBIDDEN_RESOURCES, f"forbidden resource: {item}"


def test_policy_blocks_mutations_and_secrets():
    p = Policy(_cfg()["allowlist"], _cfg()["investigation"]["tools"])
    for v in FORBIDDEN_VERBS:
        assert not p.check(Action(verb=v, resource="pods"))[0], f"{v} allowed!"
    assert not p.check(Action(verb="get", resource="secrets"))[0]
    assert not p.check(Action(verb="get", resource="configmaps"))[0]


# ---- WALL 1b: every investigate tool builds a read-only verb ------------------

def test_every_tool_builds_read_only_verb():
    for name in tools.CATALOG:
        argv = tools.build_argv(ToolCall(tool=name, namespace="ns", name="obj"), "default")
        assert argv[0] in READ_ONLY_VERBS, f"tool {name} -> non-read-only verb {argv[0]}"
        assert not (FORBIDDEN_VERBS & set(argv)), f"tool {name} argv has a write token: {argv}"


def test_config_investigation_tools_all_have_builders():
    # A tool name in config with no builder passes every other test but raises
    # ValueError("unknown tool") at runtime when the brain calls it. Guard that.
    for name in _cfg()["investigation"]["tools"]:
        assert name in tools.CATALOG, f"config investigation tool has no builder: {name}"


# ---- No-namespace LIST/GET tools sweep ALL namespaces, never silently 'default' --

def test_list_tools_sweep_all_namespaces_when_none_given():
    # These answer cluster-wide questions; with no namespace they must search every
    # namespace, not default (which produced wrong default-only answers).
    for name in ("list_pods", "get_events", "top_pods", "list_deployments",
                 "list_services", "get_endpoints", "get_ingresses", "get_pvcs"):
        argv = tools.build_argv(ToolCall(tool=name), "default")
        assert "--all-namespaces" in argv, f"{name} did not sweep all namespaces: {argv}"
        assert "default" not in argv, f"{name} silently fell back to default: {argv}"
        assert argv[0] in READ_ONLY_VERBS
    # A named namespace still scopes to exactly that namespace.
    argv = tools.build_argv(ToolCall(tool="list_pods", namespace="prd"), "default")
    assert argv[argv.index("-n") + 1] == "prd" and "--all-namespaces" not in argv


# ---- WALL 2: the executor hard-refuses every mutating verb --------------------

def test_executor_refuses_all_forbidden_verbs():
    assert not (_HARD_READ_ONLY_VERBS & FORBIDDEN_VERBS), "read-only set contains a write verb"
    ex = Executor("/tmp/kc", "default")
    for v in FORBIDDEN_VERBS:
        assert ex.run(Action(verb=v, resource="pods")).startswith("error: refused"), v


# ---- WALL 3: the RBAC role grants read-only, never secrets/configmaps ---------

def test_rbac_clusterrole_is_read_only():
    doc = yaml.safe_load(open(ROOT / "k8s/rbac/clusterrole.yaml", encoding="utf-8"))
    for rule in doc["rules"]:
        for v in rule.get("verbs", []):
            assert v in {"get", "list", "watch"}, f"RBAC grants non-read verb: {v}"
        for r in rule.get("resources", []):
            assert r.lower() not in FORBIDDEN_RESOURCES, f"RBAC grants forbidden resource: {r}"


# ---- No shell execution anywhere in the app ----------------------------------

def test_no_shell_execution_in_app():
    for pyf in (ROOT / "app").glob("*.py"):
        src = pyf.read_text(encoding="utf-8")
        assert "shell=True" not in src, f"shell=True found in {pyf.name}"
        assert not re.search(r"os\.system\s*\(", src), f"os.system in {pyf.name}"


# ---- Injection guard: names/namespaces can't become flags or shell -----------

@pytest.mark.parametrize("bad", ["-rf", "--all-namespaces", "a; rm -rf /", "a|b",
                                 "../../x", "a$(id)", "a`b`", "a b"])
def test_injection_values_rejected(bad):
    from app.policy import _is_safe
    assert not _is_safe(bad)


# ---- RAG can never introduce a non-allowlisted tool --------------------------

def test_rag_recall_is_allowlist_gated():
    from app.memory import RagMemory

    class Store:
        def search(self, v, k): return [("delete_pod,list_all_pods", 0.99)]
        def count(self): return 1
    out = RagMemory(lambda q: [1.0], Store(), ["list_all_pods"]).recall("x")
    assert out == ["list_all_pods"]   # the poisoned non-allowlisted tool is dropped
