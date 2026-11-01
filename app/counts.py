"""Deterministic facts from kubectl table output. Counting is done in CODE, never
by the LLM (small models miscount rows). The computed fact is injected into the
evidence so the model reports exact numbers instead of guessing."""

from collections import Counter


def table_facts(output: str) -> str | None:
    """Return a short exact fact string for a kubectl table, or None if not a table.

    Examples: '14 rows; STATUS: Running=14'  /  '0 rows'
    """
    if not output:
        return None
    text = output.strip()
    low = text.lower()
    if low.startswith("error") or low.startswith("(no resources") or "no resources found" in low:
        return "0 rows"

    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("...")]
    if len(lines) < 2:
        return None  # no header + data -> not a countable table

    header = lines[0].split()
    if "NAME" not in header:
        return None  # not a standard kubectl table
    rows = lines[1:]
    fact = f"{len(rows)} rows"

    if "STATUS" in header:
        idx = header.index("STATUS")
        tally: Counter[str] = Counter()
        for r in rows:
            parts = r.split()
            if len(parts) > idx:
                tally[parts[idx]] += 1
        if tally:
            fact += "; STATUS: " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items()))
    return fact
