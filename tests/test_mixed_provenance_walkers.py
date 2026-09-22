"""Every walker survives a mixed ``was_derived_from`` — the nw#55 audit, pinned.

Since lacing#14, ``Provenance.was_derived_from`` is ``list[ProvenanceRef]``
where ``ProvenanceRef = UUID | AssetId`` (64-hex, format-disjoint from a
UUID). nw writes them itself since nw#55 (``derive_provenance`` reads each
input's declared asset fields, :mod:`nw.transforms.asset_refs`); they also
arrive through raw store writes and foreign producers. The audit question was
whether the annotation-tier walkers crash, lie, or degrade safely on one;
each behaviour is pinned here so a refactor that starts crashing on a 64-hex
parent (or silently treating it as a resolvable annotation) goes red.

The audited-safe behaviours:

- reachability (``descendants_of`` / ``derived_from`` / the children index)
  IGNORES asset-id parents — they name artifacts, not annotations;
- a mixed-parent annotation written RAW (bypassing the chokepoint) has no
  trace and reads ``no-trace`` stale — over-reporting, the safe direction;
- through the chokepoint it gets a trace whose ``upstream_assets`` records
  the artifact parents verbatim, and reads fresh; a change to an annotation
  parent still reads ``upstream-changed`` (nw#55);
- ``backfill_traces`` blesses it the same way (until nw#55 it was skipped).
"""

from __future__ import annotations

from uuid import uuid4

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
from nw.bodies import SectionBodyV1


ASSET_REF = "c" * 64  # a lacing AssetId — 64-hex, never a UUID


def _project_with_mixed_parentage(tmp_path):
    proj = nw.Project.init(tmp_path / "p")
    a_id = proj.graph.upsert_section(
        SectionBodyV1(section_id="s", label="x"),
        interval=TimeInterval.from_seconds(0, 4),
    )

    def _derived(parents, url):
        ann = Annotation(
            id=uuid4(),
            tier="render-result",
            reference=MediaRef(
                asset_id=proj.graph.asset_id,
                interval=TimeInterval.from_seconds(0, 0),
            ),
            body={"url": url},
            body_schema_uri="annot://schema/render-result/v1",
            provenance=Provenance(
                was_generated_by="transform:test@1",
                was_attributed_to="agent:test",
                was_derived_from=list(parents),
                generated_at_time=RationalTime.now(),
                activity="derive",
            ),
        )
        with proj.graph._open() as store:
            store.add_tier(
                Tier(name="render-result", stereotype=TierStereotype.NONE)
            )
            store.add(ann)
        return ann

    mixed = _derived([a_id, ASSET_REF], "mixed")
    child = _derived([mixed.id], "child")
    return proj, a_id, mixed, child


def test_reachability_ignores_asset_refs_without_crashing(tmp_path):
    proj, a_id, mixed, child = _project_with_mixed_parentage(tmp_path)
    downstream = {a.id for a in nw.descendants_of(proj.root, a_id)}
    assert downstream == {mixed.id, child.id}
    # The asset ref is not resolved as a parent annotation — it names an
    # artifact, and pretending otherwise would be a lie, not a feature.
    parents = nw.derived_from(proj.root, mixed.id)
    assert [p.id for p in parents] == [a_id]


def test_freshness_reads_mixed_parentage_as_stale_never_crashes(tmp_path):
    proj, a_id, mixed, child = _project_with_mixed_parentage(tmp_path)
    verdicts = {v.annotation.id: v for v in nw.freshness.stale_verdicts_all(proj.root)}
    assert verdicts[mixed.id].is_stale
    assert verdicts[mixed.id].reason == "no-trace"
    # Over-reporting is the safe direction; silence or a crash is not.
    assert verdicts[child.id].is_stale


