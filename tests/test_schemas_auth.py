"""Schema validation bounds and auth (fail-closed, constant-time)."""

import pytest
from pydantic import ValidationError
from app.schemas import QueryRequest, InvestigateRequest, ToolCall


def test_query_rejects_empty_and_oversized():
    with pytest.raises(ValidationError):
        QueryRequest(q="")
    with pytest.raises(ValidationError):
        QueryRequest(q="x" * 501)


def test_investigate_rejects_oversized():
    with pytest.raises(ValidationError):
        InvestigateRequest(q="x" * 1001)
    assert InvestigateRequest(q="ok").q == "ok"


@pytest.mark.parametrize("tail", [0, -1, 5000])
def test_toolcall_tail_bounds(tail):
    with pytest.raises(ValidationError):
        ToolCall(tool="get_logs", tail_lines=tail)


def test_toolcall_tail_valid():
    assert ToolCall(tool="get_logs", tail_lines=100).tail_lines == 100


# ----- auth -------------------------------------------------------------------

def test_auth_fail_closed_without_token(monkeypatch):
    import importlib
    import app.auth as auth
    monkeypatch.setattr(auth, "_EXPECTED", "")
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        auth.verify("Bearer anything")
    assert e.value.status_code == 503


def test_auth_rejects_missing_bearer(monkeypatch):
    import app.auth as auth
    monkeypatch.setattr(auth, "_EXPECTED", "secret")
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        auth.verify("")
    assert e.value.status_code == 401


def test_auth_rejects_wrong_token(monkeypatch):
    import app.auth as auth
    monkeypatch.setattr(auth, "_EXPECTED", "secret")
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        auth.verify("Bearer wrong")
    assert e.value.status_code == 403


def test_auth_accepts_correct_token(monkeypatch):
    import app.auth as auth
    monkeypatch.setattr(auth, "_EXPECTED", "secret")
    assert auth.verify("Bearer secret") == "bastion"
