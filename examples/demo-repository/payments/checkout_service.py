"""Checkout orchestration — a caller of PaymentService.retry_payment."""

from __future__ import annotations

from payments.models import Payment, RetryResult
from payments.payment_service import PaymentService


class CheckoutService:
    def __init__(self, payments: PaymentService) -> None:
        self.payments = payments

    def handle_failure(self, payment: Payment, outcomes: list[bool]) -> RetryResult:
        """On a failed charge, ask PaymentService to retry the payment."""
        result = self.payments.retry_payment(payment, outcomes)
        if not result.succeeded:
            # escalate; invoice reconciliation intentionally missing (see demo bug)
            result.events.append("escalated_to_support")
        return result