def test_a_raw_write_bypasses_the_chokepoint_and_gets_no_trace(tmp_path):
    """The fixture writes with ``store.add``, not ``add_annotation`` — the
    documented way to end up stale forever. No trace is fabricated for it."""
    proj, a_id, mixed, child = _project_with_mixed_parentage(tmp_path)
    from nw.bodies import VERIFYING_TRACE_TIER

    trace_targets = {
        str(a.body.get("for_annotation_id"))
        for a in nw.iter_all_annotations(proj.root)
        if a.tier == VERIFYING_TRACE_TIER and isinstance(a.body, dict)
    }
    assert str(mixed.id) not in trace_targets


def test_backfill_blesses_mixed_parentage_recording_the_assets(tmp_path):
    proj, a_id, mixed, child = _project_with_mixed_parentage(tmp_path)
    report = nw.backfill_traces(proj.root, execute=True)
    assert str(mixed.id) not in {s["annotation_id"] for s in report["skipped"]}
    verdicts = {v.annotation.id: v for v in nw.freshness.stale_verdicts_all(proj.root)}
    assert not verdicts[mixed.id].is_stale
    assert not verdicts[child.id].is_stale


def _add_through_chokepoint(proj, parents, url):
    ann = Annotation(
        id=uuid4(),
        tier="render-result",
        reference=MediaRef(
            asset_id=proj.graph.asset_id, interval=TimeInterval.from_seconds(0, 0)
        ),
        body={"url": url},
        body_schema_uri="annot://schema/render-result/v1",
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


def _trace_body(proj, target_id):
    from nw.bodies import VERIFYING_TRACE_TIER

    (body,) = [
        a.body
        for a in nw.iter_all_annotations(proj.root)
        if a.tier == VERIFYING_TRACE_TIER
        and isinstance(a.body, dict)
        and a.body.get("for_annotation_id") == str(target_id)
    ]
    return body


def test_the_chokepoint_traces_mixed_parentage_and_it_reads_fresh(tmp_path):
    proj = nw.Project.init(tmp_path / "p")
    a_id = proj.graph.upsert_section(
        SectionBodyV1(section_id="s", label="x"),
        interval=TimeInterval.from_seconds(0, 4),
    )
    mixed = _add_through_chokepoint(proj, [a_id, ASSET_REF], "m")

    body = _trace_body(proj, mixed.id)
    assert body["upstream_assets"] == [ASSET_REF]
    assert [u["annotation_id"] for u in body["upstream"]] == [str(a_id)]
    verdicts = {v.annotation.id: v for v in nw.freshness.stale_verdicts_all(proj.root)}
    assert not verdicts[mixed.id].is_stale

    # Early cutoff still works on the annotation half: change the parent.
    proj.graph.upsert_section(
        SectionBodyV1(section_id="s", label="changed"),
        interval=TimeInterval.from_seconds(0, 4),
    )
    verdicts = {v.annotation.id: v for v in nw.freshness.stale_verdicts_all(proj.root)}
    assert verdicts[mixed.id].is_stale
    assert verdicts[mixed.id].reason == "upstream-changed"


def test_an_asset_only_parentage_is_traced_and_fresh(tmp_path):
    proj = nw.Project.init(tmp_path / "p")
    only = _add_through_chokepoint(proj, [ASSET_REF], "o")
    body = _trace_body(proj, only.id)
    assert body["upstream"] == [] and body["upstream_assets"] == [ASSET_REF]
    verdicts = {v.annotation.id: v for v in nw.freshness.stale_verdicts_all(proj.root)}
    assert not verdicts[only.id].is_stale


def test_a_trace_with_no_artifact_parents_is_byte_identical_to_before(tmp_path):
    """No ``upstream_assets`` key at all when there are none — so an nw that
    predates the field (``extra="forbid"``) still reads every ordinary trace."""
    proj = nw.Project.init(tmp_path / "p")
    a_id = proj.graph.upsert_section(
        SectionBodyV1(section_id="s", label="x"),
        interval=TimeInterval.from_seconds(0, 4),
    )
    plain = _add_through_chokepoint(proj, [a_id], "p")
    assert "upstream_assets" not in _trace_body(proj, plain.id)
