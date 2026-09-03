"""Parser selection by file extension, plus module-name derivation."""

from __future__ import annotations

from functools import lru_cache

from coderag.parsing.base import LanguageParser


@lru_cache
def _python_parser() -> LanguageParser:
    from coderag.parsing.python import PythonParser

    return PythonParser()


@lru_cache
def _treesitter_parser(language: str) -> LanguageParser | None:
    from coderag.parsing.treesitter import SPECS, TreeSitterParser

    for spec in SPECS:
        if spec.language == language:
            return TreeSitterParser(spec)
    return None


def get_parser_for_path(path: str) -> LanguageParser | None:
    from coderag.indexing.ignore import language_for_path

    lang = language_for_path(path)
    if lang is None:
        return None
    if lang == "python":
        return _python_parser()
    return _treesitter_parser(lang)


def module_qualified_name(rel_path: str) -> str:
    """Derive a dotted module name from a repo-relative path.

    ``services/payment_service.py`` -> ``services.payment_service``
    ``services/__init__.py``        -> ``services``
    ``src/components/App.tsx``      -> ``src.components.App``
    """
    p = rel_path.replace("\\", "/")
    dot = p.rfind(".")
    if dot != -1:
        p = p[:dot]
    parts = [seg for seg in p.split("/") if seg]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else "root"
