"""Retry policy: how many attempts and how long to back off."""

from __future__ import annotations

PAYMENT_RETRY_LIMIT = 3
BASE_TIMEOUT_SECONDS = 2
MAX_TIMEOUT_SECONDS = 30

ERR_PAYMENT_102 = "ERR_PAYMENT_102: gateway timeout"


class RetryPolicy:
    """Computes retry timeouts with exponential backoff."""

    def __init__(self, limit: int = PAYMENT_RETRY_LIMIT) -> None:
        self.limit = limit

    def should_retry(self, attempts: int) -> bool:
        return attempts < self.limit

    def calculate_timeout(self, attempt: int) -> int:
        """Return the backoff timeout (seconds) for a given attempt number."""
        timeout = BASE_TIMEOUT_SECONDS * (2 ** max(0, attempt - 1))
        return min(timeout, MAX_TIMEOUT_SECONDS)
