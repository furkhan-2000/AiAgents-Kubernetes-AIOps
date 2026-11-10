# 11. RAG eval, deep — concepts, interview Q&A, real scenarios

A plain-English, market-grounded guide to evaluating RAG. Pairs with `docs/10-rag.md`
(how our RAG works) and `eval/` (our deterministic eval exporter). Thresholds and
numbers here reflect 2026 industry practice (sources at the bottom).

---

## The one idea
You can't improve what you can't measure. **RAG eval = give the RAG a test (questions
with known-right answers) and score it** — so you know if it's good, and catch it getting
worse after a change.

**Always split the score in two** (the #1 thing interviewers probe):

```mermaid
flowchart LR
    Q[user question] --> R[Retrieval<br/>did we FETCH the right stuff?]
    R --> G[Generation<br/>did the LLM ANSWER correctly?]
    R -. measure .-> MR[recall@K, precision@K, MRR]
    G -. measure .-> MG[faithfulness, answer relevancy]
```

Measure the halves **separately** so you know which to fix. Wrong answer? Either
retrieval fetched junk (fix search) **or** the context was fine but the model
hallucinated (fix prompt/model). One number can't tell you which — two can.

---

## The 6 metrics jobs name

**Retrieval metrics** — classic search math, deterministic, no LLM needed:
| Metric | Plain meaning |
|--------|---------------|
| **Recall@K** | of the chunks needed, how many appeared in the top-K? (missing info → low) |
| **Precision@K** | of the top-K fetched, how many were actually relevant? (junk → low) |
| **MRR / NDCG** | was the *best* chunk near the **top**, not buried at #9? |

**Generation metrics** — the RAGAS framework, usually LLM-as-judge:
| Metric | Plain meaning | Market threshold |
|--------|---------------|------------------|
| **Faithfulness** | every claim is backed by the context (no hallucination) — **primary metric** | ≥ 0.75 |
| **Answer Relevancy** | the answer actually addresses the question | ≥ 0.8 |
| **Context Precision** | retrieved chunks were relevant | ≥ 0.7 |
| **Context Recall** | retrieved chunks held all the needed info | ≥ 0.8 |

**RAGAS** is the dominant tool; it is *reference-free* — it can score with an LLM judge
without a hand-written perfect answer for every case. Alternatives: DeepEval, TruLens,
ARES; commercial: LangSmith, Arize Phoenix, Braintrust.

**Diagnostic trick:** context precision/recall grade the **retrieval**; faithfulness /
answer-relevancy grade the **generation**. Read together → you can tell a retrieval
failure from a generation failure.

---

## Real-world numbers (quote these)
From contextual-retrieval research: contextual embeddings cut top-20 retrieval failure
**35%**; + BM25 (hybrid) → **49%**; **+ reranking → 67%**. → **Reranking is the single
highest-ROI quality lever** in most RAG systems.

---

## Interview questions (the tough ones + model answers)

**Q1 — "Your RAG gives a wrong answer. How do you debug it?"**
Split retrieval vs generation. Check context recall/precision first: if the right chunk
wasn't retrieved → retrieval bug (chunking / embeddings / reranker). If the chunk *was*
there but the answer's wrong → generation bug (prompt / model), shown by low faithfulness.

**Q2 — "How do you pick chunk size?"**
Start **300–500 tokens, 10–20% overlap**, prepend the heading path so each chunk carries
context. Then **let evaluation move it** — measure recall@K at different sizes, don't guess.
Too big dilutes relevance; too small strands facts across boundaries. Poor chunking is the
most common root cause of RAG failures.

**Q3 — "What is reranking and when do you add it?"**
A cross-encoder (or LLM) reads query+chunk **together** and reorders the top-k so the best
is #1. Add it when retrieval returns many roughly-close chunks. Highest-ROI lever (67%
failure reduction). LLM rerankers are smarter but slow/costly — reserve for low-volume,
high-value queries.

**Q4 — "A 500-PDF corpus — you can't hand-write every eval case. How do you build the set?"**
Auto-generate: run an LLM over each chunk ("write 2 questions this answers") — the source
chunk is the ground truth. Mine user logs for real questions + clicked docs. Hand-curate
only a small **golden set** (~50–100 critical cases). RAGAS / LlamaIndex can generate a
test set from the corpus.

**Q5 — "How do you A/B test a new chunker/reranker safely in prod?"**
Shadow traffic or a % split; run both pipelines; compare faithfulness + recall@K on live
queries; gate rollout on the metric. Keep a frozen golden set as a regression gate so a
change can't silently degrade quality.

**Q6 — "Faithfulness is high but users are unhappy. Why?"**
Faithful ≠ relevant. The answer is grounded but may not address intent (low answer
relevancy), or retrieval missed the real need (low recall). Metrics must be read together,
never one alone.

**Q7 — "LLM-as-judge is slow/expensive. Alternatives?"**
Deterministic retrieval metrics (recall@K, MRR) need no LLM. Use a small cheap judge,
cache results, sample a % of traffic, and validate the judge against a human-labeled
subset once.

---

## Real-time scenarios (what actually happens)
- **"After re-ingesting docs, answers got worse"** → recall@K drops → chunking/embedding
  changed → caught by the dashboard before users complain. (Exactly what our `eval/`
  exporter watches — see `docs/10-rag.md`.)
- **"Model hallucinates on finance numbers"** → faithfulness drops → tighten the prompt
  ("answer only from context"), add reranking so the right chunk is on top.
- **"Retrieval slow at 1M docs"** → swap exact KNN → an ANN index (HNSW); add caching;
  watch the latency KPI.
- **"Users ask things the docs don't cover"** → low context recall → the model should say
  "I don't know" — measured, not guessed.

---

## How this maps to OUR project
We retrieve **tool names**, not document chunks, at tiny scale — so we use the simple,
deterministic half of this (hit-rate / precision / recall / latency via `eval/rag_eval.py`),
hand-write the eval set, and skip the LLM-judge + reranker (see `docs/10-rag.md` for why).
The concepts above are what you'd add for a large **document-RAG**.

**Interview pitch for this project:** *"I built an offline RAG with a sqlite-vec store,
allowlist-gated retrieval, fail-open, and a deterministic eval harness exporting
hit-rate/precision to Prometheus/Grafana — so RAG quality is a monitored KPI, not a guess."*

---

## Sources
- RAG Evaluation Metrics in 2026 — FutureAGI: https://futureagi.com/blog/rag-evaluation-metrics-2025/
- RAG Evaluation: Metrics, Tools, Context Gap — Atlan: https://atlan.com/know/how-to-evaluate-rag-systems-explained/
- RAGAS: how to measure your RAG pipeline — DEV: https://dev.to/m_t_ramkrushna/ragas-how-to-actually-measure-your-rag-pipeline-faithfulness-context-precisionrecall-relevancy-26
- RAG Evaluation 2026: Methods, Metrics, Frameworks — DataVLab: https://datavlab.ai/post/rag-evaluation-methods-metrics-2026-guide
- 100 LLM & AI System Interview Questions (Senior): https://himanshuai.substack.com/p/100-llm-and-ai-system-interview-questions
- RAG Interview Questions 2026 — Hirist: https://www.hirist.tech/blog/top-rag-interview-questions-and-answers/
- RAG Interview Questions & Answers 2026 Senior Guide — Tuition.in: https://www.tuition.in/blog/rag-retrieval-augmented-generation-interview-questions-2026/
