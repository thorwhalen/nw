"""Verifying traces travel with the annotation they describe (nw#36).

A verifying trace is a sidecar record: it exists only to describe one
annotation's recorded inputs. Before nw#36, every deletion path removed the
annotation and left the trace — inert for correctness, unbounded for growth,
and (on an id re-used within one RationalTime tick) able to answer a
freshness query from digests recorded for content that is no longer there.

Two layers, tested separately:

- **Delete-with-target**: each nw deletion path removes the traces naming
  what it removes — :meth:`nw.ProjectGraph.remove_annotation`,
  ``write_spec``'s entity reconciliation, the storyboard save's wipe.
- **The backstop**: :func:`nw.collect_orphan_traces` sweeps traces whose
  target no longer resolves anywhere in the project, catching deletions that
  never went through nw (a direct ``store.remove``, pre-nw#36 history).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import nw
from lacing import (
    Annotation,
    MediaRef,
    Provenance,
    RationalTime,
    Tier,
    TierStereotype,
    TimeInterval,
)
from lacing.digest import VALUE_DIGEST_SCHEME
from nw.bodies import (
    VERIFYING_TRACE_BODY_SCHEMA_URI,
    VERIFYING_TRACE_TIER,
    SectionBodyV1,
    VerifyingTraceBodyV1,
)
from nw.graph_backend import SCOPE_STORYBOARD, open_graph_store
from nw.schema import SectionSpec, ShotSpec
from nw.storyboard import storyboard_db_path


DERIVED_SCHEMA = "annot://schema/render-result/v1"


# --- scaffolding -------------------------------------------------------------


def _iv(a: float, b: float) -> TimeInterval:
    return TimeInterval.from_seconds(a, b)


def _authored(proj, *, section_id: str = "s", label: str = "seed") -> UUID:
    return proj.graph.upsert_section(
        SectionBodyV1(section_id=section_id, label=label), interval=_iv(0, 4)
    )


def _derived(
    proj,
    parents,
    *,
    body: dict | None = None,
    tier: str = "render-result",
) -> Annotation:
    """Write a derived annotation through the normal (trace-writing) path."""
    ann = Annotation(
        id=uuid4(),
        tier=tier,
        reference=MediaRef(asset_id=proj.graph.asset_id, interval=_iv(0, 0)),
        body=body if body is not None else {"shot_id": "s01", "url": "x"},
        body_schema_uri=DERIVED_SCHEMA,
        provenance=Provenance(
            was_generated_by="transform:test@1",
            was_attributed_to="agent:test",
            was_derived_from=list(parents),
            generated_at_time=RationalTime.now(),
            activity="derive",
        ),
    )
    proj.graph.add_annotation(ann)
    return ann


def _trace_targets(project_root) -> dict[UUID, UUID]:
    """``{trace_id: target_id}`` for every verifying trace in the project."""
    out: dict[UUID, UUID] = {}
    for ann in nw.iter_all_annotations(project_root):
        if ann.tier == VERIFYING_TRACE_TIER and isinstance(ann.body, dict):
            out[ann.id] = UUID(str(ann.body["for_annotation_id"]))
    return out


def _all_ids(project_root) -> set[UUID]:
    return {a.id for a in nw.iter_all_annotations(project_root)}


def _remove_directly(proj, ann_id: UUID) -> None:
    """A deletion that bypasses nw entirely — what the GC exists for."""
    with nw.open_project_stores(proj.root) as stores:
        for store in stores:
            if store.remove(ann_id) is not None:
                return


def _hand_written_trace(*, asset_id: str, target_id: UUID) -> Annotation:
    """A trace as a foreign producer would write one (no nw code path)."""
    body = VerifyingTraceBodyV1(
        for_annotation_id=str(target_id),
        digest_scheme=VALUE_DIGEST_SCHEME,
        upstream=(),
    )
    return Annotation(
        id=uuid4(),
        tier=VERIFYING_TRACE_TIER,
        reference=MediaRef(asset_id=asset_id, interval=_iv(0, 0)),
        body=body.model_dump(mode="json"),
        body_schema_uri=VERIFYING_TRACE_BODY_SCHEMA_URI,
        provenance=Provenance(
            was_generated_by="agent:test",
            was_attributed_to="agent:test",
            was_derived_from=[],
            generated_at_time=RationalTime.now(),
            activity="record",
        ),
    )


def _chain(tmp_path):
    """Authored A → derived B → derived C; B and C each carry a trace."""
    proj = nw.Project.init(tmp_path / "p")
    a_id = _authored(proj)
    b = _derived(proj, (a_id,), body={"shot_id": "s01", "url": "b"})
    c = _derived(proj, (b.id,), body={"shot_id": "s01", "url": "c"})
    assert set(_trace_targets(proj.root).values()) == {b.id, c.id}
    return proj, a_id, b, c


# --- ProjectGraph.remove_annotation ------------------------------------------


def test_remove_annotation_takes_its_trace_along(tmp_path):
    proj, _a_id, b, c = _chain(tmp_path)

    assert proj.graph.remove_annotation(b.id) is True

    assert b.id not in _all_ids(proj.root)
    # B's trace went with it; C's (which merely *records* B upstream) stays.
    assert set(_trace_targets(proj.root).values()) == {c.id}


def test_remove_annotation_collects_traces_even_for_a_missing_target(tmp_path):
    """An id whose annotation is already gone: False, but the trace still goes."""
    proj, _a_id, b, _c = _chain(tmp_path)
    _remove_directly(proj, b.id)  # annotation gone, trace leaked

    assert b.id in set(_trace_targets(proj.root).values())
    assert proj.graph.remove_annotation(b.id) is False
    assert b.id not in set(_trace_targets(proj.root).values())


# --- write_spec reconciliation -----------------------------------------------


def test_write_spec_reconciliation_collects_traces_of_dropped_entities(tmp_path):
    """The nw#34 hazard, closed: even ``set_title`` reconciles every entity
    tier, and a *derived* annotation at an entity tier carries a trace."""
    proj = nw.Project.init(tmp_path / "p")
    a_id = _authored(proj)
    proposed = _derived(
        proj, (a_id,), tier="shot", body={"shot_id": "proposed", "url": "x"}
    )
    assert set(_trace_targets(proj.root).values()) == {proposed.id}

    # The spec knows no shot "proposed", so any spec write drops it.
    proj.set_title("retitled")

    assert proposed.id not in _all_ids(proj.root)
    assert _trace_targets(proj.root) == {}


# --- storyboard save wipe ----------------------------------------------------


def _storyboard_project(tmp_path) -> nw.Project:
    proj = nw.Project.init(tmp_path / "p")
    proj.upsert_section(SectionSpec(id="v", start_s=0.0, end_s=8.0))
    proj.upsert_shot(
        ShotSpec(
            id="s01",
            start_s=0.0,
            end_s=8.0,
            section_id="v",
            description="Bell tower at moonlight",
        )
    )
    return proj


def test_storyboard_save_wipe_collects_traces_of_wiped_panels(tmp_path):
    proj = _storyboard_project(tmp_path)
    sb, ivs = nw.storyboard_from_shots(proj)
    nw.save_storyboard(proj, sb, panel_intervals=ivs)

    panel_ids = [
        a.id for a in nw.iter_all_annotations(proj.root) if a.tier == "storyboard"
    ]
    assert panel_ids, "seed panels expected"

    # A trace naming a panel, co-located in the storyboard store — as a
    # trace-writing producer of panels would leave it.
    store = open_graph_store(
        storyboard_db_path(proj),
        asset_id=nw.project_asset_id(proj),
        scope=SCOPE_STORYBOARD,
    )
    try:
        store.add_tier(Tier(name=VERIFYING_TRACE_TIER, stereotype=TierStereotype.NONE))
        store.add(
            _hand_written_trace(asset_id=sb.asset_id, target_id=panel_ids[0])
        )
    finally:
        store.close()
    assert panel_ids[0] in set(_trace_targets(proj.root).values())

    # Re-saving wipes the panels; the trace must not survive the wipe.
    nw.save_storyboard(proj, sb, panel_intervals=ivs)
    assert panel_ids[0] not in set(_trace_targets(proj.root).values())


# --- collect_orphan_traces (the backstop) ------------------------------------


def test_collect_orphan_traces_sweeps_exactly_the_orphans(tmp_path):
    proj, _a_id, b, c = _chain(tmp_path)
    _remove_directly(proj, b.id)  # the deletion nw never saw

    before = _trace_targets(proj.root)
    (orphan_trace_id,) = [t for t, target in before.items() if target == b.id]

    removed = nw.collect_orphan_traces(proj.root)

    assert removed == [orphan_trace_id]
    assert set(_trace_targets(proj.root).values()) == {c.id}
    # Idempotent: a second sweep finds nothing.
    assert nw.collect_orphan_traces(proj.root) == []


def test_collect_orphan_traces_resolves_targets_across_stores(tmp_path):
    """A trace in one store whose target lives in *another* is not an orphan."""
    proj = _storyboard_project(tmp_path)
    sb, ivs = nw.storyboard_from_shots(proj)
    nw.save_storyboard(proj, sb, panel_intervals=ivs)
    panel_id = next(
        a.id for a in nw.iter_all_annotations(proj.root) if a.tier == "storyboard"
    )

    # Trace in the *graph* store, target in the *storyboard* store.
    cross_trace = _hand_written_trace(
        asset_id=proj.graph.asset_id, target_id=panel_id
    )
    with proj.graph._open() as store:
        store.add_tier(Tier(name=VERIFYING_TRACE_TIER, stereotype=TierStereotype.NONE))
        store.add(cross_trace)

    assert nw.collect_orphan_traces(proj.root) == []
    assert cross_trace.id in _trace_targets(proj.root)

    _remove_directly(proj, panel_id)
    assert nw.collect_orphan_traces(proj.root) == [cross_trace.id]


def test_collect_orphan_traces_leaves_unreadable_trace_bodies_alone(tmp_path):
    """Deleting what we cannot identify is worse than carrying it."""
    proj = nw.Project.init(tmp_path / "p")
    unreadable = Annotation(
        id=uuid4(),
        tier=VERIFYING_TRACE_TIER,
        reference=MediaRef(asset_id=proj.graph.asset_id, interval=_iv(0, 0)),
        body={"for_annotation_id": "not-a-uuid", "digest_scheme": "x"},
        body_schema_uri=VERIFYING_TRACE_BODY_SCHEMA_URI,
        provenance=Provenance(
            was_generated_by="agent:test",
            was_attributed_to="agent:test",
            was_derived_from=[],
            generated_at_time=RationalTime.now(),
            activity="record",
        ),
    )
    with proj.graph._open() as store:
        store.add_tier(Tier(name=VERIFYING_TRACE_TIER, stereotype=TierStereotype.NONE))
        store.add(unreadable)

    assert nw.collect_orphan_traces(proj.root) == []
    assert unreadable.id in _all_ids(proj.root)
