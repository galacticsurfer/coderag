"""Pydantic request/response models for the API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RegisterRepoRequest(BaseModel):
    name: str
    path: str
    url: str | None = None


class RepositoryOut(BaseModel):
    id: int
    name: str
    local_path: str
    default_branch: str
    indexed_commit_sha: str | None = None


class IndexStatusOut(BaseModel):
    repository_id: int
    status: str | None = None
    mode: str | None = None
    files_indexed: int = 0
    symbols_indexed: int = 0
    embeddings_created: int = 0
    relationships: int = 0
    duration_seconds: float = 0.0
    to_commit: str | None = None
    error: str | None = None


class SearchRequest(BaseModel):
    query: str
    repository: str | None = None
    limit: int = 10
    semantic: bool = True
    graph: bool = True


class CandidateOut(BaseModel):
    symbol_id: int
    qualified_name: str
    symbol_type: str
    file_path: str
    start_line: int
    end_line: int
    score: float
    reasons: list[str]


class SearchResponse(BaseModel):
    repository: str
    latency_ms: float
    candidates: list[CandidateOut]


class ContextRequest(BaseModel):
    query: str
    repository: str | None = None
    max_tokens: int | None = None
    finding: str | None = None
    include_prompt: bool = True


class ContextEntryOut(BaseModel):
    category: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    tokens: int
    reasons: list[str]


class AccountingOut(BaseModel):
    query_tokens: int
    candidates_found: int
    candidates_selected: int
    candidate_tokens: int
    context_tokens: int
    dropped_tokens: int
    final_prompt_tokens: int
    token_reduction_from_candidates: float


class ContextResponse(BaseModel):
    repository: str
    entries: list[ContextEntryOut]
    accounting: AccountingOut
    prompt: str | None = None


class AskRequest(BaseModel):
    query: str
    repository: str | None = None
    max_tokens: int | None = None
    max_output_tokens: int | None = None


class UsageOut(BaseModel):
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int | None = None
    model: str
    latency_ms: float


class AskResponse(BaseModel):
    repository: str
    answer: str
    usage: UsageOut
    accounting: AccountingOut


class SymbolOut(BaseModel):
    id: int
    qualified_name: str
    symbol_name: str
    symbol_type: str
    file_path: str
    start_line: int
    end_line: int
    signature: str | None = None
    docstring: str | None = None
    token_count: int
    source_code: str


class RelationshipOut(BaseModel):
    relationship_type: str
    confidence: float
    target_symbol_id: int | None = None
    target_qualified_name: str | None = None
    target_name: str | None = None
    direction: str = Field(description="outgoing|incoming")


class MetricsOut(BaseModel):
    repositories: int
    symbols: int
    embeddings: int
    relationships: int
    queries: int
    llm_requests: int
    avg_context_tokens: float
    avg_retrieval_latency_ms: float
    total_llm_input_tokens: int
    total_llm_output_tokens: int
    # token-savings aggregates (candidate set -> budgeted context)
    total_candidate_tokens: int
    total_context_tokens: int
    total_tokens_saved: int
    avg_token_reduction_percent: float
    # "what reading whole files would have cost" baseline
    total_baseline_tokens: int
    total_saved_vs_files: int
    reduction_vs_files_percent: float


class QueryRow(BaseModel):
    id: int
    repository: str
    mode: str
    query: str
    candidates_found: int
    candidates_selected: int
    candidate_tokens: int
    context_tokens: int
    tokens_saved: int
    reduction_percent: float
    baseline_tokens: int = 0
    baseline_files: int = 0
    saved_vs_files: int = 0
    reduction_vs_files: float = 0.0
    retrieval_latency_ms: float
    llm_input_tokens: int | None = None
    llm_output_tokens: int | None = None
    created_at: str
