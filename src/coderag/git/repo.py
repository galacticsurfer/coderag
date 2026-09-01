"""Git-backed repository access.

For git repositories we enumerate with ``git ls-files`` so ``.gitignore`` and the
tracked/untracked distinction are honoured for free. For plain directories we
fall back to a filesystem walk. Diffs (base..head) power incremental indexing.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DiffResult:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    @property
    def changed(self) -> list[str]:
        return self.added + self.modified


class GitRepo:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()

    # -- git helpers ------------------------------------------------------
    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.path), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def is_git(self) -> bool:
        try:
            out = self._git("rev-parse", "--is-inside-work-tree").strip()
            return out == "true"
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def current_commit(self) -> str | None:
        try:
            return self._git("rev-parse", "HEAD").strip()
        except subprocess.CalledProcessError:
            return None  # e.g. repo with no commits yet

    def default_branch(self) -> str:
        try:
            return self._git("rev-parse", "--abbrev-ref", "HEAD").strip() or "main"
        except subprocess.CalledProcessError:
            return "main"

    # -- enumeration ------------------------------------------------------
    def list_files(self) -> list[str]:
        """Repository-relative paths of candidate files (POSIX separators).

        For git repos we include tracked files *and* untracked files that are
        not gitignored (``--exclude-standard``), so a working tree with
        uncommitted files still indexes correctly while honouring .gitignore.
        """
        if self.is_git():
            tracked = self._git("ls-files", "-z").split("\0")
            untracked = self._git(
                "ls-files", "-z", "--others", "--exclude-standard"
            ).split("\0")
            seen: dict[str, None] = {}
            for p in (*tracked, *untracked):
                if p:
                    seen[p] = None
            return list(seen)
        return self._walk()

    def _walk(self) -> list[str]:
        from coderag.indexing.ignore import DEFAULT_IGNORE_DIRS

        files: list[str] = []
        for p in sorted(self.path.rglob("*")):
            if p.is_dir():
                continue
            rel = p.relative_to(self.path).as_posix()
            if set(rel.split("/")[:-1]) & DEFAULT_IGNORE_DIRS:
                continue
            files.append(rel)
        return files

    # -- diff (incremental) ----------------------------------------------
    def diff(self, base: str, head: str = "HEAD") -> DiffResult:
        out = self._git("diff", "--name-status", "-z", base, head)
        return _parse_name_status(out)

    # -- reading ----------------------------------------------------------
    def read_text(self, rel_path: str) -> str | None:
        fp = self.path / rel_path
        try:
            data = fp.read_bytes()
        except (OSError, FileNotFoundError):
            return None
        from coderag.indexing.ignore import looks_binary

        if looks_binary(data):
            return None
        return data.decode("utf-8", errors="replace")


def _parse_name_status(z: str) -> DiffResult:
    """Parse ``git diff --name-status -z`` output.

    Records are NUL-separated. Renames/copies (R/C) emit the status token, then
    the old path, then the new path as separate records.
    """
    res = DiffResult()
    tokens = [t for t in z.split("\0") if t != ""]
    i = 0
    while i < len(tokens):
        status = tokens[i]
        code = status[0]
        if code in ("R", "C"):
            old, new = tokens[i + 1], tokens[i + 2]
            res.deleted.append(old)
            res.added.append(new)
            i += 3
        else:
            path = tokens[i + 1]
            if code == "A":
                res.added.append(path)
            elif code == "M":
                res.modified.append(path)
            elif code == "D":
                res.deleted.append(path)
            else:  # T (type change), etc. -> treat as modified
                res.modified.append(path)
            i += 2
    return res
