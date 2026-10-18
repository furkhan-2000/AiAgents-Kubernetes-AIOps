"""Structured audit logging. Everything is written to stdout as JSON so the
host (Falco / Wazuh / SIEM) can collect it. Never log secrets."""

import json
import logging
import sys

_logger = logging.getLogger("agent.audit")
_logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(message)s"))
_logger.addHandler(_handler)
_logger.propagate = False


def audit(event: str, **fields) -> None:
    """Emit one JSON audit line: who asked, what the LLM chose, what ran, result."""
    record = {"event": event}
    record.update(fields)
    _logger.info(json.dumps(record, ensure_ascii=False))
