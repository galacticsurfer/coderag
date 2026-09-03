"""Doctor topline priced per model: topline == by-model sum by construction."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from coderag import doctor as D

PIN, POUT = 5.0, 25.0  # configured fallback (Opus-level)


@dataclass
class Row:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int | None = 0
    cache_creation_input_tokens: int | None = 0
    tool_result_chars: int = 0
    tool_schema_chars: int = 0
    token_lean_active: bool = False
    model: str = "claude-sonnet-5"
    requested_model: str | None = None
    success: bool = True
    compression_chars_saved: int = 0
    cap_applied: bool = False


def test_topline_equals_by_model_sum_on_mixed_traffic():
    rows = [
        Row(input_tokens=1_000_000, output_tokens=100_000),           # sonnet
        Row(input_tokens=1_000_000, output_tokens=100_000,
            model="claude-haiku-4-5"),
        Row(input_tokens=1_000_000, output_tokens=100_000,
            model="totally-unknown"),                                 # fallback
    ]
    b = D.attribute(rows, PIN, POUT)
    mix = D.model_mix(rows, (PIN, POUT))
    assert b.total_usd == pytest.approx(sum(m.est_usd for m in mix))
    # hand-check: sonnet 3+1.5, haiku 1+0.5, unknown at configured 5+2.5
    assert b.total_usd == pytest.approx((3 + 1.5) + (1 + 0.5) + (5 + 2.5))


def test_sonnet_traffic_priced_at_sonnet_not_configured_opus():
    rows = [Row(input_tokens=0, output_tokens=1_000_000,
                cached_input_tokens=1_000_000,
                cache_creation_input_tokens=1_000_000)]
    b = D.attribute(rows, PIN, POUT)
    assert b.output_usd == pytest.approx(15.0)               # not 25
    assert b.cache_read_usd == pytest.approx(3.0 * 0.10)     # not 0.50
    assert b.cache_write_usd == pytest.approx(3.0 * 1.25)    # not 6.25


def test_effective_input_price_reflects_observed_mix():
    rows = [Row(input_tokens=1_000_000),
            Row(input_tokens=1_000_000, model="claude-haiku-4-5")]
    b = D.attribute(rows, PIN, POUT)
    assert b.effective_input_price(PIN) == pytest.approx((3.0 + 1.0) / 2)
    empty = D.attribute([], PIN, POUT)
    assert empty.effective_input_price(PIN) == PIN           # fallback


def test_estimates_use_observed_prices():
    # all-sonnet zero-cache traffic: R7's estimate must be at $3/M, not $5/M
    rows = [Row(input_tokens=20_000, output_tokens=100) for _ in range(6)]
    ds = D.examine(rows, PIN, POUT, retrieval_queries=50).diagnoses
    r7 = next(d for d in ds if d.code == "no_caching")
    assert r7.est_saving_usd == pytest.approx(
        120_000 * 0.9 / 1e6 * 3.0 * 0.9, abs=1e-4)
