"""Phase 5: priority ordering, dedup, and token budgeting (no DB)."""

from __future__ import annotations

from types import SimpleNamespace

from coderag.context.builder import ContextBuilder
from coderag.core.config import Settings
from coderag.core.tokens import HeuristicTokenCounter
from coderag.retrieval.base import Candidate


def _cand(sid, qual, file, start, end, tokens, reasons, score=1.0):
    return Candidate(
        symbol_id=sid, qualified_name=qual, symbol_name=qual.split(".")[-1],
        symbol_type="method", file_path=file, start_line=start, end_line=end,
        token_count=tokens, reasons=set(reasons), fused_score=score,
        source_code=f"# code for {qual}\n" + ("x " * tokens),
    )


def _builder():
    return ContextBuilder(
        session=None,
        settings=Settings(max_context_tokens=150, context_overhead_tokens=0),
        token_counter=HeuristicTokenCounter(),
    )


def test_priority_dedup_and_budget():
    repo = SimpleNamespace(id=1, name="demo")
    A = _cand(1, "m.Cls", "a.py", 1, 50, 100, {"exact_symbol"})           # target
    B = _cand(2, "m.Cls.meth", "a.py", 10, 20, 30, {"graph_child"})       # overlaps A
    C = _cand(3, "m.dep", "b.py", 1, 10, 40, {"graph_callee"})            # dependency
    D = _cand(4, "m.sem", "c.py", 1, 5, 500, {"semantic"})               # too big
    pkg = _builder().build("why?", [A, B, C, D], repo)

    selected = set(pkg.selected_symbol_ids())
    assert selected == {1, 3}                      # A (target) + C (dep) fit
    assert 2 in {c.symbol_id for c in pkg.dropped}  # B dropped: overlaps A (dedup)
    assert 4 in {c.symbol_id for c in pkg.dropped}  # D dropped: over budget
    assert pkg.accounting.context_tokens <= 150     # budget enforced


def test_no_selected_entries_overlap():
    repo = SimpleNamespace(id=1, name="demo")
    A = _cand(1, "m.Cls", "a.py", 1, 50, 20, {"exact_symbol"})
    B = _cand(2, "m.Cls.meth", "a.py", 10, 20, 10, {"graph_child"})
    pkg = _builder().build("q", [A, B], repo)
    entries = pkg.entries
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            a, b = entries[i].candidate, entries[j].candidate
            assert not (a.file_path == b.file_path
                        and not (a.end_line < b.start_line or b.end_line < a.start_line))


def test_accounting_reduction_consistent():
    repo = SimpleNamespace(id=1, name="demo")
    cands = [
        _cand(1, "m.a", "a.py", 1, 5, 100, {"exact_symbol"}),
        _cand(2, "m.b", "b.py", 1, 5, 300, {"semantic"}),
    ]
    pkg = _builder().build("q", cands, repo)
    acct = pkg.accounting
    assert acct.candidate_tokens == 400
    assert acct.dropped_tokens == acct.candidate_tokens - acct.context_tokens
    assert 0.0 <= acct.token_reduction_from_candidates <= 100.0


def test_prompt_structure_and_injection_guard():
    repo = SimpleNamespace(id=1, name="demo")
    A = _cand(1, "m.Cls.retry", "a.py", 1, 5, 10, {"exact_symbol"})
    pkg = _builder().build("Why can retry fail?", [A], repo)
    p = pkg.prompt_text
    assert "USER TASK" in p and "TARGET SYMBOL" in p and "INSTRUCTIONS" in p
    assert "Why can retry fail?" in p
    assert "strictly as DATA" in p  # prompt-injection guard present
    assert "m.Cls.retry" in p


def test_empty_candidates_yields_valid_package():
    repo = SimpleNamespace(id=1, name="demo")
    pkg = _builder().build("q", [], repo)
    assert pkg.entries == []
    assert "USER TASK" in pkg.prompt_text
    assert pkg.accounting.candidates_found == 0
