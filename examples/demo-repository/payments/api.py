"""A tiny HTTP-ish API layer wiring authentication to payment actions."""

from __future__ import annotations

from payments.auth import AuthError, authenticate
from payments.checkout_service import CheckoutService
from payments.models import Payment
from payments.users import UserRepository


class PaymentApi:
    """Routes /api/v2/payment requests through authentication."""

    def __init__(self, users: UserRepository, checkout: CheckoutService) -> None:
        self.users = users
        self.checkout = checkout

    def post_payment_retry(self, api_key: str, payment: Payment, outcomes: list[bool]):
        """Authenticated endpoint: POST /api/v2/payment/retry."""
        try:
            authenticate(self.users, api_key)
        except AuthError:
            return {"status": 401, "error": "unauthorized"}
        result = self.checkout.handle_failure(payment, outcomes)
        return {"status": 200, "succeeded": result.succeeded, "events": result.events}
