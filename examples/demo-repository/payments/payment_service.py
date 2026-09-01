"""Payment processing and retry logic."""

from __future__ import annotations

from payments.models import Invoice, InvoiceStatus, Payment, PaymentStatus, RetryResult
from payments.payment_repository import InvoiceRepository, PaymentRepository
from payments.retry_policy import ERR_PAYMENT_102, RetryPolicy


class PaymentService:
    """Charges payments and retries failed ones."""

    def __init__(
        self,
        payments: PaymentRepository,
        invoices: InvoiceRepository,
        policy: RetryPolicy | None = None,
    ) -> None:
        self.payments = payments
        self.invoices = invoices
        self.policy = policy or RetryPolicy()

    def process_payment(self, payment: Payment, gateway_ok: bool = True) -> Payment:
        """Charge a payment once and update the invoice on success."""
        payment.attempts += 1
        if gateway_ok:
            payment.status = PaymentStatus.SUCCEEDED
            self.invoices.mark_paid(payment.invoice_id)
        else:
            payment.status = PaymentStatus.FAILED
        return self.payments.save(payment)

    def retry_payment(self, payment: Payment, outcomes: list[bool]) -> RetryResult:
        """Retry a failed payment until it succeeds or the limit is reached.

        BUG (intentional, for the demo): when all retries are exhausted the
        payment is marked FAILED but the invoice is left in its previous state,
        so an invoice can remain PENDING even though processing has stopped.
        """
        result = RetryResult(payment=payment, succeeded=False, attempts=payment.attempts)
        for attempt, ok in enumerate(outcomes, start=1):
            if not self.policy.should_retry(payment.attempts):
                result.events.append("retry_limit_reached")
                break
            self.process_payment(payment, gateway_ok=ok)
            result.attempts = payment.attempts
            if payment.status == PaymentStatus.SUCCEEDED:
                result.succeeded = True
                result.events.append("succeeded")
                return result
            result.events.append(f"attempt_{attempt}_failed")
        # NOTE: invoice status is not reconciled here -> may stay PENDING.
        result.error = ERR_PAYMENT_102
        return result
