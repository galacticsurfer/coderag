"""One-command setup: database -> migrations -> index -> Claude Code wiring.

Collapses the multi-step install into `coderag setup`, and removes the manual
steps that most often go wrong:
  * exporting CODERAG_DATABASE_URL (the URL is recorded and auto-discovered),
  * finding the absolute path to `coderag-mcp` (Claude Code spawns it without
    your shell's PATH, so a bare name fails),
  * remembering the CLAUDE.md nudge (without it the tools are never called).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

NUDGE_MARKER = "coderag_context"
NUDGE = """
## Code search

This repo is indexed in CodeRAG. To find or understand code, call the
`coderag_context` or `coderag_search` MCP tools **before** globbing/grepping or
reading whole files — they return only the relevant symbols and cost far fewer
input tokens.

- `coderag_search` — locate symbols ("where is X defined?").
- `coderag_context` — understand behaviour ("why does X happen?"). Returns the
  target symbol plus its dependencies, callers, and tests, under a token budget.
- `coderag_symbol` — fetch one symbol's full source by qualified name.

Read whole files only when the retrieved context is genuinely insufficient. If
results reference code that has moved, re-index with `coderag_index`
(`incremental: true`).
"""


@dataclass
class SetupResult:
    steps: list[tuple[str, str]] = field(default_factory=list)  # (status, message)
    database_url: str | None = None
    repository: str | None = None
    symbols: int = 0

    def ok(self, msg: str) -> None:
        self.steps.append(("ok", msg))

    def skip(self, msg: str) -> None:
        self.steps.append(("skip", msg))

    def warn(self, msg: str) -> None:
        self.steps.append(("warn", msg))


def mcp_binary() -> str | None:
    """Absolute path to the coderag-mcp entry point next to this interpreter."""
    candidate = Path(sys.executable).parent / "coderag-mcp"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("coderag-mcp")
    return found


def claude_md_needs_nudge(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        return NUDGE_MARKER not in path.read_text()
    except OSError:  # pragma: no cover - unreadable file
        return True


def append_nudge(path: Path) -> None:
    existing = path.read_text() if path.exists() else ""
    sep = "" if existing.endswith("\n") or not existing else "\n"
    path.write_text(existing + sep + NUDGE)


def register_mcp(name: str, database_url: str, repository: str) -> tuple[bool, str]:
    """Register the MCP server with Claude Code. Returns (done, message)."""
    if shutil.which("claude") is None:
        return False, "`claude` CLI not found — see docs/claude-code.md to wire it manually"
    binary = mcp_binary()
    if binary is None:
        return False, "coderag-mcp not found — install the 'mcp' extra"
    result = subprocess.run(
        [
            "claude", "mcp", "add", name,
            "-e", f"CODERAG_DATABASE_URL={database_url}",
            "-e", f"CODERAG_DEFAULT_REPOSITORY={repository}",
            "-s", "user",
            "--", binary,
        ],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return True, f"registered MCP server '{name}' -> {binary}"
    detail = (result.stderr or result.stdout).strip().splitlines()
    tail = detail[-1] if detail else "unknown error"
    return False, f"`claude mcp add` failed: {tail[:160]}"
