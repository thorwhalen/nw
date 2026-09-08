"""Re-quoting a persisted plan at today's rates (nw#74).

nw writes cost figures into places that are **read back later**: a
render-decision payload, a ``RenderResultBodyV1`` body, a job's cost gate.
Each of those numbers is a :attr:`falaw.CallPlan.estimated_cost_usd` frozen at
plan time. falaw's rate tables move — 0.0.46 re-quoted every premium LLM call
*tenfold upward* — so a figure nw stored before a table moves under-quotes the
run it is later used to describe or gate. Under-quoting is the one direction a
spend decision must never err in.

falaw 0.0.49 (falaw#60) supplies the two halves of the fix:
:class:`falaw.CostBasis` (which pricer, what it priced, the quantity hints,
the rate table and its content digest) and :func:`falaw.reprice_plan` (pure
data: no network, no billing API, no cache peek). This module is nw's single
adoption point for them, and it enforces one rule:

**A stored figure is never reported as current.** Reading a persisted cost
means calling :func:`current_quote` first and reporting
:attr:`PlanQuote.total_usd` with :attr:`PlanQuote.status` beside it. A plan
whose calls carry no basis re-prices to ``None`` — *unknown*, never its stale
number, and never zero.

Two things this module deliberately does **not** do:

- **It does not touch cache identity.** ``cost_basis`` is descriptive: falaw
  omits it from the serialized call when unset and keeps it out of
  ``plan_hash`` and the per-call cache key. So a resumed render still dedups
  on the same digest, and :func:`nw.jobs.enqueue`'s idempotency key is unmoved
  by anything here — ``tests/test_pricing.py`` pins that.
- **It does not re-quote money already spent.**
  :meth:`nw.Project.total_spend_usd` sums what was *billed*, and a receipt is
  not a quote: re-pricing it at today's rates would rewrite history. Its
  estimate-based fallback is an as-of figure and says so.

>>> from falaw import CallPlan, Plan
>>> stale = CallPlan(tool="text_to_video", application="fal-ai/x",
...                  arguments={}, output_kind="video",
...                  estimated_cost_usd=0.25)
>>> quote = current_quote(Plan(calls=(stale,)))
>>> quote.status, quote.total_usd, quote.as_of_total_usd
('unknown', None, 0.25)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping, Optional, Sequence

from falaw import (
    CallPlan,
    Plan,
    cost_basis_from_dict,
    cost_basis_to_dict,
    reprice_plan,
)
from falaw.reprice import DFLT_PRICERS, Pricer, RepricedPlan


QuoteStatus = Literal["unchanged", "changed", "unknown"]
"""What a re-quote was able to say about a whole plan.

- ``"unchanged"`` — every billable call re-quoted, and the total did not move.
- ``"changed"`` — re-quoted, and at least one call's price moved. The total in
  :attr:`PlanQuote.total_usd` is today's; the stored one was yesterday's.
- ``"unknown"`` — at least one billable call could not be re-quoted (no basis,
  no pricer, a model that left the catalogue, a table that moved out from
  under it). :attr:`PlanQuote.total_usd` is ``None``: unknown, never free, and
  never the frozen figure.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanQuote:
    """Today's price for a persisted plan, with the stale figure alongside.

    The stale figure is kept — as :attr:`as_of_total_usd`, explicitly named
    "as of then" — because an audit surface wants to show the movement. What
    it must never do is *present* it as current; that is what
    :attr:`total_usd` and :attr:`status` are for.
    """

    total_usd: Optional[float]
    """Today's billable total, or ``None`` when any billable call is
    unpriceable today. ``None`` means unknown, never free (nw invariant #2)."""

    status: QuoteStatus
    """Which of the three cases this plan fell into — see :data:`QuoteStatus`."""

    as_of_total_usd: Optional[float]
    """What the persisted plan said, ``None`` if it already said unknown.

    A fact about the moment it was written. Render it labelled as such, or
    not at all."""

    repriced: RepricedPlan
    """falaw's per-call diff — ``status``, ``basis_changed``, ``reason`` per
    call. Read it for the audit view; :attr:`total_usd` is the headline."""

    reason: str = ""
    """Why no plan could be re-quoted at all, when that is the situation.

    Set only by :func:`unquotable`; empty for a real re-quote, where the
    per-call reasons live on :attr:`repriced` instead."""

    @property
    def has_unknown_costs(self) -> bool:
        """True when the total cannot be known — the gate's refusal condition.

        The same judgement as :attr:`falaw.Plan.has_unknown_costs`, made at
        re-quote time rather than at plan time.
        """
        return self.total_usd is None

    @property
    def delta_usd(self) -> Optional[float]:
        """``total_usd - as_of_total_usd``, or ``None`` when either is unknown.

        ``None`` rather than ``0.0``: a plan that lost its price did not move
        by zero.
        """
        if self.total_usd is None or self.as_of_total_usd is None:
            return None
        return self.total_usd - self.as_of_total_usd

    def to_dict(self) -> dict[str, Any]:
        """JSON-able headline for a surface (an API response, a job record).

        Deliberately not the whole per-call diff: a spend surface needs the
        number, whether it is knowable, and whether it moved. Reach into
        :attr:`repriced` for the rest.
        """
        return {
            "total_usd": self.total_usd,
            "status": self.status,
            "as_of_total_usd": self.as_of_total_usd,
            "delta_usd": self.delta_usd,
            "has_unknown_costs": self.has_unknown_costs,
            "unpriced_call_count": len(self.repriced.unpriced),
            "reason": self.reason,
        }


