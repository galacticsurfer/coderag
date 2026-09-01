"""Phase 10: static-analysis adapters + finding -> symbol -> fix context."""

from __future__ import annotations

import pytest

from coderag.analyzers.base import Finding
from coderag.analyzers.flake8_adapter import Flake8Analyzer
from coderag.analyzers.workflow import build_fix_context, map_finding_to_symbol, run_fix_loop


def test_flake8_finds_unused_import(tmp_path):
    analyzer = Flake8Analyzer()
    if not analyzer.available():
        pytest.skip("flake8 not installed")
    (tmp_path / "m.py").write_text("import os\n\n\ndef f():\n    return 1\n")
    findings = analyzer.analyze(str(tmp_path))
    assert any(f.code == "F401" for f in findings)  # unused import
    assert all(f.tool == "flake8" and f.line >= 1 for f in findings)


@pytest.mark.db
def test_map_finding_to_enclosing_symbol(db_session, demo_repo):
    from sqlalchemy import select

    from coderag.db.models import Symbol

    retry = db_session.scalar(
        select(Symbol).where(
            Symbol.qualified_name == "payments.payment_service.PaymentService.retry_payment"
        )
    )
    line = (retry.start_line + retry.end_line) // 2
    finding = Finding(file_path="payments/payment_service.py", line=line,
                      code="X", message="something", tool="test")
    sym = map_finding_to_symbol(db_session, demo_repo.id, finding)
    assert sym is not None
    assert sym.qualified_name.endswith("PaymentService.retry_payment")


@pytest.mark.db
def test_module_fallback_for_top_level_line(db_session, demo_repo):
    finding = Finding(file_path="payments/retry_policy.py", line=1,
                      code="F401", message="unused import", tool="flake8")
    sym = map_finding_to_symbol(db_session, demo_repo.id, finding)
    assert sym is not None  # falls back to module (or top-level symbol at line 1)


@pytest.mark.db
def test_build_fix_context_includes_finding_and_target(db_session, demo_repo):
    from sqlalchemy import select

    from coderag.db.models import Symbol

    retry = db_session.scalar(
        select(Symbol).where(
            Symbol.qualified_name == "payments.payment_service.PaymentService.retry_payment"
        )
    )
    line = (retry.start_line + retry.end_line) // 2
    finding = Finding(file_path="payments/payment_service.py", line=line,
                      code="R1710", message="inconsistent return", tool="pylint")
    fix = build_fix_context(db_session, demo_repo, finding)
    assert fix.symbol_qualified_name.endswith("PaymentService.retry_payment")
    assert "inconsistent return" in fix.package.prompt_text
    assert "STATIC ANALYSIS FINDING" in fix.package.prompt_text


@pytest.mark.db
def test_fix_loop_is_bounded(db_session, demo_repo):
    from coderag.core.config import Settings
    from coderag.llm.base import LLMProvider, LLMResponse, Usage

    class MockProvider(LLMProvider):
        name = "mock"

        def generate(self, request):
            return LLMResponse(text="--- patch ---", usage=Usage(model="mock"))

    findings = [
        Finding("payments/payment_service.py", 40, "X", "m", "t") for _ in range(10)
    ]
    fixes = [build_fix_context(db_session, demo_repo, f,
                               settings=Settings(max_fix_attempts=2)) for f in findings]
    proposals = run_fix_loop(fixes, MockProvider(), settings=Settings(max_fix_attempts=2))
    assert len(proposals) == 2  # capped at MAX_FIX_ATTEMPTS
    assert all(p["patch"] for p in proposals)
