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

## Deterministic-first quality

We avoid leaning on "LLM judges". Retrieval metrics are exact-match on expected symbols. For
code-fixing tasks (Phase 10) the ultimate check is deterministic: **tests pass + the analyzer
finding disappears + no new lint failures**.
