"""nw#72 — ``Transform.execute(force=True)`` must not double-spend.

The defect: both nw call sites translated the caller's ``force`` into
``use_cache=use_cache and not force``, and falaw's ``use_cache`` gated the
cache **write** as well as the read. So a forced run paid for a result, threw
it away, and the next ordinary consumer re-billed it — measured downstream as
thorwhalen/reelee#384, where ``run_stage(force=True)`` reaches
``BaseTransform.execute``.

falaw 0.0.45 split the halves (falaw#49): ``refresh=True`` skips the read and
keeps the write. ``force`` is a *read* instruction, so it maps to ``refresh``.

The regression here counts **vendor calls** through a counting fake
``fal_client`` — the unit the defect is measured in, not a cache internal —
with the real ``falaw`` executor and the real on-disk cache underneath
(``conftest.py``'s autouse fixtures isolate the cache, serve assets from
memory, and fail the test on any outbound socket). Mutation-tested: restoring
``use_cache=use_cache and not force`` at either call site turns
:func:`test_a_forced_run_leaves_its_paid_result_in_the_cache` red on
``2 != 1``.

The render-strategy adapter is covered by asserting the pair it forwards
rather than by a second vendor count: its ``execute`` also runs ``materialize``
(ffmpeg over the artifact), which synthetic test bytes cannot satisfy, so an
end-to-end run there would be testing the fake asset transport. The pair it
forwards is the whole of what nw#72 changed in that method.
"""

from __future__ import annotations

import importlib
import sys
import types
import uuid

import pytest
from lacing import Annotation, MediaRef, Provenance, RationalTime, TimeInterval

from nw.transforms import BaseTransform, CacheModeConflict, resolve_cache_mode

BODY_URI = "annot://schema/test-body/v1"
IMAGE_APPLICATION = "fal-ai/flux/dev"
IMAGE_COST_USD = 0.025


def _install_counting_fal(monkeypatch) -> list[str]:
    """Install a fake ``fal_client`` recording one entry per vendor call.

    Returns the (live) list of applications called, in order — its ``len`` is
    the spend. Each call mints a fresh URL, as fal does.
    """
    called: list[str] = []

    def subscribe(application, *, arguments, with_logs, on_queue_update):
        called.append(application)
        return {
            "images": [
                {
                    "url": f"https://fal.media/nw72-{len(called)}.png",
                    "content_type": "image/png",
                }
            ]
        }

    fake = types.SimpleNamespace(
        InProgress=type("InProgress", (), {"__init__": lambda self, logs: None}),
        subscribe=subscribe,
    )
    monkeypatch.setitem(sys.modules, "fal_client", fake)
    return called


class _ImageTransform(BaseTransform):
    """A minimal 1:1 Transform: one image call, one completed annotation."""

    name = "test_to_image.fal.counting"
    output_kind = BODY_URI


def _plan(prompt: str = "a red panda"):
    from falaw import CallPlan, Plan

    return Plan(
        calls=(
            CallPlan(
                tool="generate_image",
                application=IMAGE_APPLICATION,
                arguments={"prompt": prompt},
                output_kind="image",
                estimated_cost_usd=IMAGE_COST_USD,
            ),
        )
    )


def _skeleton():
    interval = TimeInterval(RationalTime(0), RationalTime(24000))
    provenance = Provenance(
        was_generated_by=f"transform:{_ImageTransform.name}@1",
        was_attributed_to="agent:test",
        was_derived_from=[],
        generated_at_time=RationalTime.now(),
        activity="derive",
    )
    return (
        Annotation(
            id=uuid.uuid4(),
            tier="t",
            reference=MediaRef(asset_id="a" * 64, interval=interval),
            body={"artifact_id": None},
            body_schema_uri=BODY_URI,
            provenance=provenance,
        ),
    )


class _Graph:
    def __init__(self):
        self.written: list[Annotation] = []
        self.unproduced: list[dict] = []

    def add_annotation(self, annotation, **kwargs):
        self.written.append(annotation)

    def add_unproduced_output(self, skeleton, **kwargs):
        self.unproduced.append({"skeleton": skeleton, **kwargs})


class _Project:
    def __init__(self):
        self.graph = _Graph()


# --- the regression ---------------------------------------------------------


def test_a_forced_run_leaves_its_paid_result_in_the_cache(monkeypatch):
    """The reelee#384 sequence in vendor calls: forced run, then a consumer.

    Cold cache. ``force=True`` re-executes (**one** call) and keeps what it
    paid for, so the ordinary follow-up — the next ``run_stage`` — is served
    from the cache and bills nothing. Total: 1.

    Under the old ``use_cache=use_cache and not force`` the forced run never
    reaches falaw's cache writer, the follow-up re-bills, and this asserts
    ``2 == 1``.
    """
    called = _install_counting_fal(monkeypatch)
    transform, plan, project = _ImageTransform(), _plan(), _Project()

    result = transform.execute(project, plan, _skeleton(), force=True)

    assert len(called) == 1, "the forced run itself executes exactly once"
    assert len(result.annotations) == 1, "and it produced its annotation"

    followup = transform.execute(project, plan, _skeleton())

    assert len(called) == 1, (
        "the forced run's paid result must be reusable — a follow-up that "
        "re-bills means the forced run discarded what it bought (nw#72)"
    )
    assert followup.cache_hit_savings_usd == pytest.approx(IMAGE_COST_USD), (
        "and falaw must report it as a saving, not as fresh spend"
    )


