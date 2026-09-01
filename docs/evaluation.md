# Evaluation

CodeRAG's premise — *fewer tokens at equal quality* — must be measured. The harness
(`evaluation/`, `coderag eval` / `coderag benchmark`) does two things.

## 1. Retrieval quality

Dataset format (`examples/eval/eval_dataset.json`):

```json
[
  {
    "question": "Where is retry logic for failed payments implemented?",
    "expected_symbols": ["services.payment_service.PaymentService.retry_payment"]
  }
]
```

Metrics: **Recall@1/3/5/10** and **MRR**, plus average **retrieved-candidate tokens** and
**final context tokens** and retrieval latency. Substitute your own dataset by pointing
`coderag eval --dataset <path>` at it.

## 2. Baseline vs Code-RAG token comparison

`coderag benchmark --compare-baseline` compares, per question:

| | BASELINE (naive) | CODE-RAG |
|---|---|---|
| input tokens | whole target files / broad context | budgeted, deduplicated context |
| output tokens | (equal task) | (equal task) |
| retrieval quality | n/a | Recall@K / MRR |
| latency | — | retrieval + context build |

The baseline is deliberately naive (concatenate the target files) to represent "just send the
code". We report the **measured** reduction; we never claim savings without numbers.

## 3. `--measure`: what reading whole files would have cost

The baseline that matters for an agent like Claude Code is *"instead of this context, I'd have
opened the files."* At index time CodeRAG records each file's token count, so every query can
report that counterfactual with no file I/O:

```bash
coderag context "why can payment retry leave an invoice pending?" --measure
```

```
approach                                        input tokens
read the 8 whole file(s) containing this code          1,654
CodeRAG budgeted context                               1,516
CodeRAG full prompt (incl. scaffolding)                2,648
saved 138 tokens (8.3%) vs opening those files
```

`baseline_tokens` / `baseline_files` / `saved_vs_files` / `reduction_vs_files` are persisted per
query, exposed on `GET /queries` and `GET /metrics`, shown on the dashboard, and returned in the
MCP `coderag_context` response.

### Read this honestly

The numbers above are from the **bundled demo repo, and they are not flattering** — 8.3%, and
the *full prompt* is larger than the files it replaces. That is a truthful result, not a bug,
and it tells you exactly when this system pays off:

- **The baseline is deliberately conservative.** It counts only the files the selected symbols
  came from — not the files an agent would have grepped through and discarded first.
- **Small repos won't show savings.** With 11 tiny files, one-hop graph expansion reaches most
  of the repo, so "relevant symbols" ≈ "the whole thing", and fixed prompt scaffolding
  (~1.1k tokens here) dominates.
- **Savings come from the ratio of file size to symbol size.** A 900-line service file where you
  need one 40-line method is where budgeted retrieval wins; on a real backend that ratio is
  routinely 10–50×.
- **A tighter `MAX_CONTEXT_TOKENS` or `GRAPH_MAX_CANDIDATES` increases the saving** at some
  recall risk — measure both with `coderag eval` before choosing.

So: **run `--measure` on your own repository** before believing any headline number, including
ours. That is the entire point of shipping the measurement rather than a claim.

## Deterministic-first quality

We avoid leaning on "LLM judges". Retrieval metrics are exact-match on expected symbols. For
code-fixing tasks (Phase 10) the ultimate check is deterministic: **tests pass + the analyzer
finding disappears + no new lint failures**.
