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
class DoctorReport:
    breakdown: CostBreakdown
    diagnoses: list[Diagnosis] = field(default_factory=list)
    skill_effect: SkillEffect | None = None


def attribute(rows: list[UsageRow], price_in: float, price_out: float) -> CostBreakdown:
    b = CostBreakdown()
    for r in rows:
        b.requests += 1
        b.fresh_input_tokens += r.input_tokens or 0
        b.cache_read_tokens += r.cached_input_tokens or 0
        b.cache_write_tokens += r.cache_creation_input_tokens or 0
        b.output_tokens += r.output_tokens or 0
        b.tool_result_chars += getattr(r, "tool_result_chars", 0) or 0
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

    # R2 — poor cache hit rate on substantial repeated traffic
    seen_input = b.fresh_input_tokens + b.cache_read_tokens
    if (b.requests >= 5 and seen_input / max(b.requests, 1) > 10_000
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
    return DoctorReport(
        breakdown=b,
        diagnoses=diagnose(
            b, price_in, price_out,
            retrieval_queries=retrieval_queries,
            ordered_total_input=ordered_total_input,
            measured_output_reduction=(
                effect.measured_reduction if effect else None
            ),
        ),
        skill_effect=effect,
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
