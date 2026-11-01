"""Executor: the lowest read-only wall and output handling. subprocess is mocked
so no real kubectl runs."""

import subprocess
import types
import pytest
from app.executor import Executor, _HARD_READ_ONLY_VERBS


def _mk(monkeypatch, returncode=0, stdout="", stderr=""):
    def fake_run(argv, **kw):
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    monkeypatch.setattr(subprocess, "run", fake_run)


def test_run_readonly_refuses_mutating_verb(monkeypatch):
    _mk(monkeypatch, 0, "should not matter")
    ex = Executor("/tmp/kc", "default")
    for verb in ["delete", "create", "apply", "patch", "exec", "edit", "scale"]:
        out = ex.run_readonly([verb, "pods"])
        assert out.startswith("error: refused"), f"{verb} was not refused"


def test_run_readonly_allows_readonly_verbs(monkeypatch):
    _mk(monkeypatch, 0, "NAME   READY")
    ex = Executor("/tmp/kc", "default")
    for verb in _HARD_READ_ONLY_VERBS:
        out = ex.run_readonly([verb, "pods"])
        assert "error" not in out


def test_empty_stdout_returns_stderr_message(monkeypatch):
    # kubectl on an empty namespace: rc 0, empty stdout, message on stderr.
    _mk(monkeypatch, 0, "", "No resources found in default namespace.")
    ex = Executor("/tmp/kc", "default")
    out = ex.run_readonly(["get", "pods", "-n", "default"])
    assert "No resources found" in out


def test_empty_everything_returns_placeholder(monkeypatch):
    _mk(monkeypatch, 0, "", "")
    ex = Executor("/tmp/kc", "default")
    assert ex.run_readonly(["get", "pods"]) == "(no resources found)"


def test_nonzero_returns_error(monkeypatch):
    _mk(monkeypatch, 1, "", "forbidden")
    ex = Executor("/tmp/kc", "default")
    assert ex.run_readonly(["get", "pods"]).startswith("error:")


def test_timeout_handled(monkeypatch):
    def boom(argv, **kw):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)
    monkeypatch.setattr(subprocess, "run", boom)
    ex = Executor("/tmp/kc", "default")
    assert "timed out" in ex.run_readonly(["get", "pods"])


def test_empty_argv_is_error(monkeypatch):
    ex = Executor("/tmp/kc", "default")
    assert ex.run_readonly([]).startswith("error:")
