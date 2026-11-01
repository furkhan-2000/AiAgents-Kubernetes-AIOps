"""Deterministic counting + client routing."""

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


@pytest.mark.parametrize("q", [
    "why is the web pod crashing", "what went wrong in the logs",
    "debug the api service", "investigate the failed deployment", "pod keeps restarting",
])
def test_route_investigate(q):
    assert ask._route(q) == "/investigate"


@pytest.mark.parametrize("q", [
    "how many pods are running", "list all services", "show me the nodes",
    "what namespaces exist",
])
def test_route_query(q):
    assert ask._route(q) == "/query"
