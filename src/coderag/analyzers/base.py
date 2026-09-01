"""Static-analysis adapter interface (Phase 10).

A ``StaticAnalyzer`` turns tool output into normalized ``Finding``s. Adapters run
the tool as a subprocess (never importing/linking it), so licensing stays clean
(see docs/licenses.md re: pylint GPL). Future adapters: SonarQube, mypy, Ruff,
Bandit.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Finding:
    file_path: str          # repository-relative
    line: int
    code: str               # rule id, e.g. "F401", "W0611"
    message: str
    tool: str

    def describe(self) -> str:
        return f"[{self.tool}:{self.code}] {self.file_path}:{self.line} — {self.message}"


class StaticAnalyzer(ABC):
    name: str

    @abstractmethod
    def available(self) -> bool:
        """Whether the underlying tool is installed/runnable."""

    @abstractmethod
    def analyze(self, root: str, paths: list[str] | None = None) -> list[Finding]:
        """Run the analyzer over ``root`` (optionally limited to ``paths``)."""
