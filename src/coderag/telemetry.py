"""Persist query telemetry so token consumption is observable (spec §17, §24)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from coderag.context.package import ContextPackage
from coderag.db.models import LLMRequest, QueryRecord, RetrievalResult
from coderag.llm.base import Usage
from coderag.retrieval.base import Candidate


def record_query(
    session: Session,
    repository_id: int,
    query_text: str,
    mode: str,
    retrieval_latency_ms: float,
    package: ContextPackage | None = None,
    candidates: list[Candidate] | None = None,
) -> QueryRecord:
    acct = package.accounting if package else None
    record = QueryRecord(
        repository_id=repository_id,
        query_text=query_text,
        mode=mode,
        candidates_found=acct.candidates_found if acct else (len(candidates or [])),
        candidates_selected=acct.candidates_selected if acct else 0,
        candidate_tokens=acct.candidate_tokens if acct else 0,
        context_tokens=acct.context_tokens if acct else 0,
        dropped_tokens=acct.dropped_tokens if acct else 0,
        baseline_tokens=acct.baseline_tokens if acct else 0,
        baseline_files=acct.baseline_files if acct else 0,
        retrieval_latency_ms=retrieval_latency_ms,
    )
    session.add(record)
    session.flush()

    if candidates is not None:
        selected = set(package.selected_symbol_ids()) if package else set()
        for rank, c in enumerate(candidates, start=1):
            session.add(
                RetrievalResult(
                    query_id=record.id,
                    symbol_id=c.symbol_id,
                    rank=rank,
                    score=c.fused_score,
                    reasons=sorted(c.reasons),
                    selected=c.symbol_id in selected,
                )
            )
        session.flush()
    return record


def record_llm_request(
    session: Session, query_id: int | None, provider: str, usage: Usage
) -> LLMRequest:
    row = LLMRequest(
        query_id=query_id,
        provider=provider,
        model=usage.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        latency_ms=usage.latency_ms,
        success=usage.success,
        error=usage.error,
    )
    session.add(row)
    session.flush()
    return row
