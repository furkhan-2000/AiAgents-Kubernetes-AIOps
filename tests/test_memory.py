"""RAG memory logic: allowlist gating, similarity threshold, fail-open, bounded,
dedupe. Uses a fake store + fake embed so no native extension is needed."""

import pytest
from app.memory import RagMemory, cosine


class FakeStore:
    def __init__(self):
        self.rows = []   # (vec, question, tools_csv)
    def add(self, vec, question, tools):
        self.rows.append((vec, question, tools))
    def count(self):
        return len(self.rows)
    def search(self, vec, k):
        scored = [(tools, cosine(vec, v)) for (v, _q, tools) in self.rows]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]


# fake embed: map a word to a simple vector so similar text -> similar vector
def _embed(text: str):
    t = text.lower()
    return [
        1.0 if "pod" in t else 0.0,
        1.0 if "node" in t else 0.0,
        1.0 if "log" in t else 0.0,
    ]


ALLOWED = ["list_all_pods", "describe_pod", "get_logs", "list_nodes"]


def _mem(store=None, **kw):
    return RagMemory(_embed, store or FakeStore(), ALLOWED, min_similarity=0.5, **kw)


def test_remember_then_recall_returns_tools():
    m = _mem()
    m.remember("why is the pod failing", ["list_all_pods", "describe_pod"])
    out = m.recall("the pod is failing why")
    assert "list_all_pods" in out and "describe_pod" in out


def test_recall_only_allowlisted_tools():
    store = FakeStore()
    # inject a poisoned row with a non-allowlisted tool
    store.add(_embed("pod"), "q", "delete_pod,list_all_pods")
    out = _mem(store).recall("pod")
    assert "delete_pod" not in out and "list_all_pods" in out   # poison filtered


def test_remember_drops_non_allowlisted():
    store = FakeStore()
    _mem(store).remember("pod question", ["delete_pod", "list_all_pods"])
    assert store.rows and "delete_pod" not in store.rows[0][2]


def test_remember_nothing_when_no_valid_tools():
    store = FakeStore()
    _mem(store).remember("q", ["delete_pod", "exec_pod"])
    assert store.count() == 0


def test_similarity_threshold_filters_weak_matches():
    store = FakeStore()
    store.add(_embed("node stuff"), "q", "list_nodes")   # about nodes
    out = _mem(store, ).recall("pod logs")               # unrelated -> low sim
    assert out == []


def test_threshold_boundary_mid_range():
    """A moderate match (~0.58) is filtered at threshold 0.75 but kept at 0.5."""
    store = FakeStore()
    # query "pod logs" -> [1,0,1]; stored "pod node" -> [1,1,0] -> cosine ~0.5
    store.add([1.0, 1.0, 0.0], "pod node", "list_all_pods")
    # exactly-matching high case -> cosine 1.0
    store.add([1.0, 0.0, 1.0], "pod logs", "describe_pod")
    high = RagMemory(_embed, store, ALLOWED, min_similarity=0.75).recall("pod logs")
    low  = RagMemory(_embed, store, ALLOWED, min_similarity=0.40).recall("pod logs")
    assert "describe_pod" in high and "list_all_pods" not in high   # 0.5 < 0.75 -> filtered
    assert "list_all_pods" in low                                   # 0.5 > 0.40 -> kept


def test_recall_fail_open_on_embed_error():
    def bad_embed(t): raise RuntimeError("ollama down")
    m = RagMemory(bad_embed, FakeStore(), ALLOWED)
    assert m.recall("anything") == []        # never raises


def test_remember_fail_open_on_store_error():
    class BadStore(FakeStore):
        def add(self, *a): raise RuntimeError("disk full")
    RagMemory(_embed, BadStore(), ALLOWED).remember("q", ["list_all_pods"])  # no raise


def test_size_cap_enforced():
    store = FakeStore()
    m = _mem(store, max_rows=1)
    m.remember("first pod", ["list_all_pods"])
    m.remember("second pod", ["describe_pod"])
    assert store.count() == 1                 # bounded


def test_loop_uses_memory_recall_and_remember(cfg):
    """End to end: the loop asks memory for a hint, and stores the good case."""
    from app.agent_loop import InvestigateLoop
    from app.policy import Policy
    from app.schemas import ToolCall

    class SpyMemory:
        def __init__(self): self.recalled = None; self.remembered = None
        def recall(self, q): self.recalled = q; return ["list_all_pods"]
        def remember(self, q, tools): self.remembered = (q, tools)

    class FakeExec:
        def run_readonly(self, argv, timeout=30): return "NAME READY\npod 1/1"

    class FakeLLM:
        def __init__(self): self._s = [ToolCall(tool="list_all_pods"), ToolCall(tool="final")]
        def next_step(self, q, c, h, timeout=None):
            # the recall hint must be present in the history passed to the planner
            assert "list_all_pods" in h
            return self._s.pop(0)
        def synthesize(self, q, h, timeout=None): return "all good"

    spy = SpyMemory()
    loop = InvestigateLoop(FakeLLM(), Policy(cfg["allowlist"], cfg["investigation"]["tools"]),
                           FakeExec(), "default", dict(cfg["investigation"]), memory=spy)
    res = loop.run("are all pods up", "bastion")
    assert res.allowed
    assert spy.recalled == "are all pods up"
    assert spy.remembered and "list_all_pods" in spy.remembered[1]


def test_cosine_basic():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([], [1]) == 0.0
