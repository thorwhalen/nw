"""Tests for nw#25 — a Transform's failure policy.

The defect: ``BaseTransform.execute`` ran the whole Plan through
``execute_plan``, which raises on the first failure and returns nothing. On a
fan-out that means **one** rate-limited call discards 199 paid renders.

What these pin:

* ``"halt"`` is unchanged — same original exception, nothing written;
* ``"isolate"`` writes every success to the graph *before* reporting failures;
* skeletons and outcomes stay aligned when calls drop out (the zip that would
  otherwise pair panel 48's artifact onto panel 47's skeleton);
* cost is attributed to what actually ran, and ``$0.00`` stays distinguishable
  from "unknown".
"""

from __future__ import annotations

import importlib

import pytest
from lacing import Annotation, Artifact

from nw.transforms import BaseTransform


BODY_URI = "annot://schema/test-body/v1"


def _plan(n: int, *, cost=1.0):
    from falaw import CallPlan, Plan

    return Plan(
        calls=tuple(
            CallPlan(
                tool="generate_image",
                application="test/app",
                arguments={"i": i},
                output_kind="image",
                estimated_cost_usd=cost,
            )
            for i in range(n)
        )
    )


def _skel(n: int):
    import uuid

    from lacing import MediaRef, Provenance, RationalTime, TimeInterval

    iv = TimeInterval(RationalTime(0), RationalTime(24000))
    prov = Provenance(
        was_generated_by="transform:t@1",
        was_attributed_to="agent:test",
        was_derived_from=[],
        generated_at_time=RationalTime.now(),
        activity="derive",
    )
    return tuple(
        Annotation(
            id=uuid.uuid4(),
            tier="t",
            reference=MediaRef(asset_id="a" * 64, interval=iv),
            body={"index": i, "artifact_id": None},
            body_schema_uri=BODY_URI,
            provenance=prov,
        )
        for i in range(n)
    )


def _artifact(seed: str, provenance):
    return Artifact(
        asset_id=seed * 64,
        kind="image",
        bytes_size=1,
        provenance=provenance,
    )


def _report(calls, *, succeeded, failures=(), cache_hits=()):
    """A real ``falaw.ExecutionReport``.

    Built from falaw's own types rather than faked: the invariant under test is
    that ``outcomes`` is full-length in plan order **by construction**, so a
    convenient stub would be testing nw against a contract falaw does not have.
    """
    from falaw.outcomes import CallOutcome, ExecutionReport

    failures = dict(failures)
    outcomes = []
    for i, call in enumerate(calls):
        if i in failures:
            status, reason = failures[i]
            outcomes.append(
                CallOutcome(
                    index=i,
                    call=call,
                    status=status,
                    error=RuntimeError(reason) if status == "failed" else None,
                    reason=reason,
                    blocked_by=(0,) if status == "blocked" else (),
                )
            )
        else:
            outcomes.append(
                CallOutcome(
                    index=i,
                    call=call,
                    status="succeeded",
                    artifact=succeeded[i],
                    cache_hit=i in cache_hits,
                )
            )
    return ExecutionReport(outcomes=tuple(outcomes))


class _Graph:
    def __init__(self):
        self.written = []

    def add_annotation(self, ann):
        self.written.append(ann)


class _Project:
    def __init__(self):
        self.graph = _Graph()


@pytest.fixture()
def patch_execute(monkeypatch):
    """Install a stub ``execute_plan_isolated`` in the transforms module."""
    # `nw.transforms` the ATTRIBUTE is the Registry, not the module.
    module = importlib.import_module("nw.transforms")

    def install(report, *, capture=None):
        def stub(plan, **kwargs):
            if capture is not None:
                capture.update(kwargs)
            return report

        monkeypatch.setattr(module, "execute_plan_isolated", stub)

    return install


# --- halt: unchanged --------------------------------------------------------


