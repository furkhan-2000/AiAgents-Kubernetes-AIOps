"""Every tool must build a read-only kubectl argv and nothing else."""

import pytest
from app import tools
from app.schemas import ToolCall

_READ_ONLY = {"get", "describe", "logs", "top", "events"}
_FORBIDDEN = {"delete", "create", "apply", "patch", "edit", "scale", "exec",
              "cp", "drain", "cordon", "replace", "annotate", "label", "rollout"}


def test_catalog_and_builders_match():
    # Every advertised tool must be buildable (with a name where required).
    for name in tools.CATALOG:
        call = ToolCall(tool=name, namespace="ns", name="obj")
        argv = tools.build_argv(call, "default")
        assert argv and isinstance(argv, list)


def test_every_tool_is_read_only():
    for name in tools.CATALOG:
        argv = tools.build_argv(ToolCall(tool=name, namespace="ns", name="obj"), "default")
        verb = argv[0]
        assert verb in _READ_ONLY, f"{name} produced non-read-only verb {verb}"
        assert not (_FORBIDDEN & set(argv)), f"{name} contains a forbidden token: {argv}"


def test_unknown_tool_raises():
    with pytest.raises(ValueError):
        tools.build_argv(ToolCall(tool="delete_everything"), "default")


@pytest.mark.parametrize("tool", ["describe_pod", "describe_node", "describe_deployment"])
def test_describe_requires_name(tool):
    with pytest.raises(ValueError):
        tools.build_argv(ToolCall(tool=tool, namespace="ns"), "default")  # no name


def test_list_all_pods_is_all_namespaces():
    argv = tools.build_argv(ToolCall(tool="list_all_pods"), "default")
    assert argv == ["get", "pods", "--all-namespaces", "-o", "wide"]


def test_list_tool_sweeps_all_namespaces_when_missing():
    # No namespace on a LIST tool -> search every namespace (not silently 'default',
    # which gave wrong default-only answers for cluster-wide questions).
    argv = tools.build_argv(ToolCall(tool="list_pods"), "mydefault")
    assert "--all-namespaces" in argv and "mydefault" not in argv


def test_named_tool_uses_default_namespace_when_missing():
    # A tool that targets a specific object still needs a namespace; use the default.
    argv = tools.build_argv(ToolCall(tool="describe_pod", name="p"), "mydefault")
    assert "mydefault" in argv


def test_get_logs_tail_and_previous():
    argv = tools.build_argv(
        ToolCall(tool="get_logs", namespace="ns", name="p", tail_lines=50, previous=True),
        "default",
    )
    assert "logs" == argv[0] and "--tail" in argv and "50" in argv and "--previous" in argv
