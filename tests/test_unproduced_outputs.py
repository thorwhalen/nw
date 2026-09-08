"""A blocked/failed output's reason survives a reload (nw#44).

``TransformResult.blocked[i].reason`` / ``.blocked_by`` (nw#25) used to live
only in the in-memory result returned by one ``execute()`` call — reload the
project and the hole was unexplained again. :meth:`nw.ProjectGraph.add_unproduced_output`
persists it as a parentless sidecar (:mod:`nw.bodies.unproduced_output`);
:meth:`nw.ProjectGraph.add_annotation` retires it the moment a real output
with the same ``(transform_name, upstream)`` identity is written — a
successful retry clears its own tombstone.
"""

from __future__ import annotations

from uuid import uuid4

import nw
from lacing import Annotation, MediaRef, Provenance, RationalTime, TimeInterval
from nw.graph import ProjectGraph
from nw.bodies import SectionBodyV1


def _iv(a: float, b: float) -> TimeInterval:
    return TimeInterval.from_seconds(a, b)


def _authored(proj, *, section_id: str = "s") -> "uuid.UUID":  # noqa: F821
    return proj.graph.upsert_section(
        SectionBodyV1(section_id=section_id, label="seed"), interval=_iv(0, 4)
    )


def _skeleton(proj, parents, *, tier="render-result", body=None) -> Annotation:
    """An unproduced skeleton, shaped the way `derive_provenance` shapes one."""
    return Annotation(
        id=uuid4(),
        tier=tier,
        reference=MediaRef(asset_id=proj.graph.asset_id, interval=_iv(0, 0)),
        body=body if body is not None else {"shot_id": "s01", "url": None},
        body_schema_uri="annot://schema/render-result/v1",
        provenance=Provenance(
            was_generated_by="transform:shot_to_render_result.fal.fake@1",
            was_attributed_to="agent:shot_to_render_result.fal.fake",
            was_derived_from=list(parents),
            generated_at_time=RationalTime.now(),
            activity="derive",
        ),
    )


def test_a_blocked_reason_survives_a_reload(tmp_path):
    proj = nw.Project.init(tmp_path / "p")
    parent_id = _authored(proj)
    skel = _skeleton(proj, (parent_id,))

    proj.graph.add_unproduced_output(
        skel,
        transform_name="shot_to_render_result.fal.fake",
        status="blocked",
        reason="upstream panel 47 was filtered",
        blocked_by=(2,),
    )

    # A fresh ProjectGraph object over the same project root — the same
    # thing a process restart gives you.
    reloaded = ProjectGraph(proj.root)
    (record,) = reloaded.unproduced_outputs()
    assert record.body.transform_name == "shot_to_render_result.fal.fake"
    assert record.body.status == "blocked"
    assert record.body.reason == "upstream panel 47 was filtered"
    assert record.body.blocked_by == (2,)
    assert record.body.upstream == (str(parent_id),)


def test_a_failed_reason_carries_its_error_type_and_output_kind(tmp_path):
    proj = nw.Project.init(tmp_path / "p")
    parent_id = _authored(proj)
    skel = _skeleton(proj, (parent_id,))

    proj.graph.add_unproduced_output(
        skel,
        transform_name="t",
        status="failed",
        reason="rate limited",
        error=RuntimeError("rate limited"),
    )

    (record,) = ProjectGraph(proj.root).unproduced_outputs()
    assert record.body.status == "failed"
    assert record.body.error_type == "RuntimeError"
    assert record.body.output_kind == "annot://schema/render-result/v1"


