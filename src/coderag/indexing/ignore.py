"""Which files to index.

Responsibilities:
  * skip vendored/generated/build directories (a configurable default list),
  * skip binary files,
  * skip secret files (delegated to ``security.secrets``),
  * map file extensions to a language.

`.gitignore` is respected automatically for git repositories because we
enumerate via ``git ls-files`` (tracked files only) — see ``git/repo.py``.
"""

from __future__ import annotations

from coderag.security.secrets import is_secret_file

DEFAULT_IGNORE_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        "vendor",
        "dist",
        "build",
        ".venv",
        "venv",
        "__pycache__",
        "generated",
        "migrations",
        ".git",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".tox",
        ".idea",
        ".vscode",
        "target",
        "site-packages",
    }
)

BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".pdf",
        ".zip", ".gz", ".tar", ".tgz", ".bz2", ".7z", ".rar",
        ".so", ".dylib", ".dll", ".a", ".o", ".class", ".jar", ".pyc", ".pyd",
        ".exe", ".bin", ".wasm", ".woff", ".woff2", ".ttf", ".eot",
        ".mp3", ".mp4", ".mov", ".avi", ".wav", ".ogg", ".webp",
        ".parquet", ".pkl", ".pt", ".onnx", ".npy", ".npz", ".db", ".sqlite",
    }
)

# extension -> language name
LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
    ".rs": "rust",
}


def language_for_path(path: str) -> str | None:
    dot = path.rfind(".")
    if dot == -1:
        return None
    return LANGUAGE_BY_EXTENSION.get(path[dot:].lower())


def is_binary_extension(path: str) -> bool:
    dot = path.rfind(".")
    return dot != -1 and path[dot:].lower() in BINARY_EXTENSIONS


def in_ignored_dir(path: str, extra: frozenset[str] | None = None) -> bool:
    parts = path.replace("\\", "/").split("/")
    dirs = set(parts[:-1])
    if dirs & DEFAULT_IGNORE_DIRS:
        return True
    if extra:
        for frag in extra:
            if frag and frag.strip("/") in dirs:
                return True
        for frag in extra:
            if frag and frag in path:
                return True
    return False


def should_index(path: str, extra_ignore: list[str] | None = None) -> bool:
    """Return True if the path is an indexable source file.

    Skips ignored dirs, secret files, binaries, and files of unknown language.
    """
    extra = frozenset(extra_ignore or [])
    if in_ignored_dir(path, extra):
        return False
    if is_secret_file(path):
        return False
    if is_binary_extension(path):
        return False
    return language_for_path(path) is not None


def looks_binary(content: bytes) -> bool:
    """Heuristic: a NUL byte in the first chunk means binary."""
    return b"\x00" in content[:8192]
