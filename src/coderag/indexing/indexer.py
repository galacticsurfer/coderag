"""Repository indexing.

Full index: enumerate files (git-aware, .gitignore-respecting) -> filter
ignored/secret/binary -> redact residual secrets -> parse into symbols ->
persist. Embedding generation is layered on in Phase 3; incremental indexing in
Phase 8.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from coderag.core.config import Settings, get_settings
from coderag.core.logging import get_logger
from coderag.core.tokens import TokenCounter, get_token_counter
from coderag.db.models import IndexingRun, Repository, SourceFile, Symbol
from coderag.git.repo import GitRepo
from coderag.indexing.ignore import should_index
from coderag.parsing.registry import get_parser_for_path, module_qualified_name
from coderag.security.secrets import redact

log = get_logger("indexer")


@dataclass
class IndexStats:
    files_indexed: int = 0
    symbols_indexed: int = 0
    files_skipped: int = 0
    secrets_redacted: int = 0
    embeddings_created: int = 0
    duration_seconds: float = 0.0
    commit_sha: str | None = None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_or_create_repository(
    session: Session, name: str, local_path: str, url: str | None = None
) -> Repository:
    repo = session.scalar(select(Repository).where(Repository.name == name))
    git = GitRepo(local_path)
    branch = git.default_branch() if git.is_git() else "main"
    if repo is None:
        repo = Repository(name=name, local_path=local_path, url=url, default_branch=branch)
        session.add(repo)
        session.flush()
    else:
        repo.local_path = local_path
        if url:
            repo.url = url
        repo.default_branch = branch
    return repo


class Indexer:
    def __init__(
        self,
        session: Session,
        settings: Settings | None = None,
        token_counter: TokenCounter | None = None,
        embed: bool = True,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.tokens = token_counter or get_token_counter(self.settings)
        self.extra_ignore = self.settings.extra_ignore
        self.embed = embed

    # -- full index -------------------------------------------------------
    def full_index(self, repo: Repository) -> tuple[IndexingRun, IndexStats]:
        started = time.perf_counter()
        git = GitRepo(repo.local_path)
        commit = git.current_commit()
        run = IndexingRun(repository_id=repo.id, mode="full", status="running",
                          to_commit=commit)
        self.session.add(run)
        self.session.flush()

        # Full reindex: clear prior data for this repository (cascades to
        # symbols/embeddings/relationships via FK ON DELETE CASCADE).
        self.session.execute(
            delete(SourceFile).where(SourceFile.repository_id == repo.id)
        )
        self.session.execute(delete(Symbol).where(Symbol.repository_id == repo.id))

        stats = IndexStats(commit_sha=commit)
        try:
            for rel_path in git.list_files():
                if not should_index(rel_path, self.extra_ignore):
                    stats.files_skipped += 1
                    continue
                self._index_file(repo, rel_path, commit, git, stats)

            if self.embed:
                stats.embeddings_created = self._embed(repo)

            stats.duration_seconds = time.perf_counter() - started
            run.status = "success"
            run.files_indexed = stats.files_indexed
            run.symbols_indexed = stats.symbols_indexed
            run.embeddings_created = stats.embeddings_created
            run.duration_seconds = stats.duration_seconds
            from sqlalchemy import func

            run.finished_at = func.now()
            repo.indexed_commit_sha = commit
            repo.updated_at = func.now()
            log.info(
                "full_index.done", repository=repo.name,
                files=stats.files_indexed, symbols=stats.symbols_indexed,
                skipped=stats.files_skipped, redactions=stats.secrets_redacted,
                seconds=round(stats.duration_seconds, 3),
            )
        except Exception as exc:  # pragma: no cover - defensive
            run.status = "failed"
            run.error = str(exc)[:500]
            log.error("full_index.failed", repository=repo.name, error=str(exc)[:200])
            raise
        return run, stats

    def _embed(self, repo: Repository) -> int:
        from coderag.embeddings.pipeline import EmbeddingPipeline
        from coderag.embeddings.registry import get_embedding_provider

        provider = get_embedding_provider(self.settings)
        return EmbeddingPipeline(self.session, provider).embed_repository(repo)

    # -- per-file ---------------------------------------------------------
    def _index_file(
        self, repo: Repository, rel_path: str, commit: str | None,
        git: GitRepo, stats: IndexStats,
    ) -> None:
        raw = git.read_text(rel_path)
        if raw is None:
            stats.files_skipped += 1
            return
        red = redact(raw)
        if red.count:
            stats.secrets_redacted += red.count
            log.warning("redacted_secret", path=rel_path, count=red.count,
                        labels=red.labels)
        content = red.text

        parser = get_parser_for_path(rel_path)
        if parser is None:
            stats.files_skipped += 1
            return

        sf = SourceFile(
            repository_id=repo.id, path=rel_path, language=parser.language,
            content_hash=_hash(content), size_bytes=len(content.encode("utf-8")),
            indexed_commit_sha=commit,
        )
        self.session.add(sf)
        self.session.flush()

        result = parser.parse(module_qualified_name(rel_path), content)
        local_to_db: dict[int, int] = {}
        for ps in result.symbols:
            parent_db = (
                local_to_db.get(ps.parent_local_id)
                if ps.parent_local_id is not None
                else None
            )
            sym = Symbol(
                repository_id=repo.id,
                source_file_id=sf.id,
                file_path=rel_path,
                language=parser.language,
                symbol_name=ps.symbol_name,
                qualified_name=ps.qualified_name,
                symbol_type=ps.symbol_type,
                signature=ps.signature,
                start_line=ps.start_line,
                end_line=ps.end_line,
                parent_symbol_id=parent_db,
                source_code=ps.source_code,
                docstring=ps.docstring,
                source_hash=ps.source_hash,
                commit_sha=commit,
                token_count=self.tokens.count(ps.source_code),
                search_document=" ".join(ps.search_terms),
            )
            self.session.add(sym)
            self.session.flush()
            local_to_db[ps.local_id] = sym.id
            stats.symbols_indexed += 1
        stats.files_indexed += 1


def index_repository(
    session: Session, name: str, local_path: str, url: str | None = None,
    settings: Settings | None = None,
) -> tuple[Repository, IndexingRun, IndexStats]:
    """Convenience: register (or update) a repository and run a full index."""
    repo = get_or_create_repository(session, name, local_path, url)
    indexer = Indexer(session, settings=settings)
    run, stats = indexer.full_index(repo)
    return repo, run, stats