def test_halt_is_the_default_and_re_raises_the_original_error(patch_execute):
    plan, skeleton = _plan(3), _skel(3)
    arts = {0: _artifact("a", skeleton[0].provenance)}
    boom = RuntimeError("rate limited")
    from falaw.outcomes import CallOutcome, ExecutionReport

    report = ExecutionReport(
        outcomes=(
            CallOutcome(index=0, call=plan.calls[0], status="succeeded", artifact=arts[0]),
            CallOutcome(index=1, call=plan.calls[1], status="failed", error=boom),
            CallOutcome(
                index=2,
                call=plan.calls[2],
                status="blocked",
                blocked_by=(1,),
                reason="upstream call 1 failed",
            ),
        )
    )
    patch_execute(report)
    project = _Project()

    with pytest.raises(RuntimeError) as caught:
        BaseTransform().execute(project, plan, skeleton)

    assert caught.value is boom, "the original typed exception, unwrapped"
    assert project.graph.written == [], "halt writes nothing"


def test_halt_asks_falaw_to_stop_early(patch_execute):
    """`halt` must not pay for the rest of the plan before deciding to raise."""
    plan, skeleton = _plan(2), _skel(2)
    arts = [_artifact("a", skeleton[0].provenance), _artifact("b", skeleton[1].provenance)]
    kwargs = {}
    patch_execute(_report(plan.calls, succeeded=arts), capture=kwargs)

    BaseTransform().execute(_Project(), plan, skeleton)

    assert kwargs["halt_on_failure"] is True


def test_isolate_asks_falaw_to_keep_going(patch_execute):
    plan, skeleton = _plan(2), _skel(2)
    arts = [_artifact("a", skeleton[0].provenance), _artifact("b", skeleton[1].provenance)]
    kwargs = {}
    patch_execute(_report(plan.calls, succeeded=arts), capture=kwargs)

    BaseTransform().execute(_Project(), plan, skeleton, on_failure="isolate")

    assert kwargs["halt_on_failure"] is False


# --- isolate ----------------------------------------------------------------


def test_isolate_writes_the_successes_and_reports_the_rest(patch_execute):
    """The point of the issue: one bad call must not discard the paid ones."""
    plan, skeleton = _plan(4), _skel(4)
    succeeded = {
        0: _artifact("a", skeleton[0].provenance),
        3: _artifact("d", skeleton[3].provenance),
    }
    patch_execute(
        _report(
            plan.calls,
            succeeded=succeeded,
            failures={1: ("failed", "rate limited"), 2: ("blocked", "upstream failed")},
        )
    )
    project = _Project()

    result = BaseTransform().execute(project, plan, skeleton, on_failure="isolate")

    assert len(result.annotations) == 2
    assert len(project.graph.written) == 2, "successes reach the graph"
    assert [f.status for f in result.failed] == ["failed"]
    assert [b.status for b in result.blocked] == ["blocked"]
    assert result.is_complete is False


def test_a_failed_output_names_which_skeleton_is_missing(patch_execute):
    """A UI needs 'panel 47 is missing because X', not an unexplained hole."""
    plan, skeleton = _plan(3), _skel(3)
    succeeded = {0: _artifact("a", skeleton[0].provenance), 2: _artifact("c", skeleton[2].provenance)}
    patch_execute(
        _report(plan.calls, succeeded=succeeded, failures={1: ("failed", "rate limited")})
    )

    result = BaseTransform().execute(_Project(), plan, skeleton, on_failure="isolate")

    (missing,) = result.failed
    assert missing.skeleton is skeleton[1], "the skeleton that was planned"
    assert missing.skeleton.body["index"] == 1
    assert missing.reason == "rate limited"
    assert isinstance(missing.error, RuntimeError)


def test_a_blocked_output_says_what_blocked_it(patch_execute):
    plan, skeleton = _plan(2), _skel(2)
    patch_execute(
        _report(
            plan.calls,
            succeeded={},
            failures={0: ("failed", "boom"), 1: ("blocked", "upstream 0 failed")},
        )
    )

    result = BaseTransform().execute(_Project(), plan, skeleton, on_failure="isolate")

    (blocked,) = result.blocked
    assert blocked.blocked_by == (0,)
    assert "upstream" in blocked.reason


