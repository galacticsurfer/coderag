"""Phase 1: Python symbol extraction."""

from __future__ import annotations

from coderag.parsing.base import CLASS, FUNCTION, METHOD, MODULE
from coderag.parsing.python import PythonParser
from coderag.parsing.registry import module_qualified_name

SOURCE = '''"""Module docstring."""
import os
from a.b import C

PAYMENT_RETRY_LIMIT = 3


@decorator
def top_level(x: int) -> int:
    """Top docstring."""
    return x + 1


class PaymentService(Base):
    """Handles payments."""

    def process_payment(self, payment):
        return payment

    def retry_payment(self, payment, outcomes):
        """Retry a failed payment."""
        return self.process_payment(payment)
'''


def _by_qual(symbols):
    return {s.qualified_name: s for s in symbols}


def test_module_qualified_name():
    assert module_qualified_name("services/payment_service.py") == "services.payment_service"
    assert module_qualified_name("services/__init__.py") == "services"
    assert module_qualified_name("main.py") == "main"


def test_extracts_all_symbols():
    result = PythonParser().parse("pkg.mod", SOURCE)
    syms = _by_qual(result.symbols)
    assert "pkg.mod" in syms and syms["pkg.mod"].symbol_type == MODULE
    assert syms["pkg.mod.top_level"].symbol_type == FUNCTION
    assert syms["pkg.mod.PaymentService"].symbol_type == CLASS
    assert syms["pkg.mod.PaymentService.process_payment"].symbol_type == METHOD
    assert syms["pkg.mod.PaymentService.retry_payment"].symbol_type == METHOD


def test_parent_relationships_local_ids():
    result = PythonParser().parse("pkg.mod", SOURCE)
    syms = _by_qual(result.symbols)
    by_local = {s.local_id: s for s in result.symbols}
    method = syms["pkg.mod.PaymentService.retry_payment"]
    cls = syms["pkg.mod.PaymentService"]
    module = syms["pkg.mod"]
    assert by_local[method.parent_local_id] is cls
    assert by_local[cls.parent_local_id] is module
    assert module.parent_local_id is None


def test_signature_and_docstring():
    result = PythonParser().parse("pkg.mod", SOURCE)
    syms = _by_qual(result.symbols)
    top = syms["pkg.mod.top_level"]
    assert top.signature == "def top_level(x: int) -> int"
    assert top.docstring == "Top docstring."
    cls = syms["pkg.mod.PaymentService"]
    assert cls.signature == "class PaymentService(Base)"
    assert cls.docstring == "Handles payments."
    assert syms["pkg.mod"].docstring == "Module docstring."


def test_line_ranges_include_decorator():
    result = PythonParser().parse("pkg.mod", SOURCE)
    top = _by_qual(result.symbols)["pkg.mod.top_level"]
    # decorator line is included in the symbol span
    assert top.source_code.lstrip().startswith("@decorator")
    assert top.start_line < top.end_line


def test_module_source_excludes_definitions():
    result = PythonParser().parse("pkg.mod", SOURCE)
    module = _by_qual(result.symbols)["pkg.mod"]
    assert "PAYMENT_RETRY_LIMIT" in module.source_code
    assert "import os" in module.source_code
    # class/function bodies are their own chunks, not part of the module chunk
    assert "def retry_payment" not in module.source_code


def test_search_terms_expand_identifiers():
    result = PythonParser().parse("pkg.mod", SOURCE)
    retry = _by_qual(result.symbols)["pkg.mod.PaymentService.retry_payment"]
    terms = {t.lower() for t in retry.search_terms}
    assert "retry_payment" in terms
    assert "retry" in terms and "payment" in terms


def test_empty_file_yields_module_only():
    result = PythonParser().parse("pkg.empty", "")
    assert len(result.symbols) == 1
    assert result.symbols[0].symbol_type == MODULE
