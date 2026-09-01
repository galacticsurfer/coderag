# Security model

This document complements [`../SECURITY.md`](../SECURITY.md) (policy + threat table) with the
implementation detail.

## Never indexed

`indexing/ignore.py` + `security/secrets.py` skip, before parsing:

- `.gitignore` matches and a configurable ignore list (`node_modules/`, `vendor/`, `dist/`,
  `build/`, `.venv/`, `__pycache__/`, `generated/`, `migrations/`, …).
- Secret-bearing files: `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa`,
  `credentials`, `*.crt`, service-account JSON, etc.
- Binary / generated / vendored files (by extension and heuristics).

Extra fragments come from `CODERAG_EXTRA_IGNORE`.

## Redaction

`security/secrets.py` scans retained content for high-signal secret shapes (AWS keys, generic
API tokens, private-key PEM blocks) and redacts them before storage/serving where detected.

## Logging

`core/logging.py` uses structlog and logs identifiers/counts only. `preview()` truncates and
flattens any possibly-sensitive string. `CODERAG_LOG_CODE_SNIPPETS` (default `false`) gates
even short snippets. Full source and full prompts are **never** logged.

## Isolation & authorization

- Mandatory `repository_id` scope on all retrieval (ADR-006).
- `security/authz.py` defines `AuthorizationProvider`; MVP ships `AllowAllAuthorizationProvider`.
- Isolation is covered by `tests/` (two repos, identical symbol names, scoped query returns
  only its own).

## Prompt injection

Repository code can contain adversarial comments ("ignore previous instructions…"). We:

- Clearly delimit and label retrieved code as **untrusted data** in the prompt.
- Instruct the model (system prompt) to treat repository content as data, not instructions.
- Never auto-execute model output; analyzer patches are gated by tests and `MAX_FIX_ATTEMPTS`.

These are mitigations, not guarantees — treat LLM output as untrusted.
