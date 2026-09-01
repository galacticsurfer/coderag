"""Tests covering PaymentService.process_payment and retry_payment."""

from __future__ import annotations

from payments.models import Invoice, InvoiceStatus, Payment, PaymentStatus
from payments.payment_repository import InvoiceRepository, PaymentRepository
from payments.payment_service import PaymentService
from payments.retry_policy import RetryPolicy


def _service() -> tuple[PaymentService, InvoiceRepository]:
    payments = PaymentRepository()
    invoices = InvoiceRepository()
    invoices.save(Invoice(id=1, amount_cents=1000))
    return PaymentService(payments, invoices, RetryPolicy(limit=3)), invoices


def test_process_payment_marks_invoice_paid():
    service, invoices = _service()
    payment = Payment(id=10, invoice_id=1, amount_cents=1000)
    service.process_payment(payment, gateway_ok=True)
    assert payment.status == PaymentStatus.SUCCEEDED
    assert invoices.get(1).status == InvoiceStatus.PAID


def test_retry_payment_succeeds_on_second_attempt():
    service, invoices = _service()
    payment = Payment(id=11, invoice_id=1, amount_cents=1000)
    result = service.retry_payment(payment, outcomes=[False, True])
    assert result.succeeded is True
    assert invoices.get(1).status == InvoiceStatus.PAID


def test_retry_payment_exhausted_leaves_invoice_pending():
    # Documents the intentional demo bug: invoice stays PENDING after exhaustion.
    service, invoices = _service()
    payment = Payment(id=12, invoice_id=1, amount_cents=1000)
    result = service.retry_payment(payment, outcomes=[False, False, False])
    assert result.succeeded is False
    assert invoices.get(1).status == InvoiceStatus.PENDING
