"""The bastion client must never dump a traceback at a user."""

import io
import json
import types
import urllib.request
import urllib.error
import pytest

import client.ask as ask


class _Resp:
    def __init__(self, body: bytes):
        self._body = body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def read(self):
        return self._body


def _run_ask(monkeypatch, capsys, urlopen):
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    ask._ask("why broken", "token")
    return capsys.readouterr().out


def test_unreadable_response_is_handled(monkeypatch, capsys):
    out = _run_ask(monkeypatch, capsys, lambda req, timeout=None, context=None: _Resp(b"not json at all"))
    assert "unreadable" in out.lower()


def test_http_403_is_friendly(monkeypatch, capsys):
    def urlopen(req, timeout=None, context=None):
        raise urllib.error.HTTPError("u", 403, "forbidden", {}, None)
    out = _run_ask(monkeypatch, capsys, urlopen)
    assert "access denied" in out.lower()


def test_ctrl_c_during_request_is_clean(monkeypatch, capsys):
    def urlopen(req, timeout=None, context=None):
        raise KeyboardInterrupt()
    out = _run_ask(monkeypatch, capsys, urlopen)
    assert "cancelled" in out.lower()


def test_valid_answer_printed(monkeypatch, capsys):
    out = _run_ask(monkeypatch, capsys,
                   lambda req, timeout=None, context=None: _Resp(json.dumps({"answer": "All good."}).encode()))
    assert "All good." in out