def test_skeletons_stay_aligned_when_a_middle_call_drops_out(patch_execute):
    """The alignment bug this issue is named for.

    Zipping skeletons against the *artifact* list pairs call 2's artifact onto
    skeleton 1 the moment call 1 fails. Zipping against `outcomes` cannot,
    because outcomes is full-length in plan order.
    """
    plan, skeleton = _plan(3), _skel(3)
    succeeded = {
        0: _artifact("a", skeleton[0].provenance),
        2: _artifact("c", skeleton[2].provenance),
    }
    patch_execute(
        _report(plan.calls, succeeded=succeeded, failures={1: ("failed", "boom")})
    )

    result = BaseTransform().execute(_Project(), plan, skeleton, on_failure="isolate")

    by_index = {ann.body["index"]: ann for ann in result.annotations}
    assert set(by_index) == {0, 2}, "skeleton 1 must not be completed"
    assert by_index[0].body["artifact_id"] == "a" * 64
    assert by_index[2].body["artifact_id"] == "c" * 64, (
        "skeleton 2 got call 2's artifact, not call 1's"
    )


def test_a_fully_successful_isolate_run_is_complete(patch_execute):
    plan, skeleton = _plan(2), _skel(2)
    arts = [_artifact("a", skeleton[0].provenance), _artifact("b", skeleton[1].provenance)]
    patch_execute(_report(plan.calls, succeeded=dict(enumerate(arts))))

    result = BaseTransform().execute(_Project(), plan, skeleton, on_failure="isolate")

    assert result.is_complete is True
    assert result.failed == () and result.blocked == ()


# --- cost -------------------------------------------------------------------


def test_cost_counts_only_what_actually_ran(patch_execute):
    """The report, not `sum(artifact.cost_usd)`, is the spend source.

    Since falaw#26 the artifacts are also stamped from the observed outcome,
    so the sums agree — but the report is the run-level truth (it carries
    has_unknown_costs, and a failed call may not have been billed at all).
    """
    plan, skeleton = _plan(4, cost=2.0), _skel(4)
    succeeded = {
        0: _artifact("a", skeleton[0].provenance),
        1: _artifact("b", skeleton[1].provenance),
        3: _artifact("d", skeleton[3].provenance),
    }
    patch_execute(
        _report(
            plan.calls,
            succeeded=succeeded,
            failures={2: ("failed", "boom")},
            cache_hits={1},
        )
    )

    result = BaseTransform().execute(_Project(), plan, skeleton, on_failure="isolate")

    # Two succeeded-and-not-cached calls at $2; the cache hit and the failure
    # are both excluded.
    assert result.cost_usd_actual == 4.0
    assert result.cache_hit_savings_usd == 2.0


def test_an_unknown_cost_stays_distinguishable_from_zero(patch_execute):
    """$0.00-because-unknown reading as 'free' is the #208 failure mode."""
    plan, skeleton = _plan(1, cost=None), _skel(1)
    patch_execute(
        _report(plan.calls, succeeded={0: _artifact("a", skeleton[0].provenance)})
    )

    result = BaseTransform().execute(_Project(), plan, skeleton)

    assert result.cost_usd_actual == 0.0
    assert result.has_unknown_costs is True


# --- guards -----------------------------------------------------------------


def test_an_unknown_policy_is_refused(patch_execute):
    plan, skeleton = _plan(1), _skel(1)
    patch_execute(_report(plan.calls, succeeded={0: _artifact("a", skeleton[0].provenance)}))

    with pytest.raises(ValueError, match="on_failure must be"):
        BaseTransform().execute(_Project(), plan, skeleton, on_failure="ignore")


def test_the_policy_check_precedes_any_spending(monkeypatch):
    module = importlib.import_module("nw.transforms")
    called = []
    monkeypatch.setattr(
        module, "execute_plan_isolated", lambda *a, **k: called.append(1)
    )

    with pytest.raises(ValueError):
        BaseTransform().execute(None, _plan(1), _skel(1), on_failure="nonsense")

    assert called == []


# --- a billed call whose result cannot become an annotation -----------------


class _RefusingTransform(BaseTransform):
    """Completion fails for one index — falaw succeeded and billed anyway."""

    output_kind = BODY_URI

    def __init__(self, bad_index: int, error: Exception):
        self.bad_index = bad_index
        self.error = error

    def _complete_annotation(self, skeleton, artifact):
        if skeleton.body["index"] == self.bad_index:
            raise self.error
        return skeleton.model_copy(
            update={"body": {**skeleton.body, "artifact_id": artifact.asset_id}}
        )


