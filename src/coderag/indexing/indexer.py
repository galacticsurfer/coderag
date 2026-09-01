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

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from coderag.core.config import Settings, get_settings
from coderag.core.logging import get_logger
from coderag.core.tokens import TokenCounter, get_token_counter
from coderag.db.models import (
    IndexingRun,
    Repository,
    SourceFile,
    Symbol,
    SymbolRelationship,
)
from coderag.git.repo import GitRepo
from coderag.indexing.ignore import should_index
from coderag.parsing.base import CALLS, CONTAINS, TESTS
from coderag.parsing.registry import get_parser_for_path, module_qualified_name
from coderag.security.secrets import redact

log = get_logger("indexer")


@dataclass
class IndexStats:
    files_indexed: int = 0
    files_deleted: int = 0
    symbols_indexed: int = 0
    symbols_deleted: int = 0
    files_skipped: int = 0
    secrets_redacted: int = 0
    embeddings_created: int = 0
    relationships_created: int = 0
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

        # accumulate cross-file graph edges, resolved after all symbols exist
        self._pending_contains: list[tuple[int, int]] = []
        self._pending_rels: list[tuple[int, str, str, float, dict | None]] = []

        stats = IndexStats(commit_sha=commit)
        try:
            for rel_path in git.list_files():
                if not should_index(rel_path, self.extra_ignore):
                    stats.files_skipped += 1
                    continue
                self._index_file(repo, rel_path, commit, git, stats)

            stats.relationships_created = self._persist_relationships(repo)

            if self.embed:
                stats.embeddings_created = self._embed(repo)

            stats.duration_seconds = time.perf_counter() - started
            run.status = "success"
            run.files_indexed = stats.files_indexed
            run.symbols_indexed = stats.symbols_indexed
            run.embeddings_created = stats.embeddings_created
            run.duration_seconds = stats.duration_seconds

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

    # -- incremental index ------------------------------------------------
    def incremental_index(self, repo: Repository) -> tuple[IndexingRun, IndexStats]:
        """Update only what changed since ``repo.indexed_commit_sha``.

        Symbols/embeddings for unchanged files are preserved (unchanged code is
        never re-embedded). The relationship graph is rebuilt from a fast parse
        pass so cross-file edges stay correct. Falls back to a full index when
        there is no prior commit or the path is not a git repo.
        """
        git = GitRepo(repo.local_path)
        base = repo.indexed_commit_sha
        head = git.current_commit()
        if not git.is_git() or not base:
            return self.full_index(repo)

        started = time.perf_counter()
        run = IndexingRun(repository_id=repo.id, mode="incremental", status="running",
                          from_commit=base, to_commit=head)
        self.session.add(run)
        self.session.flush()

        self._pending_contains = []
        self._pending_rels = []
        stats = IndexStats(commit_sha=head)
        try:
            diff = git.diff(base, head or "HEAD")
            changed = [p for p in diff.changed if should_index(p, self.extra_ignore)]
            gone = set(changed) | set(diff.deleted)
            for path in gone:
                self._delete_file(repo, path, stats)

            for path in changed:
                self._index_file(repo, path, head, git, stats)

            # rebuild the whole-repo graph: collect edges from unchanged files too
            qual_to_id = self._qualified_index(repo)
            changed_set = set(changed)
            for path in self._indexed_paths(repo):
                if path not in changed_set:
                    self._collect_edges(repo, path, git, qual_to_id)
            self.session.execute(
                delete(SymbolRelationship).where(
                    SymbolRelationship.repository_id == repo.id
                )
            )
            stats.relationships_created = self._persist_relationships(repo)

            if self.embed:
                stats.embeddings_created = self._embed(repo)

            stats.duration_seconds = time.perf_counter() - started

            run.status = "success"
            run.files_indexed = stats.files_indexed
            run.files_deleted = stats.files_deleted
            run.symbols_indexed = stats.symbols_indexed
            run.symbols_deleted = stats.symbols_deleted
            run.embeddings_created = stats.embeddings_created
            run.duration_seconds = stats.duration_seconds
            run.finished_at = func.now()
            repo.indexed_commit_sha = head
            repo.updated_at = func.now()
            log.info(
                "incremental_index.done", repository=repo.name,
                changed=len(changed), deleted=len(diff.deleted),
                symbols_added=stats.symbols_indexed, symbols_deleted=stats.symbols_deleted,
                embedded=stats.embeddings_created, seconds=round(stats.duration_seconds, 3),
            )
        except Exception as exc:  # pragma: no cover - defensive
            run.status = "failed"
            run.error = str(exc)[:500]
            log.error("incremental_index.failed", repository=repo.name, error=str(exc)[:200])
            raise
        return run, stats

    def _delete_file(self, repo: Repository, path: str, stats: IndexStats) -> None:
        n = self.session.scalar(
            select(func.count()).select_from(Symbol).where(
                Symbol.repository_id == repo.id, Symbol.file_path == path
            )
        ) or 0
        existed = self.session.scalar(
            select(SourceFile.id).where(
                SourceFile.repository_id == repo.id, SourceFile.path == path
            )
        ) is not None
        self.session.execute(
            delete(Symbol).where(Symbol.repository_id == repo.id, Symbol.file_path == path)
        )
        self.session.execute(
            delete(SourceFile).where(
                SourceFile.repository_id == repo.id, SourceFile.path == path
            )
        )
        if existed:
            stats.files_deleted += 1
        stats.symbols_deleted += int(n)

    def _qualified_index(self, repo: Repository) -> dict[str, int]:
        return {
            qual: sid
            for sid, qual in self.session.execute(
                select(Symbol.id, Symbol.qualified_name).where(
                    Symbol.repository_id == repo.id
                )
            ).all()
        }

    def _indexed_paths(self, repo: Repository) -> list[str]:
        return list(
            self.session.scalars(
                select(SourceFile.path).where(SourceFile.repository_id == repo.id)
            )
        )

    def _collect_edges(
        self, repo: Repository, path: str, git: GitRepo, qual_to_id: dict[str, int]
    ) -> None:
        """Parse an unchanged file for its edges only, mapping to existing ids."""
        raw = git.read_text(path)
        parser = get_parser_for_path(path)
        if raw is None or parser is None:
            return
        result = parser.parse(module_qualified_name(path), redact(raw).text)
        local_to_db = {
            ps.local_id: qual_to_id.get(ps.qualified_name) for ps in result.symbols
        }
        for ps in result.symbols:
            db_id = local_to_db.get(ps.local_id)
            if db_id is None:
                continue
            if ps.parent_local_id is not None:
                parent_db = local_to_db.get(ps.parent_local_id)
                if parent_db is not None:
                    self._pending_contains.append((parent_db, db_id))
        for pr in result.relationships:
            src_db = local_to_db.get(pr.source_local_id)
            if src_db is not None:
                self._pending_rels.append(
                    (src_db, pr.relationship_type, pr.target_name, pr.confidence,
                     pr.metadata)
                )

    # -- relationships ----------------------------------------------------
    def _persist_relationships(self, repo: Repository) -> int:
        rows = self.session.execute(
            select(
                Symbol.id, Symbol.symbol_name, Symbol.qualified_name,
                Symbol.parent_symbol_id, Symbol.file_path,
            ).where(Symbol.repository_id == repo.id)
        ).all()
        by_qual: dict[str, int] = {}
        by_name: dict[str, list[int]] = {}
        info: dict[int, tuple[int | None, str, str]] = {}
        for sid, name, qual, parent_id, path in rows:
            by_qual[qual.lower()] = sid
            by_name.setdefault(name.lower(), []).append(sid)
            info[sid] = (parent_id, name, path)

        def resolve(target_name: str, via: str | None, source_id: int) -> int | None:
            t = target_name.lower()
            if t in by_qual:
                return by_qual[t]
            cands = by_name.get(t.split(".")[-1], [])
            if not cands:
                return None
            if via == "self":
                parent = info[source_id][0]
                same = [c for c in cands if info[c][0] == parent]
                if len(same) == 1:
                    return same[0]
            return cands[0] if len(cands) == 1 else None

        def is_test(sid: int) -> bool:
            _parent, name, path = info[sid]
            p = path.replace("\\", "/")
            return (
                name.lower().startswith("test")
                or "/tests/" in p
                or p.startswith("tests/")
                or p.rsplit("/", 1)[-1].startswith("test_")
            )

        edges: dict[tuple[int, int | None, str], tuple[float, dict | None]] = {}

        def add(source: int, target: int | None, rtype: str, conf: float, meta):
            key = (source, target, rtype)
            if key not in edges or conf > edges[key][0]:
                edges[key] = (conf, meta)

        for parent_db, child_db in self._pending_contains:
            add(parent_db, child_db, CONTAINS, 1.0, None)

        for src, rtype, target_name, conf, meta in self._pending_rels:
            via = (meta or {}).get("via") if meta else None
            tid = resolve(target_name, via, src)
            if tid is None or tid == src:
                continue  # keep the graph correct: only in-repo, non-self-loop edges
            add(src, tid, rtype, conf, meta)
            if rtype == CALLS and is_test(src):
                add(src, tid, TESTS, min(conf, 0.8), None)

        for (source, target, rtype), (conf, meta) in edges.items():
            self.session.add(
                SymbolRelationship(
                    repository_id=repo.id, source_symbol_id=source,
                    target_symbol_id=target, relationship_type=rtype,
                    confidence=conf, meta=meta,
                )
            )
        self.session.flush()
        return len(edges)

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
            token_count=self.tokens.count(content),
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
            if parent_db is not None:
                self._pending_contains.append((parent_db, sym.id))
            stats.symbols_indexed += 1

        for pr in result.relationships:
            src_db = local_to_db.get(pr.source_local_id)
            if src_db is not None:
                self._pending_rels.append(
                    (src_db, pr.relationship_type, pr.target_name, pr.confidence, pr.metadata)
                )
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