def unquotable(reason: str) -> PlanQuote:
    """A quote for something that could not be re-quoted at all.

    For the caller who was handed a *plan-shaped* thing that turned out not to
    be a plan — an unparseable payload, an object of the wrong type. The honest
    answer is ``None`` (unknown), not the frozen figure that came with it, and
    not ``0.0``.

    :attr:`PlanQuote.repriced` is an empty :class:`falaw.RepricedPlan`: there
    were no calls to diff. Read :attr:`PlanQuote.reason` for what went wrong.

    >>> q = unquotable("params['plan'] is not a falaw Plan")
    >>> q.status, q.total_usd, q.has_unknown_costs
    ('unknown', None, True)
    """
    return PlanQuote(
        total_usd=None,
        status="unknown",
        as_of_total_usd=None,
        repriced=RepricedPlan(plan=Plan(calls=()), calls=()),
        reason=reason,
    )


def current_quote(
    plan: Plan,
    *,
    pricers: Mapping[str, Pricer] = DFLT_PRICERS,
) -> PlanQuote:
    """Re-quote ``plan`` at today's rates and report the result honestly.

    Pure data — :func:`falaw.reprice_plan` reads the committed rate tables and
    does arithmetic. No network, no billing API, no cache peek, so this is
    safe to call anywhere a ``plan()`` is (nw invariant #1).

    Args:
        plan: The plan to re-quote — typically one just rebuilt from a stored
            payload with :func:`plan_from_cost_records`.
        pricers: Pricing rules by :attr:`falaw.CostBasis.pricer`. The seam for
            a caller with reconciled numbers of their own; see
            :class:`falaw.reprice.Pricer`.

    >>> from falaw import Plan
    >>> current_quote(Plan(calls=())).status
    'unchanged'
    """
    repriced = reprice_plan(plan, pricers=pricers)
    total = _billable_total(repriced.plan)
    if total is None:
        status: QuoteStatus = "unknown"
    elif repriced.changed:
        status = "changed"
    else:
        status = "unchanged"
    return PlanQuote(
        total_usd=total,
        status=status,
        as_of_total_usd=_billable_total(plan),
        repriced=repriced,
    )


def _billable_total(plan: Plan) -> Optional[float]:
    """``plan.total_cost_usd``, or ``None`` when a billable call has no price.

    :attr:`falaw.Plan.total_cost_usd` sums ``billable_cost_usd``, which reads
    an unknown cost as ``0.0`` so sums stay well-defined. That is the right
    default for falaw and the wrong one to *report*: nw's invariant is that
    ``None`` means unknown, never free. A cache hit is genuinely free, so
    ``has_unknown_costs`` — which ignores hits — is the correct gate.
    """
    return None if plan.has_unknown_costs else plan.total_cost_usd


# ---------------------------------------------------------------------------
# Persisting calls in a re-quotable shape
# ---------------------------------------------------------------------------


