# ADR-006: Repository authorization & isolation

- **Status:** Accepted
- **Context:** Multiple private repositories share one database. A query scoped to repo A must
  never surface code from repo B, and users must only reach repositories they're allowed to.
- **Decision:**
  - Every retrieval-relevant row carries `repository_id`; **every** retriever query filters on
    it — there is no code path that queries symbols/embeddings without a repository scope.
  - An `AuthorizationProvider` interface maps a principal → allowed `repository_id`s. The API
    resolves the target repository and checks authorization *before* retrieval. The MVP ships
    a permissive `AllowAllAuthorizationProvider` for development; production supplies its own.
  - A dedicated test seeds two repositories with identically-named symbols and asserts a
    scoped query returns only its own repository's rows.
- **Consequences:**
  - Isolation is structural (a mandatory WHERE clause), not incidental.
  - Authorization is pluggable without touching retrieval.
- **Revisit when:** finer-grained (path/branch-level) authorization is needed — extend the
  provider, keep the mandatory `repository_id` scope.
