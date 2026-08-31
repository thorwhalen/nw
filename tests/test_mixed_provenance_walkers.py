"""Every walker survives a mixed ``was_derived_from`` — the nw#55 audit, pinned.

Since lacing#14, ``Provenance.was_derived_from`` is ``list[ProvenanceRef]``
where ``ProvenanceRef = UUID | AssetId`` (64-hex, format-disjoint from a
UUID). Nothing in nw WRITES asset refs yet, but they can arrive — through
raw store writes, foreign producers, or the day nw grows the edge-writer —
and nw#55's audit question was whether the annotation-tier walkers crash,
lie, or degrade safely on one. Measured answer: they degrade safely, and
each safe behaviour is pinned here so a refactor that starts crashing on a
64-hex parent (or silently treating it as a resolvable annotation) goes red.

The audited-safe behaviours:

- reachability (``descendants_of`` / ``derived_from`` / the children index)
  IGNORES asset-id parents — they name artifacts, not annotations;
- freshness reads a mixed-parent annotation as stale (``no-trace``: the
  trace chokepoint declines a trace it cannot complete) — over-reporting,
  the direction the module documents as safe;
- ``backfill_traces`` skips it with its nw#55 reason rather than blessing a
  partial trace.
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


def test_the_chokepoint_declines_a_trace_it_cannot_complete(tmp_path):
    """add_annotation on a mixed-parent annotation persists it TRACE-LESS —
    a partial trace (UUID parents only) would read fresh while an artifact
    input changed, which is the unsafe direction."""
    proj, a_id, mixed, child = _project_with_mixed_parentage(tmp_path)
    from nw.bodies import VERIFYING_TRACE_TIER

    trace_targets = {
        str(a.body.get("for_annotation_id"))
        for a in nw.iter_all_annotations(proj.root)
        if a.tier == VERIFYING_TRACE_TIER and isinstance(a.body, dict)
    }
    assert str(mixed.id) not in trace_targets


def test_backfill_skips_mixed_parentage_with_its_reason(tmp_path):
    proj, a_id, mixed, child = _project_with_mixed_parentage(tmp_path)
    report = nw.backfill_traces(proj.root, execute=True)
    skipped = {s["annotation_id"]: s["reason"] for s in report["skipped"]}
    assert str(mixed.id) in skipped
    assert "artifact refs" in skipped[str(mixed.id)]
