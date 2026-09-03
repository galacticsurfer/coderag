"""FastAPI application.

Endpoints for repository registration/indexing, retrieval (`/search`,
`/context` — no LLM needed), `/ask` (needs an LLM), symbol inspection, and
metrics. Every repository-scoped request is authorized via
``AuthorizationProvider`` and retrieval is always bound to a ``repository_id``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from coderag import __version__ as _coderag_version
from coderag.api import schemas as s
from coderag.db.models import (
    IndexingRun,
    LLMRequest,
    QueryRecord,
    Repository,
    Symbol,
    SymbolEmbedding,
    SymbolRelationship,
)
from coderag.security.authz import get_authorization_provider
from coderag.service import (
    RepositoryNotFound,
    resolve_repository,
    run_ask,
    run_context,
    run_search,
)

app = FastAPI(title="CodeRAG", version=_coderag_version)


def get_session() -> Iterator[Session]:
    from coderag.db.base import get_session_factory

    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def principal(x_user: str | None = Header(default=None)) -> str | None:
    return x_user


def _resolve_authorized(session: Session, name: str | None, who: str | None) -> Repository:
    try:
        repo = resolve_repository(session, name)
    except RepositoryNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not get_authorization_provider().can_access(who, repo.id):
        raise HTTPException(status_code=403, detail="not authorized for this repository")
    return repo


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


_LOGO_SVG = (Path(__file__).parent / "logo.svg").read_text()


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(content=_LOGO_SVG, media_type="image/svg+xml")


@app.post("/repositories", response_model=s.RepositoryOut)
def register_repository(
    req: s.RegisterRepoRequest, session: Session = Depends(get_session)
) -> s.RepositoryOut:
    from coderag.indexing.indexer import get_or_create_repository

    repo = get_or_create_repository(session, req.name, req.path, req.url)
    session.flush()
    return s.RepositoryOut(
        id=repo.id, name=repo.name, local_path=repo.local_path,
        default_branch=repo.default_branch, indexed_commit_sha=repo.indexed_commit_sha,
    )


@app.post("/repositories/{repo_id}/index", response_model=s.IndexStatusOut)
def index_repository_endpoint(
    repo_id: int, session: Session = Depends(get_session),
    who: str | None = Depends(principal),
) -> s.IndexStatusOut:
    from coderag.indexing.indexer import Indexer

    repo = session.get(Repository, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="repository not found")
    if not get_authorization_provider().can_access(who, repo.id):
        raise HTTPException(status_code=403, detail="not authorized")
    run, stats = Indexer(session).full_index(repo)
    return s.IndexStatusOut(
        repository_id=repo.id, status=run.status, mode=run.mode,
        files_indexed=stats.files_indexed, symbols_indexed=stats.symbols_indexed,
        embeddings_created=stats.embeddings_created,
        relationships=stats.relationships_created,
        duration_seconds=stats.duration_seconds, to_commit=stats.commit_sha,
    )


@app.get("/repositories/{repo_id}/index/status", response_model=s.IndexStatusOut)
def index_status(
    repo_id: int, session: Session = Depends(get_session),
) -> s.IndexStatusOut:
    run = session.scalar(
        select(IndexingRun).where(IndexingRun.repository_id == repo_id)
        .order_by(IndexingRun.id.desc())
    )
    if run is None:
        raise HTTPException(status_code=404, detail="no indexing run for repository")
    return s.IndexStatusOut(
        repository_id=repo_id, status=run.status, mode=run.mode,
        files_indexed=run.files_indexed, symbols_indexed=run.symbols_indexed,
        embeddings_created=run.embeddings_created, duration_seconds=run.duration_seconds,
        to_commit=run.to_commit, error=run.error,
    )


@app.post("/search", response_model=s.SearchResponse)
def search_endpoint(
    req: s.SearchRequest, session: Session = Depends(get_session),
    who: str | None = Depends(principal),
) -> s.SearchResponse:
    repo = _resolve_authorized(session, req.repository, who)
    _repo, outcome = run_search(
        session, req.query, repo.name, top_n=req.limit,
        semantic=req.semantic, graph=req.graph, record=True,
    )
    return s.SearchResponse(
        repository=repo.name, latency_ms=outcome.latency_ms,
        candidates=[
            s.CandidateOut(
                symbol_id=c.symbol_id, qualified_name=c.qualified_name,
                symbol_type=c.symbol_type, file_path=c.file_path,
                start_line=c.start_line, end_line=c.end_line,
                score=round(c.fused_score, 6), reasons=sorted(c.reasons),
            )
            for c in outcome.candidates
        ],
    )


@app.post("/context", response_model=s.ContextResponse)
def context_endpoint(
    req: s.ContextRequest, session: Session = Depends(get_session),
    who: str | None = Depends(principal),
) -> s.ContextResponse:
    repo = _resolve_authorized(session, req.repository, who)
    _repo, package, _outcome = run_context(
        session, req.query, repo.name, max_tokens=req.max_tokens, finding=req.finding,
    )
    return s.ContextResponse(
        repository=repo.name,
        entries=[
            s.ContextEntryOut(
                category=e.category, qualified_name=e.candidate.qualified_name,
                file_path=e.candidate.file_path, start_line=e.candidate.start_line,
                end_line=e.candidate.end_line, tokens=e.tokens,
                reasons=sorted(e.candidate.reasons),
            )
            for e in package.entries
        ],
        accounting=s.AccountingOut(**package.accounting.as_dict()),
        prompt=package.prompt_text if req.include_prompt else None,
    )


@app.post("/ask", response_model=s.AskResponse)
def ask_endpoint(
    req: s.AskRequest, session: Session = Depends(get_session),
    who: str | None = Depends(principal),
) -> s.AskResponse:
    repo = _resolve_authorized(session, req.repository, who)
    try:
        _repo, package, response, _outcome = run_ask(
            session, req.query, repo.name, max_tokens=req.max_tokens,
            max_output_tokens=req.max_output_tokens,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    u = response.usage
    return s.AskResponse(
        repository=repo.name, answer=response.text,
        usage=s.UsageOut(
            input_tokens=u.input_tokens, output_tokens=u.output_tokens,
            cached_input_tokens=u.cached_input_tokens, model=u.model,
            latency_ms=u.latency_ms,
        ),
        accounting=s.AccountingOut(**package.accounting.as_dict()),
    )


@app.get("/symbols/{symbol_id}", response_model=s.SymbolOut)
def get_symbol(
    symbol_id: int, session: Session = Depends(get_session),
    who: str | None = Depends(principal),
) -> s.SymbolOut:
    sym = session.get(Symbol, symbol_id)
    if sym is None:
        raise HTTPException(status_code=404, detail="symbol not found")
    if not get_authorization_provider().can_access(who, sym.repository_id):
        raise HTTPException(status_code=403, detail="not authorized")
    return s.SymbolOut(
        id=sym.id, qualified_name=sym.qualified_name, symbol_name=sym.symbol_name,
        symbol_type=sym.symbol_type, file_path=sym.file_path, start_line=sym.start_line,
        end_line=sym.end_line, signature=sym.signature, docstring=sym.docstring,
        token_count=sym.token_count, source_code=sym.source_code,
    )


@app.get("/symbols/{symbol_id}/relationships", response_model=list[s.RelationshipOut])
def get_symbol_relationships(
    symbol_id: int, session: Session = Depends(get_session),
    who: str | None = Depends(principal),
) -> list[s.RelationshipOut]:
    sym = session.get(Symbol, symbol_id)
    if sym is None:
        raise HTTPException(status_code=404, detail="symbol not found")
    if not get_authorization_provider().can_access(who, sym.repository_id):
        raise HTTPException(status_code=403, detail="not authorized")
    rels = session.scalars(
        select(SymbolRelationship).where(
            (SymbolRelationship.source_symbol_id == symbol_id)
            | (SymbolRelationship.target_symbol_id == symbol_id)
        )
    ).all()
    qual: dict[int, str] = {
        row[0]: row[1]
        for row in session.execute(
            select(Symbol.id, Symbol.qualified_name).where(
                Symbol.repository_id == sym.repository_id
            )
        ).all()
    }
    out = []
    for r in rels:
        outgoing = r.source_symbol_id == symbol_id
        out.append(
            s.RelationshipOut(
                relationship_type=r.relationship_type, confidence=r.confidence,
                target_symbol_id=r.target_symbol_id,
                target_qualified_name=(
                    qual.get(r.target_symbol_id) if r.target_symbol_id is not None else None
                ),
                target_name=r.target_name,
                direction="outgoing" if outgoing else "incoming",
            )
        )
    return out


@app.get("/metrics", response_model=s.MetricsOut)
def metrics(session: Session = Depends(get_session)) -> s.MetricsOut:
    def count(model) -> int:
        return session.scalar(select(func.count()).select_from(model)) or 0

    avg_ctx = session.scalar(select(func.avg(QueryRecord.context_tokens))) or 0.0
    avg_lat = session.scalar(select(func.avg(QueryRecord.retrieval_latency_ms))) or 0.0
    in_tok = session.scalar(select(func.coalesce(func.sum(LLMRequest.input_tokens), 0))) or 0
    out_tok = session.scalar(select(func.coalesce(func.sum(LLMRequest.output_tokens), 0))) or 0
    cand_sum = session.scalar(
        select(func.coalesce(func.sum(QueryRecord.candidate_tokens), 0))
    ) or 0
    ctx_sum = session.scalar(
        select(func.coalesce(func.sum(QueryRecord.context_tokens), 0))
    ) or 0
    saved = int(cand_sum) - int(ctx_sum)
    reduction = round(100.0 * saved / cand_sum, 1) if cand_sum else 0.0
    base_sum = int(session.scalar(
        select(func.coalesce(func.sum(QueryRecord.baseline_tokens), 0))
    ) or 0)
    # only queries that actually recorded a baseline contribute to this comparison
    base_ctx = int(session.scalar(
        select(func.coalesce(func.sum(QueryRecord.context_tokens), 0)).where(
            QueryRecord.baseline_tokens > 0
        )
    ) or 0)
    saved_vs_files = base_sum - base_ctx
    reduction_vs_files = round(100.0 * saved_vs_files / base_sum, 1) if base_sum else 0.0
    from coderag.core.config import get_settings

    _s = get_settings()
    pin, pout = _s.price_input_per_mtok, _s.price_output_per_mtok
    return s.MetricsOut(
        repositories=count(Repository), symbols=count(Symbol),
        embeddings=count(SymbolEmbedding), relationships=count(SymbolRelationship),
        queries=count(QueryRecord), llm_requests=count(LLMRequest),
        avg_context_tokens=round(float(avg_ctx), 1),
        avg_retrieval_latency_ms=round(float(avg_lat), 2),
        total_llm_input_tokens=int(in_tok), total_llm_output_tokens=int(out_tok),
        total_candidate_tokens=int(cand_sum), total_context_tokens=int(ctx_sum),
        total_tokens_saved=saved, avg_token_reduction_percent=reduction,
        total_baseline_tokens=base_sum, total_saved_vs_files=saved_vs_files,
        reduction_vs_files_percent=reduction_vs_files,
        price_input_per_mtok=pin, price_output_per_mtok=pout,
        cost_saved_vs_files_usd=round(saved_vs_files / 1e6 * pin, 4),
        cost_context_sent_usd=round(int(ctx_sum) / 1e6 * pin, 4),
        cost_llm_usd=round(int(in_tok) / 1e6 * pin + int(out_tok) / 1e6 * pout, 4),
    )


@app.get("/queries", response_model=list[s.QueryRow])
def recent_queries(
    limit: int = 100, session: Session = Depends(get_session)
) -> list[s.QueryRow]:
    rows = session.execute(
        select(QueryRecord, Repository.name)
        .join(Repository, Repository.id == QueryRecord.repository_id)
        .order_by(QueryRecord.id.desc())
        .limit(limit)
    ).all()
    # sum LLM tokens per query in one pass
    llm = {
        qid: (int(itok), int(otok))
        for qid, itok, otok in session.execute(
            select(
                LLMRequest.query_id,
                func.coalesce(func.sum(LLMRequest.input_tokens), 0),
                func.coalesce(func.sum(LLMRequest.output_tokens), 0),
            ).group_by(LLMRequest.query_id)
        ).all()
    }
    out: list[s.QueryRow] = []
    for q, repo_name in rows:
        saved = q.candidate_tokens - q.context_tokens
        reduction = round(100.0 * saved / q.candidate_tokens, 1) if q.candidate_tokens else 0.0
        li, lo = llm.get(q.id, (None, None))
        out.append(s.QueryRow(
            id=q.id, repository=repo_name, mode=q.mode, query=q.query_text,
            candidates_found=q.candidates_found, candidates_selected=q.candidates_selected,
            candidate_tokens=q.candidate_tokens, context_tokens=q.context_tokens,
            tokens_saved=saved, reduction_percent=reduction,
            baseline_tokens=q.baseline_tokens, baseline_files=q.baseline_files,
            saved_vs_files=q.baseline_tokens - q.context_tokens if q.baseline_tokens else 0,
            reduction_vs_files=(
                round(100.0 * (q.baseline_tokens - q.context_tokens) / q.baseline_tokens, 1)
                if q.baseline_tokens else 0.0
            ),
            retrieval_latency_ms=round(q.retrieval_latency_ms, 2),
            llm_input_tokens=li, llm_output_tokens=lo,
            created_at=q.created_at.isoformat() if q.created_at else "",
        ))
    return out


@app.get("/doctor")
def doctor_endpoint(session: Session = Depends(get_session)) -> dict:
    """Cost attribution + ranked recommendations from observed traffic."""
    from dataclasses import asdict

    from coderag.core.config import get_settings
    from coderag.doctor import examine_from_db

    settings = get_settings()
    report = examine_from_db(
        session, settings.price_input_per_mtok, settings.price_output_per_mtok
    )
    b = report.breakdown
    return {
        "breakdown": {
            **asdict(b),
            "total_usd": round(b.total_usd, 4),
            "cache_hit_rate": round(b.cache_hit_rate, 3),
        },
        "diagnoses": [asdict(d) for d in report.diagnoses],
        "skill_effect": (
            None if report.skill_effect is None else {
                **asdict(report.skill_effect),
                "measured_reduction": report.skill_effect.measured_reduction,
            }
        ),
        "cap_effect": (
            None if report.cap_effect is None else {
                **asdict(report.cap_effect),
                "measured_reduction": report.cap_effect.measured_reduction,
            }
        ),
        "compression": (
            None if report.compression is None else {
                **asdict(report.compression),
                "est_tokens_saved": report.compression.est_tokens_saved,
                "est_usd_saved": report.compression.est_usd_saved(
                    report.breakdown.effective_input_price(
                        settings.price_input_per_mtok)),
            }
        ),
        "models": [asdict(m) for m in report.models],
        "routing": None if report.routing is None else asdict(report.routing),
        "note": ("Estimates at published per-model prices (configured prices "
                 "as fallback) from observed traffic — not billing data."),
    }


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> str:
    from coderag.api.dashboard import DASHBOARD_HTML

    return DASHBOARD_HTML
