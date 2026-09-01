"""Pylint adapter (runs pylint as a subprocess; its JSON output is consumed).

Pylint is GPL-2.0 but is invoked only as an external tool — CodeRAG neither
imports nor links it (docs/licenses.md).
"""

from __future__ import annotations

import json
import subprocess
import sys

from coderag.analyzers.base import Finding, StaticAnalyzer


class PylintAnalyzer(StaticAnalyzer):
    name = "pylint"

    def available(self) -> bool:
        try:
            subprocess.run(
                [sys.executable, "-m", "pylint", "--version"],
                capture_output=True, check=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def analyze(self, root: str, paths: list[str] | None = None) -> list[Finding]:
        targets = paths or ["."]
        result = subprocess.run(
            [sys.executable, "-m", "pylint", "--output-format=json", "--score=n", *targets],
            cwd=root, capture_output=True, text=True,
        )
        try:
            items = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return []
        findings: list[Finding] = []
        for it in items:
            findings.append(
                Finding(
                    file_path=str(it.get("path", "")).replace("\\", "/"),
                    line=int(it.get("line", 1)),
                    code=it.get("message-id", it.get("symbol", "")),
                    message=it.get("message", ""),
                    tool=self.name,
                )
            )
        return findings
