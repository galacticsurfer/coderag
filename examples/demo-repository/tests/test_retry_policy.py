"""Tests for RetryPolicy.calculate_timeout."""

from __future__ import annotations

from payments.retry_policy import MAX_TIMEOUT_SECONDS, RetryPolicy


def test_calculate_timeout_exponential_backoff():
    policy = RetryPolicy()
    assert policy.calculate_timeout(1) == 2
    assert policy.calculate_timeout(2) == 4
    assert policy.calculate_timeout(3) == 8


def test_calculate_timeout_capped():
    policy = RetryPolicy()
    assert policy.calculate_timeout(100) == MAX_TIMEOUT_SECONDS


def test_should_retry_respects_limit():
    policy = RetryPolicy(limit=2)
    assert policy.should_retry(1) is True
    assert policy.should_retry(2) is False
