"""Flake8 adapter (runs flake8 as a subprocess)."""

from __future__ import annotations

import subprocess
import sys

from coderag.analyzers.base import Finding, StaticAnalyzer

_FMT = "%(path)s\t%(row)d\t%(code)s\t%(text)s"


class Flake8Analyzer(StaticAnalyzer):
    name = "flake8"

    def available(self) -> bool:
        try:
            subprocess.run(
                [sys.executable, "-m", "flake8", "--version"],
                capture_output=True, check=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def analyze(self, root: str, paths: list[str] | None = None) -> list[Finding]:
        targets = paths or ["."]
        result = subprocess.run(
            [sys.executable, "-m", "flake8", f"--format={_FMT}", *targets],
            cwd=root, capture_output=True, text=True,
        )
        findings: list[Finding] = []
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 4:
                continue
            path, row, code, text = parts
            path = path.lstrip("./").replace("\\", "/")
            findings.append(
                Finding(file_path=path, line=int(row), code=code, message=text,
                        tool=self.name)
            )
        return findings
