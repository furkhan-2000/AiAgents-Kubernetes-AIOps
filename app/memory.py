"""RAG memory: remembers which READ-ONLY TOOLS answered past questions, and on a
new question suggests the tools that worked for similar ones. It makes the weak
model more consistent, without changing what it is allowed to do.

Security model (fintech, hardened):
- We store ONLY tool NAMES (from the allowlist) + the question text + its vector.
  We never store cluster data, values, logs, or secrets -> nothing sensitive and
  no live data to go stale.
- On recall we return ONLY allowlisted tool names as a hint. We never re-inject
  past free-text into the prompt, so a poisoned memory cannot prompt-inject.
- Even if memory suggested a bad tool, the three walls (policy -> executor -> RBAC)
  still gate every call. Memory can never escalate privilege.
- Everything is fail-open: any error in embed/store is swallowed and the agent
  works exactly as if RAG were off. RAG is an enhancement, never a dependency.
- A similarity threshold filters weak matches; a size cap bounds the store.
"""

from __future__ import annotations

import math
from .audit import audit


class RagMemory:
    def __init__(self, embed_fn, store, allowed_tools, top_k: int = 3,
                 min_similarity: float = 0.75, max_rows: int = 5000):
        self._embed = embed_fn                     # question -> list[float] (or raises)
        self._store = store                        # add()/search()/count() (or raises)
        self._allowed = {str(t) for t in (allowed_tools or [])}
        self._top_k = int(top_k)
        self._min_sim = float(min_similarity)
        self._max_rows = int(max_rows)

    # ----- public API (both fail-open) ---------------------------------------

    def recall(self, question: str) -> list[str]:
        """Return a de-duplicated list of allowlisted tool names that worked for
        similar past questions. Empty list on any error or no confident match."""
        try:
            vec = self._embed(question)
            hits = self._store.search(vec, self._top_k)      # [(tools_csv, similarity)]
            tools: list[str] = []
            for tools_csv, sim in hits:
                if sim < self._min_sim:
                    continue
                for t in str(tools_csv).split(","):
                    t = t.strip()
                    if t in self._allowed and t not in tools:   # allowlist-gated
                        tools.append(t)
            if tools:
                audit("rag.recall", count=len(tools))
            return tools
        except Exception as exc:                              # fail-open
            audit("rag.recall_error", error=str(exc))
            return []

    def remember(self, question: str, tools_used: list[str]) -> None:
        """Store a VERIFIED-GOOD case: the question + the allowlisted tools that
        produced a real answer. Silently does nothing on error."""
        try:
            # keep only real allowlisted tools; drop everything else (no injection in)
            clean = [t for t in dict.fromkeys(tools_used) if t in self._allowed]
            if not clean:
                return
            if self._store.count() >= self._max_rows:         # bounded size
                return
            vec = self._embed(question)
            self._store.add(vec, question.strip(), ",".join(clean))
            audit("rag.remember", tools=clean)
        except Exception as exc:                              # fail-open
            audit("rag.remember_error", error=str(exc))


# ----- cosine helper (used by the sqlite store's fallback + tests) -----------

def cosine(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
