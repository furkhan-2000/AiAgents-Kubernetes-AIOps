"""Shared test fixtures. Sets env so app.main imports cleanly offline (no Ollama,
no cluster), and provides a TestClient with the LLM and executor mocked."""

import os
import sys
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Env the app reads at import time.
os.environ.setdefault("AGENT_TOKEN", "testtoken")
os.environ.setdefault("AGENT_CONFIG", str(ROOT / "config.yaml"))
os.environ.setdefault("AGENT_BRAIN", str(ROOT / "app" / "OLLAMA_BRAIN.md"))


@pytest.fixture(autouse=True)
def _disable_rate_limit():
    """Keep the slowapi limiter from leaking counts across tests. Tests that need
    the limiter re-enable it explicitly."""
    try:
        import app.main as main
        main.limiter.enabled = False
    except Exception:
        pass
    yield


@pytest.fixture
def cfg():
    import yaml
    with open(ROOT / "config.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture
def policy(cfg):
    from app.policy import Policy
    return Policy(cfg["allowlist"], cfg["investigation"]["tools"])
