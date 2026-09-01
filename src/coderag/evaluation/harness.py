"""Evaluation harness.

Retrieval quality (Recall@K, MRR) + token accounting, and a measured comparison
of a naive baseline ("send whole files") against the budgeted Code-RAG context.
We report measured numbers only — no unverified savings claims (spec §18/§19).
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from coderag.context.builder import ContextBuilder
from coderag.core.config import Settings, get_settings
from coderag.core.tokens import get_token_counter
from coderag.db.models import EvaluationRun, Repository, SourceFile
from coderag.evaluation.datasets import EvalCase
from coderag.git.repo import GitRepo
from coderag.retrieval.engine import build_engine


def _matches(qualified: str, expected: str) -> bool:
    q, e = qualified.lower(), expected.lower()
    return q == e or q.endswith("." + e) or e.endswith("." + q)


def _first_hit_rank(candidate_quals: list[str], expected: list[str]) -> int | None:
    for rank, qual in enumerate(candidate_quals, start=1):
        if any(_matches(qual, e) for e in expected):
            return rank
    return None


@dataclass
class RetrievalMetrics:
    n_cases: int = 0
    recall_at: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    avg_candidate_tokens: float = 0.0
    avg_context_tokens: float = 0.0
    avg_retrieval_latency_ms: float = 0.0
    per_case: list[dict] = field(default_factory=list)


def evaluate_retrieval(
    session: Session,
    repo: Repository,
    cases: list[EvalCase],
    settings: Settings | None = None,
    ks: tuple[int, ...] = (1, 3, 5, 10),
    *,
    semantic: bool = True,
    graph: bool = True,
) -> RetrievalMetrics:
    settings = settings or get_settings()
    engine = build_engine(
        settings,
        embedding_provider=_provider(settings) if semantic else None,
        with_graph=graph,
    )
    builder = ContextBuilder(session, settings=settings)

    hits: dict[int, list[int]] = {k: [] for k in ks}
    rrs: list[float] = []
    cand_tokens: list[int] = []
    ctx_tokens: list[int] = []
    latencies: list[float] = []
    per_case: list[dict] = []

    for case in cases:
        outcome = engine.search(session, repo.id, case.question, top_n=None)
        quals = [c.qualified_name for c in outcome.candidates]
        rank = _first_hit_rank(quals, case.expected_symbols)
        for k in ks:
            hits[k].append(1 if (rank is not None and rank <= k) else 0)
        rrs.append(1.0 / rank if rank else 0.0)
        latencies.append(outcome.latency_ms)
        cand_tokens.append(sum(c.token_count for c in outcome.candidates))
        pkg = builder.build(case.question, outcome.candidates, repo)
        ctx_tokens.append(pkg.accounting.context_tokens)
        per_case.append({
            "question": case.question, "first_hit_rank": rank,
            "candidate_tokens": cand_tokens[-1], "context_tokens": ctx_tokens[-1],
        })

    n = len(cases)
    return RetrievalMetrics(
        n_cases=n,
        recall_at={k: (sum(hits[k]) / n if n else 0.0) for k in ks},
        mrr=(sum(rrs) / n if n else 0.0),
        avg_candidate_tokens=(statistics.mean(cand_tokens) if cand_tokens else 0.0),
        avg_context_tokens=(statistics.mean(ctx_tokens) if ctx_tokens else 0.0),
        avg_retrieval_latency_ms=(statistics.mean(latencies) if latencies else 0.0),
        per_case=per_case,
    )


@dataclass
class BaselineComparison:
    n_cases: int
    baseline_kind: str
    avg_baseline_tokens: float
    avg_topfiles_tokens: float
    avg_rag_context_tokens: float
    avg_rag_prompt_tokens: float
    token_reduction_percent: float


def compare_baseline(
    session: Session,
    repo: Repository,
    cases: list[EvalCase],
    settings: Settings | None = None,
    topfiles: int = 3,
) -> BaselineComparison:
    """Compare Code-RAG context against naive baselines.

    Headline baseline is **broad repository context** (send every source file) —
    the thing you do without retrieval. We also report the "top-N whole files"
    baseline. Savings scale with repository size: on a tiny demo the whole-repo
    number is modest, but the *ratio* (RAG context vs the code you'd otherwise
    paste) is what generalises.
    """
    settings = settings or get_settings()
    engine = build_engine(settings, embedding_provider=_provider(settings), with_graph=True)
    builder = ContextBuilder(session, settings=settings)
    counter = get_token_counter(settings)
    git = GitRepo(repo.local_path)

    file_cache: dict[str, int] = {}

    def file_tokens(path: str) -> int:
        if path not in file_cache:
            file_cache[path] = counter.count(git.read_text(path) or "")
        return file_cache[path]

    all_files = list(
        session.scalars(
            select(SourceFile.path).where(SourceFile.repository_id == repo.id)
        )
    )
    whole_repo_tokens = sum(file_tokens(f) for f in all_files)

    topfiles_tok: list[int] = []
    rag_ctx: list[int] = []
    rag_prompt: list[int] = []
    for case in cases:
        outcome = engine.search(session, repo.id, case.question, top_n=None)
        files: list[str] = []
        for c in outcome.candidates:
            if c.file_path not in files:
                files.append(c.file_path)
            if len(files) >= topfiles:
                break
        topfiles_tok.append(sum(file_tokens(f) for f in files))
        pkg = builder.build(case.question, outcome.candidates, repo)
        rag_ctx.append(pkg.accounting.context_tokens)
        rag_prompt.append(pkg.accounting.final_prompt_tokens)

    avg_ctx = statistics.mean(rag_ctx) if rag_ctx else 0.0
    reduction = (
        round(100.0 * (1.0 - avg_ctx / whole_repo_tokens), 1) if whole_repo_tokens else 0.0
    )
    return BaselineComparison(
        n_cases=len(cases), baseline_kind="whole_repository",
        avg_baseline_tokens=float(whole_repo_tokens),
        avg_topfiles_tokens=(statistics.mean(topfiles_tok) if topfiles_tok else 0.0),
        avg_rag_context_tokens=avg_ctx,
        avg_rag_prompt_tokens=(statistics.mean(rag_prompt) if rag_prompt else 0.0),
        token_reduction_percent=reduction,
    )


@dataclass
class LatencyBenchmark:
    n: int
    p50_ms: float
    p95_ms: float
    mean_ms: float


def benchmark_latency(
    session: Session, repo: Repository, cases: list[EvalCase],
    settings: Settings | None = None, repeats: int = 3,
) -> LatencyBenchmark:
    settings = settings or get_settings()
    engine = build_engine(settings, embedding_provider=_provider(settings), with_graph=True)
    samples: list[float] = []
    for _ in range(repeats):
        for case in cases:
            t = time.perf_counter()
            engine.search(session, repo.id, case.question, top_n=10)
            samples.append((time.perf_counter() - t) * 1000)
    samples.sort()

    def pct(p: float) -> float:
        if not samples:
            return 0.0
        idx = min(len(samples) - 1, int(p * len(samples)))
        return samples[idx]

    return LatencyBenchmark(
        n=len(samples), p50_ms=pct(0.50), p95_ms=pct(0.95),
        mean_ms=(statistics.mean(samples) if samples else 0.0),
    )


def persist_eval_run(
    session: Session, name: str, dataset: str, metrics: RetrievalMetrics,
    settings: Settings | None = None,
) -> EvaluationRun:
    settings = settings or get_settings()
    run = EvaluationRun(
        name=name, dataset=dataset,
        recall_at_1=metrics.recall_at.get(1, 0.0),
        recall_at_3=metrics.recall_at.get(3, 0.0),
        recall_at_5=metrics.recall_at.get(5, 0.0),
        recall_at_10=metrics.recall_at.get(10, 0.0),
        mrr=metrics.mrr,
        avg_retrieved_tokens=metrics.avg_candidate_tokens,
        avg_context_tokens=metrics.avg_context_tokens,
        avg_retrieval_latency_ms=metrics.avg_retrieval_latency_ms,
        config={
            "rrf_k": settings.rrf_k,
            "weights": {
                "symbol": settings.weight_symbol, "lexical": settings.weight_lexical,
                "semantic": settings.weight_semantic, "graph": settings.weight_graph,
            },
            "max_context_tokens": settings.max_context_tokens,
        },
    )
    session.add(run)
    session.flush()
    return run


def _provider(settings: Settings):
    from coderag.embeddings.registry import get_embedding_provider

    return get_embedding_provider(settings)
