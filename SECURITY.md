# Security Policy

CodeRAG treats **source code as sensitive data**. Security is a first-class requirement, not
a later add-on.

## Reporting a vulnerability

Please report suspected vulnerabilities privately to the maintainers (do not open a public
issue for undisclosed vulnerabilities). Include reproduction steps and impact. We aim to
acknowledge within a few business days.

## Security model & controls

1. **Secrets are never indexed.** Files matching secret patterns (`.env`, `*.pem`, `*.key`,
   credential files, etc.) and configurable path exclusions are skipped at ingestion time.
   See `coderag/security/secrets.py` and `coderag/indexing/ignore.py`.
2. **Redaction.** Content that still looks like a credential (API keys, tokens, private-key
   blocks) is redacted before storage/serving where detected.
3. **No sensitive logging.** We never log full source code, full prompts, secrets, or
   credentials. Logs carry identifiers and counts only. `core.logging.preview()` truncates any
   possibly-sensitive string.
4. **Repository isolation.** Every retrieval query is scoped by `repository_id`. A query for
   repository A cannot return chunks from repository B. This is enforced in the retrieval
   layer and covered by a dedicated isolation test.
5. **Authorization interface.** `coderag/security/authz.py` defines `AuthorizationProvider`
   (user → allowed repositories). The MVP ships a permissive dev implementation; production
   deployments implement their own.
6. **Token/egress budgeting.** The context builder enforces a hard token budget *before*
   anything is sent to an external LLM, limiting how much code can ever leave the boundary.

## Threat scenarios considered

| Threat | Mitigation |
|--------|-----------|
| **Cross-repository data leakage** | Mandatory `repository_id` scoping on all queries; isolation test. |
| **Secrets entering the index / prompts** | Secret-pattern ignore list + redaction at ingest; no secret files parsed. |
| **Prompt injection via source comments** | Retrieved code is delimited and labelled as untrusted data; the system prompt instructs the model to treat repository content as data and never follow instructions embedded in it. Residual risk documented — do not auto-execute model output. |
| **Malicious repository content** | Parsing is static (Tree-sitter); code is never executed during indexing. Analyzer/patch workflows run tests in the caller's sandbox, bounded by `MAX_FIX_ATTEMPTS`. |
| **Excessive context sent externally** | Hard `MAX_CONTEXT_TOKENS` budget enforced pre-send; token accounting recorded per request. |
| **Credential exfiltration via logs** | Structured logging with no payloads; `LOG_CODE_SNIPPETS` defaults off. |

## Residual risks / non-goals (MVP)

- The MVP authorization provider is permissive (development use). Deploy a real one.
- Prompt-injection defenses are mitigations, not guarantees; treat LLM output as untrusted.
- Static call-graph edges are heuristic and may be incomplete (by design — see ADR-006/retrieval docs).