def cost_records(plan: Plan) -> list[dict[str, Any]]:
    """The JSON-able per-call cost rows nw persists in a decision payload.

    A serialized call cannot be re-quoted from ``application`` and
    ``arguments`` alone — the quantity hints that priced it are estimator-only
    and never reach the wire arguments. Carrying ``cost_basis`` alongside the
    frozen figure is what makes the row re-quotable later by
    :func:`quote_from_cost_records`.

    ``cost_basis`` is omitted when unset, exactly as falaw omits it, so a row
    written by a caller that records no basis is byte-identical to what nw
    wrote before nw#74.

    >>> from falaw import CallPlan, Plan
    >>> call = CallPlan(tool="t", application="a", arguments={},
    ...                 output_kind="video", estimated_cost_usd=1.0)
    >>> sorted(cost_records(Plan(calls=(call,)))[0])
    ['application', 'cache_status', 'estimated_cost_usd', 'tool']
    """
    rows: list[dict[str, Any]] = []
    for call in plan.calls:
        row: dict[str, Any] = {
            "tool": call.tool,
            "application": call.application,
            "estimated_cost_usd": call.estimated_cost_usd,
            "cache_status": call.cache_status,
        }
        if call.cost_basis is not None:
            row["cost_basis"] = cost_basis_to_dict(call.cost_basis)
        rows.append(row)
    return rows


def plan_from_cost_records(records: Iterable[Mapping[str, Any]]) -> Plan:
    """Rebuild a re-quotable :class:`falaw.Plan` from :func:`cost_records` rows.

    The result is a **pricing** plan, not an executable one: ``arguments`` is
    empty and ``output_kind`` is a placeholder, because a stored cost row does
    not carry the wire payload and re-pricing does not read it. Never hand one
    of these to :func:`falaw.execute_plan` — build a fresh plan for that.

    Rows missing ``cost_basis`` come back basis-free, which is exactly what
    makes them re-price as ``"no_basis"`` (unknown) rather than as their
    frozen number.
    """
    calls = tuple(
        CallPlan(
            tool=str(row.get("tool") or ""),
            application=str(row.get("application") or ""),
            arguments={},
            output_kind=_REQUOTE_ONLY_OUTPUT_KIND,
            estimated_cost_usd=_as_cost(row.get("estimated_cost_usd")),
            cache_status=row.get("cache_status") or "unknown",
            cost_basis=(
                cost_basis_from_dict(basis)
                if isinstance(basis := row.get("cost_basis"), Mapping)
                else None
            ),
        )
        for row in records
    )
    return Plan(calls=calls)


_REQUOTE_ONLY_OUTPUT_KIND = "video"
"""Placeholder ``output_kind`` for a plan rebuilt for pricing only.

``CallPlan`` requires one and re-pricing never reads it. Naming the constant
(rather than inlining a bare string) is what keeps a reader from mistaking a
pricing plan for a faithful round-trip of the original.
"""


def _as_cost(value: Any) -> Optional[float]:
    """A stored cost as a float, or ``None`` for anything that is not a number.

    A payload written by an older nw, or hand-edited, can hold ``null`` or a
    string here. Coercing junk to ``0.0`` would report an unpriceable call as
    free — the exact failure this module exists to prevent.
    """
    return float(value) if isinstance(value, (int, float)) else None


def quote_from_cost_records(
    records: Optional[Sequence[Mapping[str, Any]]],
    *,
    pricers: Mapping[str, Pricer] = DFLT_PRICERS,
) -> PlanQuote:
    """Today's price for the calls stored in a decision payload.

    The read-back half of :func:`cost_records`. ``None`` or a non-sequence
    (a payload that recorded no calls at all) yields an empty plan's quote —
    ``total_usd == 0.0``, ``status == "unchanged"`` — because "no calls" is a
    known zero, not an unknown.
    """
    rows = records if isinstance(records, Sequence) else ()
    return current_quote(
        plan_from_cost_records(r for r in rows if isinstance(r, Mapping)),
        pricers=pricers,
    )


def quote_render_decision(
    payload: Mapping[str, Any],
    *,
    pricers: Mapping[str, Pricer] = DFLT_PRICERS,
) -> PlanQuote:
    """Today's price for a ``render_shot`` decision payload.

    The counterpart to what :func:`nw.workflow._record_render_decision` wrote.
    Read this — never ``payload["total_estimated_cost_usd"]`` — whenever a
    stored render cost is about to be shown or gated on as a *current* figure.

    A payload written before nw#74 carries no per-call basis, so it re-quotes
    as ``"unknown"`` with ``total_usd`` ``None``. That is the point: nobody
    can say what it costs today, and saying so is better than repeating a
    number that has since moved.

    >>> quote_render_decision({"calls": [], "total_estimated_cost_usd": 3.0}).status
    'unchanged'
    >>> stale = quote_render_decision(
    ...     {"calls": [{"tool": "t", "application": "a",
    ...                 "estimated_cost_usd": 3.0}]})
    >>> stale.status, stale.total_usd, stale.as_of_total_usd
    ('unknown', None, 3.0)
    """
    return quote_from_cost_records(payload.get("calls"), pricers=pricers)