def test_a_default_run_is_still_served_from_the_cache(monkeypatch):
    """Guard on the unmoved corner: the default mode reads as it always did."""
    called = _install_counting_fal(monkeypatch)
    transform, plan, project = _ImageTransform(), _plan(), _Project()

    transform.execute(project, plan, _skeleton())
    transform.execute(project, plan, _skeleton())

    assert len(called) == 1


def test_force_re_executes_rather_than_replaying_the_cached_answer(monkeypatch):
    """``force`` must still skip the READ — keeping the write is not enough.

    Without this, "route force to refresh" could be satisfied by dropping
    ``force`` on the floor: the counts in the regression above would be 1 and
    1 there too. Warm the cache first, so a mode that reads would bill zero.
    """
    called = _install_counting_fal(monkeypatch)
    transform, plan, project = _ImageTransform(), _plan(), _Project()

    transform.execute(project, plan, _skeleton())
    assert len(called) == 1

    transform.execute(project, plan, _skeleton(), force=True)

    assert len(called) == 2, "force ignores the cached answer and re-runs"


def test_use_cache_false_still_touches_the_cache_not_at_all(monkeypatch):
    """The other unmoved corner: ``use_cache=False`` writes nothing, as before."""
    called = _install_counting_fal(monkeypatch)
    transform, plan, project = _ImageTransform(), _plan(), _Project()

    transform.execute(project, plan, _skeleton(), use_cache=False)
    transform.execute(project, plan, _skeleton(), use_cache=False)

    assert len(called) == 2, "nothing was written, so nothing can be read"


# --- the contradictory pair -------------------------------------------------


def test_use_cache_false_with_force_is_refused_before_anything_runs(monkeypatch):
    """``(False, True)`` has no meaning — there is no key to write under.

    Before nw#72 it collapsed silently to "no cache at all", handing the caller
    a spend policy they did not ask for. It must raise in nw's own vocabulary
    (naming ``force``, not falaw's ``refresh``) and cost nothing.
    """
    called = _install_counting_fal(monkeypatch)
    transform, plan, project = _ImageTransform(), _plan(), _Project()

    with pytest.raises(CacheModeConflict) as caught:
        transform.execute(project, plan, _skeleton(), use_cache=False, force=True)

    assert "force" in str(caught.value)
    assert called == [], "the refusal happens before any vendor call"
    assert project.graph.written == [], "and before anything reaches the graph"


def test_the_conflict_is_a_value_error():
    """Callers already catching ``ValueError`` — falaw's own refusal of the
    same corner is one — must keep working."""
    assert issubclass(CacheModeConflict, ValueError)


@pytest.mark.parametrize(
    "use_cache, force, expected",
    [
        (True, False, (True, False)),
        (True, True, (True, True)),
        (False, False, (False, False)),
    ],
)
def test_resolve_cache_mode_maps_the_three_meaningful_modes(use_cache, force, expected):
    assert resolve_cache_mode(use_cache=use_cache, force=force) == expected


# --- the second call site: the render-strategy adapter ----------------------


class _Stop(Exception):
    """Sentinel: the forwarded kwargs are all this probe needs."""


def _forwarded_kwargs(monkeypatch, module_path: str, run) -> dict:
    """Capture the kwargs ``module_path`` passes to ``execute_plan_isolated``."""
    module = importlib.import_module(module_path)
    captured: dict = {}

    def stub(plan, **kwargs):
        captured.update(kwargs)
        raise _Stop()

    monkeypatch.setattr(module, "execute_plan_isolated", stub)
    with pytest.raises(_Stop):
        run()
    return captured


def _adapter_and_skeleton(monkeypatch):
    import nw.workflow
    from nw.transforms import get_transform

    monkeypatch.setattr(nw.workflow, "prepare_shot", lambda *a, **k: object())
    transform = get_transform("shot_to_render_result.fal.still")
    skeleton = _skeleton()[0].model_copy(update={"body": {"shot_id": "s01"}})
    return transform, (skeleton,)


@pytest.mark.parametrize("force, expected_refresh", [(False, False), (True, True)])
def test_the_adapter_forwards_force_as_refresh(monkeypatch, force, expected_refresh):
    """The render-strategy adapter's half of nw#72 (``render_strategy.py``).

    Asserted on the forwarded pair rather than on a vendor count — see this
    module's docstring for why. ``prepare_shot`` is stubbed out because the
    pair is decided before it runs.
    """
    transform, skeleton = _adapter_and_skeleton(monkeypatch)

    kwargs = _forwarded_kwargs(
        monkeypatch,
        "nw.transforms._adapters.render_strategy",
        lambda: transform.execute(
            _Project(), _plan(), skeleton, use_cache=True, force=force
        ),
    )

    assert kwargs["use_cache"] is True, "the write half stays on under force"
    assert kwargs["refresh"] is expected_refresh


def test_the_adapter_refuses_the_contradictory_pair(monkeypatch):
    transform, skeleton = _adapter_and_skeleton(monkeypatch)

    with pytest.raises(CacheModeConflict):
        transform.execute(_Project(), _plan(), skeleton, use_cache=False, force=True)
