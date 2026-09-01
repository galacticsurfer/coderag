"""Context package value objects + token accounting.

The ``ContextPackage`` is exactly what would be sent to the LLM (its
``prompt_text``), plus the structured breakdown and the token accounting that
makes savings observable (spec §17).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from coderag.retrieval.base import Candidate

# category -> (priority tier, human title). Lower tier = higher priority.
CATEGORY_ORDER: dict[str, tuple[int, str]] = {
    "target": (1, "TARGET SYMBOL"),
    "changed": (2, "CHANGED CODE"),
    "implementation": (3, "IMPLEMENTATION"),
    "dependencies": (4, "DIRECT DEPENDENCIES"),
    "callers": (5, "CALLERS"),
    "tests": (6, "TESTS"),
    "semantic": (7, "SEMANTICALLY SIMILAR CODE"),
    "additional": (8, "ADDITIONAL RELEVANT CODE"),
}


@dataclass
class ContextEntry:
    candidate: Candidate
    category: str
    source_code: str
    tokens: int


@dataclass
class TokenAccounting:
    query_tokens: int = 0
    candidates_found: int = 0
    candidates_selected: int = 0
    candidate_tokens: int = 0      # tokens across ALL retrieved candidates
    context_tokens: int = 0        # tokens across SELECTED code
    dropped_tokens: int = 0        # tokens of candidates dropped by budget/dedup
    final_prompt_tokens: int = 0   # tokens of the fully assembled prompt
    # Counterfactual baseline: reading the whole files the selected symbols live in
    # (what an agent without retrieval would have loaded).
    baseline_tokens: int = 0
    baseline_files: int = 0

    @property
    def token_reduction_from_candidates(self) -> float:
        if self.candidate_tokens <= 0:
            return 0.0
        return round(100.0 * (1.0 - self.context_tokens / self.candidate_tokens), 1)

    @property
    def tokens_saved_vs_files(self) -> int:
        """Tokens avoided versus opening the whole files (never negative-by-surprise)."""
        return self.baseline_tokens - self.context_tokens

    @property
    def reduction_vs_files(self) -> float:
        if self.baseline_tokens <= 0:
            return 0.0
        return round(100.0 * (1.0 - self.context_tokens / self.baseline_tokens), 1)

    def as_dict(self) -> dict:
        return {
            "query_tokens": self.query_tokens,
            "candidates_found": self.candidates_found,
            "candidates_selected": self.candidates_selected,
            "candidate_tokens": self.candidate_tokens,
            "context_tokens": self.context_tokens,
            "dropped_tokens": self.dropped_tokens,
            "final_prompt_tokens": self.final_prompt_tokens,
            "token_reduction_from_candidates": self.token_reduction_from_candidates,
            "baseline_tokens": self.baseline_tokens,
            "baseline_files": self.baseline_files,
            "tokens_saved_vs_files": self.tokens_saved_vs_files,
            "reduction_vs_files": self.reduction_vs_files,
        }


@dataclass
class ContextPackage:
    query: str
    entries: list[ContextEntry] = field(default_factory=list)
    dropped: list[Candidate] = field(default_factory=list)
    prompt_text: str = ""
    accounting: TokenAccounting = field(default_factory=TokenAccounting)

    def selected_symbol_ids(self) -> list[int]:
        return [e.candidate.symbol_id for e in self.entries]

    def sections(self) -> list[tuple[str, list[ContextEntry]]]:
        """Entries grouped by category, in priority order."""
        groups: dict[str, list[ContextEntry]] = {}
        for e in self.entries:
            groups.setdefault(e.category, []).append(e)
        ordered = sorted(groups.items(), key=lambda kv: CATEGORY_ORDER[kv[0]][0])
        return [(CATEGORY_ORDER[c][1], entries) for c, entries in ordered]
