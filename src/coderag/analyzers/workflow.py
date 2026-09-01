"""Analyzer-driven fix workflow (Phase 10).

Flow (spec §21): finding -> identify file/line -> identify enclosing symbol ->
Code-RAG context -> (optional) LLM patch. Patch *application* and re-verification
are intentionally left to the caller as an interface; the model is never allowed
to loop unbounded — attempts are capped by ``MAX_FIX_ATTEMPTS``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from coderag.analyzers.base import Finding
from coderag.context.package import ContextPackage
from coderag.core.config import Settings, get_settings
from coderag.db.models import Repository, Symbol


def map_finding_to_symbol(session: Session, repository_id: int, finding: Finding):
    """Return the smallest symbol whose line range encloses the finding's line."""
    enclosing = session.scalars(
        select(Symbol).where(
            Symbol.repository_id == repository_id,
            Symbol.file_path == finding.file_path,
            Symbol.start_line <= finding.line,
            Symbol.end_line >= finding.line,
        )
    ).all()
    if enclosing:
        # smallest span = most specific; prefer non-module
        return min(
            enclosing,
            key=lambda s: (s.symbol_type == "module", s.end_line - s.start_line),
        )
    # fall back to the module symbol for that file
    return session.scalar(
        select(Symbol).where(
            Symbol.repository_id == repository_id,
            Symbol.file_path == finding.file_path,
            Symbol.symbol_type == "module",
        )
    )


@dataclass
class FixContext:
    finding: Finding
    symbol_qualified_name: str | None
    package: ContextPackage


def build_fix_context(
    session: Session, repo: Repository, finding: Finding,
    settings: Settings | None = None,
) -> FixContext:
    from coderag.context.builder import ContextBuilder
    from coderag.service import get_engine

    settings = settings or get_settings()
    symbol = map_finding_to_symbol(session, repo.id, finding)
    query = symbol.qualified_name if symbol else finding.message
    finding_text = (
        finding.describe()
        + "\n\nFix this finding WITHOUT changing existing behavior. "
        + "Return a minimal patch and explain the change."
    )
    engine = get_engine(settings, semantic=True, graph=True)
    outcome = engine.search(session, repo.id, query, top_n=None)
    package = ContextBuilder(session, settings=settings).build(
        query, outcome.candidates, repo,
        changed_symbol_ids={symbol.id} if symbol else None,
        finding=finding_text,
    )
    return FixContext(
        finding=finding,
        symbol_qualified_name=symbol.qualified_name if symbol else None,
        package=package,
    )


def propose_fix(fix: FixContext, provider, max_output_tokens: int = 1024) -> str:
    """Single LLM attempt to propose a patch for a finding (requires a provider)."""
    from coderag.llm.base import LLMRequest

    return provider.generate(
        LLMRequest(prompt=fix.package.prompt_text, max_tokens=max_output_tokens)
    ).text


def run_fix_loop(fixes: list[FixContext], provider, settings: Settings | None = None):
    """Bounded fix proposals — capped at MAX_FIX_ATTEMPTS to prevent infinite loops.

    Application/re-verification of patches is the caller's responsibility (pass
    verified patches back in); this MVP produces proposals only.
    """
    settings = settings or get_settings()
    proposals = []
    for fix in fixes[: settings.max_fix_attempts]:
        proposals.append({
            "finding": fix.finding.describe(),
            "symbol": fix.symbol_qualified_name,
            "patch": propose_fix(fix, provider, settings.llm_max_output_tokens),
        })
    return proposals