@pytest.mark.parametrize(
    "error",
    [
        ValueError("json artifact has no `path` to read"),
        NotImplementedError("text artifacts have no obvious target field"),
        __import__("json").JSONDecodeError("Expecting value", "prose", 0),
    ],
)
def test_a_completion_failure_does_not_discard_the_paid_siblings(patch_execute, error):
    """This issue's own defect, one layer up.

    falaw can succeed, bill, and still hand back something the Transform cannot
    turn into an annotation — a `json` artifact whose asset could not be read
    has `path=None`, a `text` artifact has no target field, and an LLM that
    prefaces its JSON with prose makes `json.loads` raise. Letting that
    propagate discards every sibling in the run.
    """
    plan, skeleton = _plan(4), _skel(4)
    arts = {i: _artifact(chr(ord("a") + i), skeleton[i].provenance) for i in range(4)}
    patch_execute(_report(plan.calls, succeeded=arts))
    project = _Project()

    result = _RefusingTransform(2, error).execute(
        project, plan, skeleton, on_failure="isolate"
    )

    assert len(result.annotations) == 3, "the other three were paid for"
    assert len(project.graph.written) == 3
    (missing,) = result.failed
    assert missing.skeleton.body["index"] == 2
    assert missing.status == "failed"
    assert "could not be turned into" in missing.reason
    assert result.is_complete is False


def test_a_completion_failure_still_raises_under_halt(patch_execute):
    """`halt` means halt. Only the isolate policy converts it to a report."""
    plan, skeleton = _plan(2), _skel(2)
    arts = {i: _artifact(chr(ord("a") + i), skeleton[i].provenance) for i in range(2)}
    patch_execute(_report(plan.calls, succeeded=arts))

    with pytest.raises(ValueError, match="nope"):
        _RefusingTransform(1, ValueError("nope")).execute(
            _Project(), plan, skeleton, on_failure="halt"
        )


def test_a_short_report_is_an_error_not_a_silent_truncation(patch_execute):
    """`zip(strict=True)`.

    falaw guarantees one outcome per call, but the guard nw kept only pins
    `len(skeleton) == len(plan.calls)`. Without `strict`, a short report drops
    skeletons silently *and* reports `is_complete=True`.
    """
    plan, skeleton = _plan(5), _skel(5)
    short = _plan(2)
    arts = {i: _artifact(chr(ord("a") + i), skeleton[i].provenance) for i in range(2)}
    patch_execute(_report(short.calls, succeeded=arts))

    with pytest.raises(ValueError):
        BaseTransform().execute(_Project(), plan, skeleton, on_failure="isolate")


def test_artifacts_are_reported(patch_execute):
    plan, skeleton = _plan(2), _skel(2)
    arts = {i: _artifact(chr(ord("a") + i), skeleton[i].provenance) for i in range(2)}
    patch_execute(_report(plan.calls, succeeded=arts))

    result = BaseTransform().execute(_Project(), plan, skeleton)

    assert [a.asset_id for a in result.artifacts] == ["a" * 64, "b" * 64]


def test_known_costs_do_not_set_the_unknown_flag(patch_execute):
    """The other half of the unknown-cost guard: it must not be always-true."""
    plan, skeleton = _plan(1, cost=3.0), _skel(1)
    patch_execute(
        _report(plan.calls, succeeded={0: _artifact("a", skeleton[0].provenance)})
    )

    result = BaseTransform().execute(_Project(), plan, skeleton)

    assert result.has_unknown_costs is False
    assert result.cost_usd_actual == 3.0


# --- RenderStrategyTransform: the shot-level policy -------------------------
#
# The adapter is what actually spends money on video in muvid, and its whole
# failure branch shipped unguarded in the first cut: five separate mutations of
# it left the suite green.


def _render_transform():
    from nw.transforms._adapters.render_strategy import RenderStrategyTransform

    class _Strategy:
        name = "fake"

        def materialize(self, prep, plan, artifacts):
            import pathlib

            return pathlib.Path("/tmp/out.mp4")

    return RenderStrategyTransform(_Strategy())


class _RenderProject(_Project):
    """A `_Project` with the `root` the adapter's success path needs."""

    def __init__(self):
        super().__init__()
        import pathlib

        self.root = pathlib.Path("/tmp")


def _render_skeleton():
    (skel,) = _skel(1)
    return (skel.model_copy(update={"body": {**skel.body, "shot_id": "s1"}}),)