def test_a_successful_retry_retires_its_own_record(tmp_path):
    """The point of the design: a retry that succeeds clears its own hole."""
    proj = nw.Project.init(tmp_path / "p")
    parent_id = _authored(proj)
    skel = _skeleton(proj, (parent_id,))

    # The retirement key is `(transform_name, upstream)` read off the
    # WRITTEN annotation's own provenance, not the string passed to
    # `add_unproduced_output` — so the two must agree, exactly as a real
    # retry (same Transform, same inputs) would produce.
    proj.graph.add_unproduced_output(
        skel,
        transform_name="shot_to_render_result.fal.fake",
        status="failed",
        reason="rate limited",
    )
    assert len(proj.graph.unproduced_outputs()) == 1

    # A retry with the SAME (transform, upstream) identity succeeds.
    completed = skel.model_copy(update={"body": {**skel.body, "url": "x"}})
    proj.graph.add_annotation(completed)

    assert ProjectGraph(proj.root).unproduced_outputs() == []


def test_a_different_units_record_is_not_retired(tmp_path):
    """Retirement is scoped to (transform_name, upstream) — not "any success"."""
    proj = nw.Project.init(tmp_path / "p")
    parent_a = _authored(proj, section_id="a")
    parent_b = _authored(proj, section_id="b")
    skel_a = _skeleton(proj, (parent_a,))
    skel_b = _skeleton(proj, (parent_b,))

    proj.graph.add_unproduced_output(
        skel_a,
        transform_name="shot_to_render_result.fal.fake",
        status="failed",
        reason="boom",
    )
    proj.graph.add_unproduced_output(
        skel_b,
        transform_name="shot_to_render_result.fal.fake",
        status="failed",
        reason="boom too",
    )

    # Only skel_a's unit succeeds.
    proj.graph.add_annotation(
        skel_a.model_copy(update={"body": {**skel_a.body, "url": "x"}})
    )

    remaining = ProjectGraph(proj.root).unproduced_outputs()
    assert [r.body.upstream for r in remaining] == [(str(parent_b),)]


def test_unproduced_outputs_filters_by_transform_name(tmp_path):
    proj = nw.Project.init(tmp_path / "p")
    parent_id = _authored(proj)
    proj.graph.add_unproduced_output(
        _skeleton(proj, (parent_id,)), transform_name="t1", status="failed"
    )
    proj.graph.add_unproduced_output(
        _skeleton(proj, (parent_id,)), transform_name="t2", status="failed"
    )

    assert len(proj.graph.unproduced_outputs()) == 2
    assert len(proj.graph.unproduced_outputs(transform_name="t1")) == 1


def test_unproduced_outputs_are_never_stale_candidates(tmp_path):
    """Parentless: excluded from freshness's descendants walk by construction."""
    proj = nw.Project.init(tmp_path / "p")
    parent_id = _authored(proj)
    proj.graph.add_unproduced_output(
        _skeleton(proj, (parent_id,)), transform_name="t", status="failed"
    )

    (record,) = proj.graph.unproduced_outputs()
    assert nw.descendants_of(proj.root, parent_id) == []
    assert nw.descendants_of(proj.root, record.annotation_id) == []


def test_unrelated_annotations_do_not_retire_anything(tmp_path):
    """A write with no `transform:` provenance must not be mistaken for a
    retiring output — only `add_annotation` writes coming from a Transform
    (``was_generated_by == "transform:<name>@<version>"``) retire anything."""
    proj = nw.Project.init(tmp_path / "p")
    parent_id = _authored(proj)
    proj.graph.add_unproduced_output(
        _skeleton(proj, (parent_id,)), transform_name="t", status="failed"
    )

    non_transform = Annotation(
        id=uuid4(),
        tier="decision",
        reference=MediaRef(asset_id=proj.graph.asset_id, interval=_iv(0, 0)),
        body={"kind": "note", "payload": {}},
        body_schema_uri="annot://schema/decision/v1",
        provenance=Provenance(
            was_generated_by="agent:nw.graph",
            was_attributed_to="user:nw",
            was_derived_from=[parent_id],
            generated_at_time=RationalTime.now(),
            activity="create",
        ),
    )
    proj.graph.add_annotation(non_transform)

    assert len(proj.graph.unproduced_outputs()) == 1
