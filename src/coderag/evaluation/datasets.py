"""Evaluation dataset format + loader.

A dataset is a JSON list of cases; each case has a ``question`` and the
``expected_symbols`` that should be retrieved. Expected symbols may be full
qualified names or suffixes (see matching in ``harness``). Point the eval at your
own dataset to measure retrieval on your codebase.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEMO_DATASET = str(
    Path(__file__).resolve().parents[3] / "examples" / "eval" / "eval_dataset.json"
)


@dataclass
class EvalCase:
    question: str
    expected_symbols: list[str]


def load_dataset(path: str) -> list[EvalCase]:
    data = json.loads(Path(path).read_text())
    cases = []
    for item in data:
        cases.append(
            EvalCase(
                question=item["question"],
                expected_symbols=list(item["expected_symbols"]),
            )
        )
    return cases