@pytest.fixture()
def patch_render_execute(monkeypatch):
    module = importlib.import_module("nw.transforms._adapters.render_strategy")

    def install(report, *, capture=None):
        def stub(plan, **kwargs):
            if capture is not None:
                capture.update(kwargs)
            return report

        monkeypatch.setattr(module, "execute_plan_isolated", stub)
        monkeypatch.setattr(
            "nw.workflow.prepare_shot", lambda *a, **k: object(), raising=False
        )

    return install


def test_render_strategy_halt_raises_rather_than_returning_empty(patch_render_execute):
    """`annotations[0]` is the reelee idiom; an empty tuple is an IndexError."""
    plan, skeleton = _plan(2), _render_skeleton()
    boom = RuntimeError("fal said no")
    from falaw.outcomes import CallOutcome, ExecutionReport

    patch_render_execute(
        ExecutionReport(
            outcomes=(
                CallOutcome(index=0, call=plan.calls[0], status="failed", error=boom),
                CallOutcome(
                    index=1,
                    call=plan.calls[1],
                    status="blocked",
                    blocked_by=(0,),
                    reason="upstream failed",
                ),
            )
        )
    )

    with pytest.raises(RuntimeError) as caught:
        _render_transform().execute(_Project(), plan, skeleton)

    assert caught.value is boom


def test_render_strategy_refuses_an_unknown_policy(patch_render_execute):
    """Without this, any typo silently selects `isolate` and halt stops halting."""
    plan, skeleton = _plan(1), _render_skeleton()
    patch_render_execute(_report(plan.calls, succeeded={0: _artifact("a", skeleton[0].provenance)}))

    for bad in ("ignore", "HALT", "", None):
        with pytest.raises(ValueError, match="on_failure must be"):
            _render_transform().execute(_Project(), plan, skeleton, on_failure=bad)


def test_render_strategy_isolate_reports_the_shot_instead_of_raising(
    patch_render_execute,
):
    plan, skeleton = _plan(1), _render_skeleton()
    patch_render_execute(
        _report(plan.calls, succeeded={}, failures={0: ("failed", "fal said no")})
    )

    result = _render_transform().execute(
        _Project(), plan, skeleton, on_failure="isolate"
    )

    assert result.annotations == ()
    assert [f.status for f in result.failed] == ["failed"]
    assert result.blocked == ()


def test_render_strategy_files_a_blocked_shot_under_blocked(patch_render_execute):
    """`if result.failed: retry_verbatim()` must not retry something re-planned."""
    plan, skeleton = _plan(1), _render_skeleton()
    patch_render_execute(
        _report(plan.calls, succeeded={}, failures={0: ("blocked", "upstream failed")})
    )

    result = _render_transform().execute(
        _Project(), plan, skeleton, on_failure="isolate"
    )

    assert result.failed == ()
    assert [b.status for b in result.blocked] == ["blocked"]


def test_render_strategy_costs_what_actually_ran(patch_render_execute):
    plan, skeleton = _plan(2, cost=5.0), _render_skeleton()
    arts = {0: _artifact("a", skeleton[0].provenance), 1: _artifact("b", skeleton[0].provenance)}
    patch_render_execute(_report(plan.calls, succeeded=arts, cache_hits={1}))

    result = _render_transform().execute(_RenderProject(), plan, skeleton)

    assert result.cost_usd_actual == 5.0, "the cache hit is not spend"
    assert result.cache_hit_savings_usd == 5.0
    assert result.has_unknown_costs is False


def test_render_strategy_flags_an_unknown_cost(patch_render_execute):
    plan, skeleton = _plan(1, cost=None), _render_skeleton()
    patch_render_execute(
        _report(plan.calls, succeeded={0: _artifact("a", skeleton[0].provenance)})
    )

    result = _render_transform().execute(_RenderProject(), plan, skeleton)

    assert result.has_unknown_costs is True


def test_render_strategy_bypasses_the_cache_on_force(patch_render_execute):
    plan, skeleton = _plan(1), _render_skeleton()
    kwargs = {}
    patch_render_execute(
        _report(plan.calls, succeeded={0: _artifact("a", skeleton[0].provenance)}),
        capture=kwargs,
    )

    _render_transform().execute(_RenderProject(), plan, skeleton, force=True)

    assert kwargs["use_cache"] is False
