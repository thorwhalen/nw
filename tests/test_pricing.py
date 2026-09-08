"""Offline tests for :mod:`nw.pricing` — re-quoting a persisted plan (nw#74).

Hermetic by construction: :func:`falaw.reprice_plan` is pure data (it reads
falaw's committed rate tables and does arithmetic), and the "the table moved"
case is exercised with a :class:`falaw.reprice.Pricer` built in-process rather
than by mutating a real table. The autouse fixtures from ``tests/conftest.py``
still guard the run — nothing here goes near the network, and nothing here
spends.

The load-bearing test is :func:`test_cache_identity_is_unmoved_by_repricing`:
``cost_basis`` is descriptive, so a plan that gained one must hash to exactly
what it hashed before, and a job's idempotency key with it. If that ever
breaks, a resumed render re-runs work already paid for.
"""

import pytest

from falaw import (
    CallPlan,
    Plan,
    catalogue_cost_basis,
    plan_generate_image,
    plan_hash,
    plan_to_dict,
)
from falaw.reprice import DFLT_PRICERS, LLM_RATES_PRICER, Pricer
from falaw.cost import estimate_call_cost
from falaw.registry import MODEL_CATALOGUE_TABLE, get_model

import nw.jobs as jobs
from nw import Project
from nw.pricing import (
    cost_records,
    current_quote,
    plan_from_cost_records,
    quote_from_cost_records,
    quote_render_decision,
    unquotable,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


PRICED_MODEL = "fal-ai/flux/dev"
SECONDS = 6.0
CATALOGUE_COST = estimate_call_cost(get_model(PRICED_MODEL), seconds=SECONDS)
"""What falaw's committed table prices ``PRICED_MODEL`` at today.

Read from the table rather than written as a literal: a call stamped with this
figure re-quotes as ``"unchanged"`` no matter which release of falaw's
``models.json`` the suite runs against, so these tests assert *the mechanism*
and never pin a price.
"""


def _basis_free_call(cost=0.25):
    """A call as a pre-falaw#60 planner would have written it: a frozen figure
    and nothing that says how it was reached."""
    return CallPlan(
        tool="text_to_video",
        application=PRICED_MODEL,
        arguments={"prompt": "a tiger"},
        output_kind="video",
        estimated_cost_usd=cost,
    )


def _priced_call(cost=CATALOGUE_COST, *, seconds=SECONDS):
    """The same call, stamped the way nw's renderers now stamp one.

    ``cost`` defaults to today's catalogue figure, so the call re-quotes as
    ``"unchanged"``; pass a different one to stand in for a quote the table has
    since moved away from."""
    return CallPlan(
        tool="text_to_video",
        application=PRICED_MODEL,
        arguments={"prompt": "a tiger"},
        output_kind="video",
        estimated_cost_usd=cost,
        cost_basis=catalogue_cost_basis(PRICED_MODEL, seconds=seconds),
    )


def _doubling_pricers():
    """Pricers that quote twice falaw's number — a rate table that moved."""
    base = DFLT_PRICERS["model_catalogue"]
    return {
        **DFLT_PRICERS,
        "model_catalogue": Pricer(
            quote=lambda basis: (q := base.quote(basis)) and q * 2,
            table=MODEL_CATALOGUE_TABLE,
            version=lambda: "moved",
        ),
    }


@pytest.fixture
def project(tmp_path):
    return Project.init(tmp_path / "proj", title="Pricing Test")


# ---------------------------------------------------------------------------
# current_quote — the three statuses
# ---------------------------------------------------------------------------


def test_unchanged_plan_reports_todays_number_and_it_matches():
    plan = Plan(calls=(plan_generate_image("a tiger", consult_cache=False),))
    quote = current_quote(plan)
    assert quote.status == "unchanged"
    assert quote.total_usd == plan.total_cost_usd
    assert quote.as_of_total_usd == plan.total_cost_usd
    assert quote.delta_usd == 0.0
    assert quote.has_unknown_costs is False


def test_moved_table_reports_the_new_number_not_the_stored_one():
    plan = Plan(calls=(_priced_call(0.25),))
    quote = current_quote(plan, pricers=_doubling_pricers())
    assert quote.status == "changed"
    assert quote.as_of_total_usd == 0.25
    assert quote.total_usd not in (None, 0.25)
    assert quote.delta_usd == quote.total_usd - 0.25
    assert quote.repriced.changed  # falaw saw it too


def test_basis_free_call_is_unknown_never_its_stale_number():
    """The whole point: a figure with no basis is *unknown*, not $0.25 and not
    free. Under-quoting is the one direction a spend gate must never err in."""
    quote = current_quote(Plan(calls=(_basis_free_call(0.25),)))
    assert quote.status == "unknown"
    assert quote.total_usd is None
    assert quote.as_of_total_usd == 0.25
    assert quote.delta_usd is None
    assert quote.has_unknown_costs is True
    assert [c.status for c in quote.repriced] == ["no_basis"]


def test_empty_plan_is_a_known_zero_not_an_unknown():
    quote = current_quote(Plan(calls=()))
    assert (quote.status, quote.total_usd) == ("unchanged", 0.0)


def test_cache_hit_without_basis_does_not_poison_the_total():
    """A cache hit bills nothing, so it is a known zero even unpriceable."""
    hit = CallPlan(
        tool="t",
        application=PRICED_MODEL,
        arguments={},
        output_kind="video",
        estimated_cost_usd=0.25,
        cache_status="hit",
    )
    quote = current_quote(Plan(calls=(hit,)))
    assert quote.total_usd == 0.0
    assert quote.status == "unchanged"


def test_unquotable_says_unknown_with_a_reason():
    quote = unquotable("not a plan")
    assert (quote.status, quote.total_usd, quote.reason) == (
        "unknown",
        None,
        "not a plan",
    )
    assert quote.to_dict()["has_unknown_costs"] is True


# ---------------------------------------------------------------------------
# persisting calls in a re-quotable shape
# ---------------------------------------------------------------------------


def test_cost_records_round_trip_keeps_the_plan_requotable():
    plan = Plan(calls=(_priced_call(),))
    rows = cost_records(plan)
    assert rows[0]["cost_basis"]["priced"] == PRICED_MODEL
    assert rows[0]["cost_basis"]["quantities"] == {"seconds": 6.0}

    rebuilt = plan_from_cost_records(rows)
    assert rebuilt.calls[0].cost_basis == plan.calls[0].cost_basis
    assert quote_from_cost_records(rows).status in ("unchanged", "changed")


def test_cost_records_omits_the_basis_when_there_is_none():
    """Byte-identical to what nw wrote before nw#74 for a basis-free call."""
    rows = cost_records(Plan(calls=(_basis_free_call(),)))
    assert set(rows[0]) == {
        "tool",
        "application",
        "estimated_cost_usd",
        "cache_status",
    }


def test_legacy_decision_payload_reprices_to_unknown():
    """A payload written before nw#74 has no basis anywhere in it."""
    payload = {
        "shot_id": "shot_01",
        "calls": [
            {
                "tool": "text_to_video",
                "application": PRICED_MODEL,
                "estimated_cost_usd": 3.0,
                "cache_status": "miss",
            }
        ],
        "total_estimated_cost_usd": 3.0,
    }
    quote = quote_render_decision(payload)
    assert quote.status == "unknown"
    assert quote.total_usd is None
    assert quote.as_of_total_usd == 3.0


def test_decision_payload_with_a_basis_reprices_for_real():
    payload = {"calls": cost_records(Plan(calls=(_priced_call(),)))}
    quote = quote_render_decision(payload, pricers=_doubling_pricers())
    assert quote.status == "changed"
    assert quote.total_usd is not None


def test_junk_cost_values_are_unknown_not_zero():
    rows = [{"tool": "t", "application": "a", "estimated_cost_usd": "3 dollars"}]
    quote = quote_from_cost_records(rows)
    assert quote.as_of_total_usd is None
    assert quote.total_usd is None


def test_payload_without_calls_is_a_known_zero():
    assert quote_render_decision({}).total_usd == 0.0


def test_stored_total_is_reported_as_the_as_of_figure():
    plan = Plan(calls=(_priced_call(),))
    payload = {
        "calls": cost_records(plan),
        "total_estimated_cost_usd": plan.total_cost_usd,
    }
    quote = quote_render_decision(payload)
    assert quote.as_of_total_usd == plan.total_cost_usd
    assert quote.status == "unchanged"


def test_a_total_that_contradicts_its_calls_is_unknown():
    """A payload's own headline is neither discarded nor believed.

    ``{"calls": [], "total_estimated_cost_usd": 3.0}`` is a broken record.
    Answering "$0, unchanged" would report a stored $3 as a *known zero* — the
    None-means-unknown invariant, violated by a payload nw's writer never
    produces but a hand-edit can.
    """
    quote = quote_render_decision({"calls": [], "total_estimated_cost_usd": 3.0})
    assert quote.status == "unknown"
    assert quote.total_usd is None
    assert quote.as_of_total_usd == 3.0
    assert "does not match" in quote.reason


def test_a_null_stored_total_leaves_the_calls_to_speak():
    payload = {
        "calls": cost_records(Plan(calls=(_priced_call(),))),
        "total_estimated_cost_usd": None,
    }
    assert quote_render_decision(payload).status == "unchanged"


def test_a_call_unpriced_back_then_keeps_its_honest_none_as_of():
    """The stored total counted an unknown as ``0.0``; the quote must not.

    ``Plan.total_cost_usd`` reads an unknown cost as zero, so a payload whose
    one call was unpriceable at plan time stores a ``0.0`` headline. Adopting
    that as ``as_of_total_usd`` would claim the render *was* quoted at zero.
    And it must not trip the disagreement rule either: the call carries a
    basis, so today's rates price it fine — reporting it as ``unknown`` would
    throw away a perfectly good re-quote.
    """
    unpriced_then = CallPlan(
        tool="text_to_video",
        application=PRICED_MODEL,
        arguments={"prompt": "a tiger"},
        output_kind="video",
        estimated_cost_usd=None,
        cost_basis=catalogue_cost_basis(PRICED_MODEL, seconds=SECONDS),
    )
    plan = Plan(calls=(unpriced_then,))
    payload = {
        "calls": cost_records(plan),
        "total_estimated_cost_usd": plan.total_cost_usd,  # 0.0, not None
    }
    quote = quote_render_decision(payload)
    assert quote.status == "changed"
    assert quote.total_usd == CATALOGUE_COST
    assert quote.as_of_total_usd is None
    assert quote.delta_usd is None
    assert quote.reason == ""


def test_basis_changed_is_surfaced_on_the_quote_and_its_dict():
    plan = Plan(calls=(_priced_call(),))
    moved = current_quote(plan, pricers=_doubling_pricers())
    assert moved.basis_changed is True
    assert moved.to_dict()["basis_changed"] is True
    assert current_quote(plan).basis_changed is False


# ---------------------------------------------------------------------------
# cache identity — the invariant repricing must not disturb
# ---------------------------------------------------------------------------


def test_cache_identity_is_unmoved_by_repricing(project):
    """``cost_basis`` is descriptive: it must not reach any identity.

    Three digests are pinned at once — falaw's ``plan_hash`` across a re-quote,
    ``plan_hash`` across *gaining* a basis, and nw's job idempotency key, which
    is built on that hash. A break here means a resumed render stops deduping
    onto work already paid for.
    """
    bare = Plan(calls=(_basis_free_call(0.25),))
    stamped = Plan(calls=(_priced_call(0.25),))

    # Stamping a basis leaves the plan's digest byte-identical...
    assert plan_hash(stamped) == plan_hash(bare)

    # ...and so does re-quoting it, including when the price moves.
    moved = current_quote(stamped, pricers=_doubling_pricers())
    assert moved.total_usd != stamped.total_cost_usd  # the price really moved
    assert plan_hash(moved.repriced.plan) == plan_hash(stamped)

    # nw's job idempotency key rides that hash, so it is unmoved too.
    key = jobs._default_idempotency_key(project, "render", {"plan": bare})
    assert key == jobs._default_idempotency_key(project, "render", {"plan": stamped})
    assert key == jobs._default_idempotency_key(
        project, "render", {"plan": moved.repriced.plan}
    )


# ---------------------------------------------------------------------------
# the jobs cost gate
# ---------------------------------------------------------------------------


def test_gate_prices_the_supplied_plan_and_ignores_the_stale_figure(project):
    """A caller's ``estimated_usd`` is not evidence when a plan is supplied."""
    plan = Plan(calls=(_priced_call(),))
    out = jobs.estimate(project, "render", {"plan": plan, "estimated_usd": 0.01})
    assert out["estimated_usd"] == plan.total_cost_usd == CATALOGUE_COST
    assert out["estimated_usd"] != 0.01
    assert out["has_unknown_costs"] is False
    assert out["quote"]["status"] == "unchanged"


def test_gate_refuses_to_price_a_basis_free_plan(project):
    plan = Plan(calls=(_basis_free_call(0.25),))
    out = jobs.estimate(project, "render", {"plan": plan, "estimated_usd": 0.25})
    assert out["estimated_usd"] is None
    assert out["has_unknown_costs"] is True
    assert out["requires_approval"] is True


def test_gate_still_falls_back_when_no_plan_is_supplied(project):
    out = jobs.estimate(project, "render", {"estimated_usd": 0.20})
    assert out["estimated_usd"] == 0.20
    assert out["requires_approval"] is False
    assert out["quote"] is None


def test_gate_reprices_a_serialized_plan(project):

    plan = Plan(calls=(_priced_call(),))
    out = jobs.estimate(project, "render", {"plan": plan_to_dict(plan)})
    assert out["estimated_usd"] == plan.total_cost_usd


def test_gate_refuses_a_plan_shaped_thing_that_is_not_a_plan(project):
    out = jobs.estimate(project, "render", {"plan": "not a plan"})
    assert out["estimated_usd"] is None
    assert out["requires_approval"] is True
    assert out["quote"]["reason"] == jobs.UNREADABLE_PLAN_REASON


def test_gate_reason_is_a_fixed_sentence_not_an_exception_text(project):
    """The reason reaches a spend surface; an exception message is for a log."""
    out = jobs.estimate(project, "render", {"plan": {"calls": "not a list"}})
    assert out["quote"]["reason"] == jobs.UNREADABLE_PLAN_REASON


def test_gate_reports_the_callers_figure_beside_todays(project):
    plan = Plan(calls=(_priced_call(),))
    out = jobs.estimate(project, "render", {"plan": plan, "estimated_usd": 0.01})
    assert out["quote"]["caller_estimated_usd"] == 0.01
    assert out["quote"]["total_usd"] == plan.total_cost_usd


def _doubled_quote(plan, **_):
    """``current_quote`` as if every rate in the catalogue had doubled."""
    return current_quote(plan, pricers=_doubling_pricers())


def test_a_serialized_plan_hashes_to_what_the_object_hashes_to(project):
    """The enqueue path is only real if both forms are one identity.

    A job's ``params`` are JSON-serialized into the index, so a caller holding
    a live ``Plan`` passes ``plan_to_dict(plan)``. If that dict keyed
    differently from the object, the same render submitted twice would dedup
    under two keys and bill twice.
    """
    plan = Plan(calls=(_priced_call(),))
    assert jobs._default_idempotency_key(
        project, "render", {"plan": plan}
    ) == jobs._default_idempotency_key(project, "render", {"plan": plan_to_dict(plan)})


def test_an_unidentifiable_plan_is_refused_loudly(project):
    with pytest.raises(TypeError, match="plan_to_dict"):
        jobs._default_idempotency_key(project, "render", {"plan": "not a plan"})


def test_enqueue_dedups_across_a_rate_change(project, monkeypatch):
    """A price that moved must not move the job's identity.

    The regression this guards: falaw 0.0.46 re-quoted premium calls tenfold,
    and had that number reached ``plan_hash`` a resumed render would have
    re-run — and re-billed — work already paid for.
    """
    import nw.pricing as pricing

    plan = Plan(calls=(_priced_call(),))
    params = {"plan": plan_to_dict(plan)}
    dispatch = {"render": lambda project, params, **kw: {"ok": True}}

    first = jobs.enqueue(project, "render", params, dispatch=dispatch)
    assert first.cost.estimated_usd == plan.total_cost_usd

    # ...and now the rate table moves underneath an identical resubmission.
    monkeypatch.setattr(pricing, "current_quote", _doubled_quote)
    assert jobs.estimate(project, "render", params)["estimated_usd"] != (
        first.cost.estimated_usd
    )  # the price really did move

    second = jobs.enqueue(project, "render", params, dispatch=dispatch)
    assert second.job_id == first.job_id  # ...and the identity did not


def test_enqueued_job_records_the_gate_s_estimate(project):
    """``enqueue`` runs the same gate as :func:`nw.jobs.estimate`.

    Exercised without a plan because a live :class:`falaw.Plan` cannot ride
    ``params`` through the job index today — the record is JSON-serialized.
    The plan path is covered above, on the gate itself.
    """
    job = jobs.enqueue(
        project,
        "render",
        {"estimated_usd": 0.20},
        dispatch={"render": lambda project, params, **kw: {"ok": True}},
    )
    assert job.cost.estimated_usd == 0.20


# ---------------------------------------------------------------------------
# the renderer that hand-builds its call
# ---------------------------------------------------------------------------


def test_text_to_video_stamps_a_basis_so_its_quote_can_be_refreshed():
    """The one strategy that builds a ``CallPlan`` outside a ``plan_*``."""
    from nw.renderers import get_strategy

    class _Prep:
        shot_id = "shot_01"
        storyboard_prompt = "a tiger in the rain"
        duration_s = 6.0

    plan = get_strategy("text_to_video").plan(_Prep(), quality="balanced")
    basis = plan.calls[0].cost_basis
    assert basis is not None
    assert basis.pricer == "model_catalogue"
    assert basis.quantities == {"seconds": 6.0}
    assert current_quote(plan).status == "unchanged"


def test_llm_rates_pricer_is_still_the_default_seam():
    """A guard on the falaw contract nw leans on, not on nw itself."""
    assert LLM_RATES_PRICER in DFLT_PRICERS
