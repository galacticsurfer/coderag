"""Persistence for payments and invoices (in-memory demo)."""

from __future__ import annotations

from payments.models import Invoice, InvoiceStatus, Payment


class PaymentRepository:
    def __init__(self) -> None:
        self._payments: dict[int, Payment] = {}

    def save(self, payment: Payment) -> Payment:
        """Persist a payment record."""
        self._payments[payment.id] = payment
        return payment

    def get(self, payment_id: int) -> Payment | None:
        return self._payments.get(payment_id)


class InvoiceRepository:
    def __init__(self) -> None:
        self._invoices: dict[int, Invoice] = {}

    def save(self, invoice: Invoice) -> Invoice:
        self._invoices[invoice.id] = invoice
        return invoice

    def get(self, invoice_id: int) -> Invoice | None:
        return self._invoices.get(invoice_id)

    def mark_paid(self, invoice_id: int) -> None:
        inv = self._invoices.get(invoice_id)
        if inv is not None:
            inv.status = InvoiceStatus.PAID
