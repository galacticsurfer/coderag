"""Cost doctor: attribute observed LLM spend, then rank the levers by $ impact.

Everything here is arithmetic over *observed* traffic (the ``llm_requests``
rows written by the proxy and by ``ask``). No LLM is called; every diagnosis
cites the numbers it was derived from, and every dollar figure is an estimate
at the configured per-million prices with its assumption stated. The point is
to tell the user which lever matters for *their* workload — including when the
honest answer is "this one wouldn't help you".

Pricing model (relative to the configured input price P_in / output P_out):
  fresh input        1.00 x P_in
  cache reads        0.10 x P_in
  cache writes       1.25 x P_in
  output             1.00 x P_out   (typically 5x P_in)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

CACHE_READ_RATE = 0.10
CACHE_WRITE_RATE = 1.25
CHARS_PER_TOKEN = 4.0  # heuristic, for the tool_result-share estimate only

# Published list prices ($ per Mtok input, output). Longest-prefix matched so
# date-suffixed IDs resolve; unknown models fall back to the configured prices.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def lookup_model_prices(model: str) -> tuple[float, float] | None:
    best = ""
    for prefix in MODEL_PRICES:
        if model.startswith(prefix) and len(prefix) > len(best):
            best = prefix
    return MODEL_PRICES[best] if best else None


def model_prices(model: str, fallback: tuple[float, float]) -> tuple[float, float]:
    return lookup_model_prices(model) or fallback


class UsageRow(Protocol):
    """The slice of an llm_requests row the doctor needs."""

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int | None
    cache_creation_input_tokens: int | None
    tool_result_chars: int


@dataclass
class CostBreakdown:
    requests: int = 0
    fresh_input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    tool_result_chars: int = 0
    tool_schema_chars: int = 0
    failed_requests: int = 0

    fresh_input_usd: float = 0.0
    cache_read_usd: float = 0.0
    cache_write_usd: float = 0.0
    output_usd: float = 0.0

    @property
    def total_usd(self) -> float:
        return (self.fresh_input_usd + self.cache_read_usd
                + self.cache_write_usd + self.output_usd)

    @property
    def cache_hit_rate(self) -> float:
        seen = self.fresh_input_tokens + self.cache_read_tokens
        return self.cache_read_tokens / seen if seen else 0.0


@dataclass
class Diagnosis:
    code: str
    title: str
    evidence: str            # the observed numbers this rests on
    action: str              # what to actually do
    est_saving_usd: float | None  # None = real but unquantifiable
    assumption: str          # what the estimate assumes — always stated


@dataclass
class SkillEffect:
    """Measured output-per-request with the /token-lean skill active vs not.

    Observational, not a randomized experiment — tasks differ between groups —
    but it replaces the doctor's *assumed* output reduction with a number from
    the user's own traffic once both groups are big enough.
    """

    active_requests: int
    inactive_requests: int
    avg_output_active: float
    avg_output_inactive: float

    @property
    def measured_reduction(self) -> float | None:
        """Fractional output reduction when the skill is active (None if n/a)."""
        if (self.active_requests < MIN_SKILL_GROUP
                or self.inactive_requests < MIN_SKILL_GROUP
                or self.avg_output_inactive <= 0):
            return None
        return 1.0 - self.avg_output_active / self.avg_output_inactive


MIN_SKILL_GROUP = 5  # requests needed on each side before the comparison counts


def skill_effect(rows: list[UsageRow]) -> SkillEffect:
    active = [r.output_tokens or 0 for r in rows
              if getattr(r, "token_lean_active", False)]
    inactive = [r.output_tokens or 0 for r in rows
                if not getattr(r, "token_lean_active", False)]
    return SkillEffect(
        active_requests=len(active),
        inactive_requests=len(inactive),
        avg_output_active=sum(active) / len(active) if active else 0.0,
        avg_output_inactive=sum(inactive) / len(inactive) if inactive else 0.0,
    )


@dataclass
class ModelSpend:
    model: str
    requests: int = 0
    fresh_input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    est_usd: float = 0.0


def _row_usd(r: UsageRow, pin: float, pout: float) -> float:
    return ((r.input_tokens or 0) / 1e6 * pin
            + (r.cached_input_tokens or 0) / 1e6 * pin * CACHE_READ_RATE
            + (r.cache_creation_input_tokens or 0) / 1e6 * pin * CACHE_WRITE_RATE
            + (r.output_tokens or 0) / 1e6 * pout)


def model_mix(
    rows: list[UsageRow], fallback: tuple[float, float]
) -> list[ModelSpend]:
    """Spend per served model at published per-model prices, biggest first."""
    by_model: dict[str, ModelSpend] = {}
    for r in rows:
        name = getattr(r, "model", None) or "unknown"
        m = by_model.setdefault(name, ModelSpend(model=name))
        m.requests += 1
        m.fresh_input_tokens += r.input_tokens or 0
        m.cache_read_tokens += r.cached_input_tokens or 0
        m.cache_write_tokens += r.cache_creation_input_tokens or 0
        m.output_tokens += r.output_tokens or 0
        m.est_usd += _row_usd(r, *model_prices(name, fallback))
    return sorted(by_model.values(), key=lambda m: (-m.est_usd, m.model))


@dataclass
class RoutingSavings:
    """Measured effect of `coderag proxy --route`: tokens x price difference."""

    routed_requests: int = 0
    saved_usd: float = 0.0
    unpriced_requests: int = 0  # routed, but a model wasn't in the price table


def routing_savings(rows: list[UsageRow]) -> RoutingSavings:
    out = RoutingSavings()
    for r in rows:
        requested = getattr(r, "requested_model", None)
        served = getattr(r, "model", None) or ""
        if not requested or requested == served:
            continue
        out.routed_requests += 1
        req_p = lookup_model_prices(requested)
        srv_p = lookup_model_prices(served)
        if req_p is None or srv_p is None:
            out.unpriced_requests += 1
            continue
        out.saved_usd += _row_usd(r, *req_p) - _row_usd(r, *srv_p)
    return out


@dataclass
class DoctorReport:
    breakdown: CostBreakdown
    diagnoses: list[Diagnosis] = field(default_factory=list)
    skill_effect: SkillEffect | None = None
    models: list[ModelSpend] = field(default_factory=list)
    routing: RoutingSavings | None = None


def attribute(rows: list[UsageRow], price_in: float, price_out: float) -> CostBreakdown:
    b = CostBreakdown()
    for r in rows:
        b.requests += 1
        b.fresh_input_tokens += r.input_tokens or 0
        b.cache_read_tokens += r.cached_input_tokens or 0
        b.cache_write_tokens += r.cache_creation_input_tokens or 0
        b.output_tokens += r.output_tokens or 0
        b.tool_result_chars += getattr(r, "tool_result_chars", 0) or 0
        b.tool_schema_chars += getattr(r, "tool_schema_chars", 0) or 0
        if getattr(r, "success", True) is False:
            b.failed_requests += 1
    b.fresh_input_usd = b.fresh_input_tokens / 1e6 * price_in
    b.cache_read_usd = b.cache_read_tokens / 1e6 * price_in * CACHE_READ_RATE
    b.cache_write_usd = b.cache_write_tokens / 1e6 * price_in * CACHE_WRITE_RATE
    b.output_usd = b.output_tokens / 1e6 * price_out
    return b


def diagnose(
    b: CostBreakdown,
    price_in: float,
    price_out: float,
    *,
    retrieval_queries: int = 0,
    ordered_total_input: list[int] | None = None,
    measured_output_reduction: float | None = None,
    models: list[ModelSpend] | None = None,
) -> list[Diagnosis]:
    """Rule-based diagnoses, ranked by estimated $ impact (unquantified last)."""
    out: list[Diagnosis] = []
    total = b.total_usd

    # R1 — output-dominant spend
    if total > 0 and b.output_usd / total >= 0.5:
        share = 100 * b.output_usd / total
        if measured_output_reduction is not None and measured_output_reduction > 0:
            reduction = measured_output_reduction
            assumption = (f"reduction measured on your traffic "
                          f"({100 * reduction:.0f}% with /token-lean active; "
                          "observational, tasks differ between groups)")
        else:
            reduction = 0.25
            assumption = "assumes a 25% reduction in output length; scale linearly"
        out.append(Diagnosis(
            code="output_dominant",
            title="Output tokens dominate your spend",
            evidence=(f"{b.output_tokens:,} output tokens = ${b.output_usd:.2f} "
                      f"({share:.0f}% of ${total:.2f} observed)"),
            action=("Lower `effort` one level (e.g. xhigh->high) and add terse-output "
                    "instructions (the /token-lean skill's output rules). "
                    "Mechanical fallback: `coderag proxy --cap-output` / "
                    "`--cap-thinking` (quality trade-off)."),
            est_saving_usd=round(b.output_usd * reduction, 4),
            assumption=assumption,
        ))

    # R7 — no cache activity at all: the client never asks for caching.
    # Takes precedence over R2 (which is about *ineffective* caching).
    no_cache_activity = (b.cache_read_tokens == 0 and b.cache_write_tokens == 0)
    if (b.requests >= 5 and no_cache_activity
            and b.fresh_input_tokens / max(b.requests, 1) > 5_000):
        cacheable = int(b.fresh_input_tokens * 0.9)
        saving = cacheable / 1e6 * price_in * (1 - CACHE_READ_RATE)
        out.append(Diagnosis(
            code="no_caching",
            title="Prompt caching isn't being used at all",
            evidence=(f"0 cache reads and 0 cache writes across {b.requests} "
                      f"requests averaging "
                      f"{b.fresh_input_tokens // max(b.requests, 1):,} "
                      "fresh input tokens"),
            action=("The client sends no cache_control breakpoints. Add them, or "
                    "run `coderag proxy --auto-cache` to inject the standard "
                    "placement (tools / system / last message) automatically."),
            est_saving_usd=round(saving, 4),
            assumption=("assumes 90% of fresh input is a repeated prefix that "
                        "would bill at the 0.1x cache-read rate"),
        ))

    # R6 — cache churn: paying the 1.25x write premium for cache that is
    # rarely read back (the prefix keeps changing between writes).
    if (b.requests >= 5 and b.cache_write_tokens > 0
            and b.cache_read_tokens < 0.5 * b.cache_write_tokens):
        premium = b.cache_write_tokens / 1e6 * price_in * (CACHE_WRITE_RATE - 1.0)
        out.append(Diagnosis(
            code="cache_churn",
            title="Cache writes rarely get read back",
            evidence=(f"{b.cache_write_tokens:,} tokens written to cache at 1.25x "
                      f"but only {b.cache_read_tokens:,} read back "
                      f"({b.requests} requests)"),
            action=("The cached prefix changes between requests, so each write is "
                    "paid for and then abandoned. Stabilize the prefix (fixed "
                    "system prompt, stable tool list, append-only history)."),
            est_saving_usd=round(premium, 4),
            assumption=("estimates only the recoverable 0.25x write premium; a "
                        "stable prefix would additionally convert fresh input "
                        "to 0.1x reads"),
        ))

    # R2 — poor cache hit rate on substantial repeated traffic
    seen_input = b.fresh_input_tokens + b.cache_read_tokens
    if (b.requests >= 5 and not no_cache_activity
            and seen_input / max(b.requests, 1) > 10_000
            and b.cache_hit_rate < 0.4):
        recoverable = int(b.fresh_input_tokens * 0.7)
        saving = recoverable / 1e6 * price_in * (1 - CACHE_READ_RATE)
        out.append(Diagnosis(
            code="cache_misses",
            title="Prompt cache is barely being hit",
            evidence=(f"cache hit rate {100 * b.cache_hit_rate:.0f}% across "
                      f"{b.requests} requests averaging "
                      f"{seen_input // max(b.requests, 1):,} input tokens"),
            action=("Something invalidates your prompt prefix (timestamp in system "
                    "prompt, changing tool list, unsorted JSON). Multi-turn traffic "
                    "should mostly bill at the 0.1x cache rate."),
            est_saving_usd=round(saving, 4),
            assumption="assumes 70% of fresh input is a repeated prefix that could cache",
        ))

    # R3 — context growing unboundedly across the window
    seq = ordered_total_input or []
    if len(seq) >= 8:
        q = max(len(seq) // 4, 1)
        early, late = seq[:q], seq[-q:]
        early_avg = sum(early) / len(early)
        late_avg = sum(late) / len(late)
        if early_avg > 0 and late_avg / early_avg >= 2.0:
            out.append(Diagnosis(
                code="history_growth",
                title="Per-request context is growing steeply",
                evidence=(f"avg total input grew {early_avg:,.0f} -> {late_avg:,.0f} "
                          f"tokens ({late_avg / early_avg:.1f}x) across the window"),
                action=("Compact or restart long sessions (`/compact`, fresh sessions "
                        "per task). History is resent every turn."),
                est_saving_usd=None,
                assumption="growth measured on observed traffic; saving depends on "
                           "where you compact",
            ))

    # R4 — proxy sees traffic but retrieval tools go unused
    if b.requests >= 10 and retrieval_queries < 2:
        out.append(Diagnosis(
            code="retrieval_unused",
            title="CodeRAG retrieval isn't being used",
            evidence=(f"{b.requests} LLM requests observed, but only "
                      f"{retrieval_queries} CodeRAG queries in the same period"),
            action=("Check /mcp shows 'coderag' connected and the CLAUDE.md nudge is "
                    "present — without it the agent reads whole files instead."),
            est_saving_usd=None,
            assumption="retrieval saved 54-75% of the file-reading slice where measured",
        ))

    # R5 — heavy tool output: the --compress lever
    tool_tokens = b.tool_result_chars / CHARS_PER_TOKEN
    if b.fresh_input_tokens > 0 and tool_tokens / b.fresh_input_tokens >= 0.2:
        saving = tool_tokens * 0.3 / 1e6 * price_in
        out.append(Diagnosis(
            code="tool_output_heavy",
            title="Tool output is a large share of fresh input",
            evidence=(f"~{int(tool_tokens):,} tokens of tool_result content vs "
                      f"{b.fresh_input_tokens:,} fresh input tokens "
                      f"({100 * tool_tokens / b.fresh_input_tokens:.0f}%)"),
            action="Run the proxy with --compress (dedupes logs, elides oversized "
                   "output, recoverable via coderag_expand).",
            est_saving_usd=round(saving, 4),
            assumption="assumes compression removes 30% of tool-result tokens; "
                       "verify on /coderag-proxy/health",
        ))

    # R8 — everything runs on the most expensive tier
    mix = models or []
    if mix and b.requests >= 20:
        top = mix[0]
        top_share = top.est_usd / max(sum(m.est_usd for m in mix), 1e-9)
        top_prices = lookup_model_prices(top.model)
        if top_share >= 0.9 and top_prices is not None and top_prices[0] >= 5.0:
            out.append(Diagnosis(
                code="expensive_model_dominant",
                title=f"~all spend runs on {top.model}",
                evidence=(f"{top.requests} of {b.requests} requests on "
                          f"{top.model} = ${top.est_usd:.2f} "
                          f"({100 * top_share:.0f}% of est. spend)"),
                action=("If part of this traffic is simple (classification, "
                        "formatting, subagent chores), route it explicitly: "
                        "`coderag proxy --route "
                        f"{top.model}=claude-sonnet-5` — then check quality "
                        "and the doctor's measured routing savings."),
                est_saving_usd=None,
                assumption=("saving depends on how much traffic tolerates a "
                            "cheaper model; routing effect is measured once "
                            "you enable it"),
            ))

    # R9 — tool definitions are a big slice of every request
    schema_tokens = b.tool_schema_chars / CHARS_PER_TOKEN
    if b.requests >= 10 and schema_tokens / max(b.requests, 1) >= 2_000:
        out.append(Diagnosis(
            code="tool_schema_heavy",
            title="Tool definitions are large on every request",
            evidence=(f"~{int(schema_tokens / max(b.requests, 1)):,} tokens of "
                      f"tool schemas per request across {b.requests} requests"),
            action=("Make sure the tool list is stable and sits in the cached "
                    "prefix (it then bills at 0.1x), or trim tool "
                    "descriptions / drop unused tools."),
            est_saving_usd=None,
            assumption=("schemas that already cache cost little; the win is "
                        "in stabilizing or shrinking them"),
        ))

    # R10 — repeated failures suggest a config/rate-limit problem
    if b.failed_requests >= 3 and b.failed_requests / max(b.requests, 1) >= 0.2:
        out.append(Diagnosis(
            code="retry_storm",
            title="A large share of requests are failing",
            evidence=(f"{b.failed_requests} of {b.requests} observed requests "
                      "returned a non-2xx status"),
            action=("Check rate limits, the upstream/base URL, and auth. "
                    "Failed requests waste latency and retries even where "
                    "they don't bill."),
            est_saving_usd=None,
            assumption="no dollar figure: failed requests are typically not billed",
        ))

    out.sort(key=lambda d: (d.est_saving_usd is None, -(d.est_saving_usd or 0)))
    return out


def examine(
    rows: list[UsageRow],
    price_in: float,
    price_out: float,
    *,
    retrieval_queries: int = 0,
    ordered_total_input: list[int] | None = None,
) -> DoctorReport:
    b = attribute(rows, price_in, price_out)
    effect = skill_effect(rows) if rows else None
    mix = model_mix(rows, (price_in, price_out))
    return DoctorReport(
        breakdown=b,
        diagnoses=diagnose(
            b, price_in, price_out,
            retrieval_queries=retrieval_queries,
            ordered_total_input=ordered_total_input,
            measured_output_reduction=(
                effect.measured_reduction if effect else None
            ),
            models=mix,
        ),
        skill_effect=effect,
        models=mix,
        routing=routing_savings(rows) if rows else None,
    )


def examine_from_db(session, price_in: float, price_out: float) -> DoctorReport:
    """Load observed traffic from the database and run the full examination."""
    from sqlalchemy import func, select

    from coderag.db.models import LLMRequest, QueryRecord

    rows = list(session.scalars(select(LLMRequest).order_by(LLMRequest.created_at)))
    retrieval_queries = session.scalar(
        select(func.count()).select_from(QueryRecord)
    ) or 0
    ordered = [
        (r.input_tokens or 0) + (r.cached_input_tokens or 0) for r in rows
    ]
    return examine(
        rows, price_in, price_out,
        retrieval_queries=int(retrieval_queries),
        ordered_total_input=ordered,
    )
