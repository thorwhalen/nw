"""``nw.backfill_traces`` — blessing a pre-trace project (nw#58).

On a project whose annotations predate nw#24's trace-writing, the
verifying-trace rule is a behavior change: every derived annotation reads
stale (``no-trace``), regen skips non-Transform-produced ones forever, and
nothing heals them. The backfill writes each one a trace against its
parents' CURRENT digests — the old timestamp rule's own verdict for
at-rest data — so the swap to trace-based freshness is
semantics-preserving at the moment of migration.

The properties under test are the ones the deploy owner runs against a
paying user's projects: report-only by default (the first run is a READ),
idempotent (a partial run is re-run, not reasoned about), and honest about
what it will not bless (a missing parent stays upstream-missing — a
fabricated trace would hide a real hole).
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
from nw.bodies import SectionBodyV1, VERIFYING_TRACE_TIER


DERIVED_SCHEMA = "annot://schema/render-result/v1"


def _iv(a: float, b: float) -> TimeInterval:
    return TimeInterval.from_seconds(a, b)


def _authored(proj, *, section_id: str = "s", label: str = "seed") -> UUID:
    return proj.graph.upsert_section(
        SectionBodyV1(section_id=section_id, label=label), interval=_iv(0, 4)
    )


def _legacy_derived(proj, parents, *, body: dict | None = None) -> Annotation:
    """A derived annotation written the pre-nw#24 way: raw store.add, NO trace.

    This is the population the backfill exists for — every live project's
    history was written like this.
    """
    ann = Annotation(
        id=uuid4(),
        tier="render-result",
        reference=MediaRef(asset_id=proj.graph.asset_id, interval=_iv(0, 0)),
        body=body if body is not None else {"shot_id": "s01", "url": "x"},
        body_schema_uri=DERIVED_SCHEMA,
        provenance=Provenance(
            was_generated_by="transform:legacy@1",
            was_attributed_to="agent:legacy",
            was_derived_from=list(parents),
            generated_at_time=RationalTime.now(),
            activity="derive",
        ),
    )
    with proj.graph._open() as store:
        store.add_tier(Tier(name="render-result", stereotype=TierStereotype.NONE))
        store.add(ann)
    return ann


def _traced_targets(project_root) -> set[UUID]:
    return {
        UUID(str(a.body["for_annotation_id"]))
        for a in nw.iter_all_annotations(project_root)
        if a.tier == VERIFYING_TRACE_TIER and isinstance(a.body, dict)
    }


def _stale_ids(project_root) -> set[UUID]:
    return {a.id for a in nw.all_stale(project_root)}


def _legacy_chain(tmp_path):
    """Authored A → legacy-derived B → legacy-derived C. No traces anywhere."""
    proj = nw.Project.init(tmp_path / "p")
    a_id = _authored(proj)
    b = _legacy_derived(proj, (a_id,), body={"shot_id": "s01", "url": "b"})
    c = _legacy_derived(proj, (b.id,), body={"shot_id": "s01", "url": "c"})
    assert _traced_targets(proj.root) == set()
    return proj, a_id, b, c


def test_default_is_a_read_that_reports_what_it_would_do(tmp_path):
    proj, a_id, b, c = _legacy_chain(tmp_path)
    # The hazard the backfill exists for, demonstrated: the whole legacy
    # chain reads stale under the verifying-trace rule.
    assert _stale_ids(proj.root) == {b.id, c.id}

    report = nw.backfill_traces(proj.root)
    assert report["executed"] is False
    assert report["backfilled"] == 2
    assert report["already_traced"] == 0
    assert report["parentless"] == 1  # the authored section
    assert report["skipped"] == []
    assert report["examined"] == 3
    # A read: nothing was written, the project still reads fully stale.
    assert _traced_targets(proj.root) == set()
    assert _stale_ids(proj.root) == {b.id, c.id}


def test_execute_blesses_at_rest_state_as_fresh(tmp_path):
    proj, a_id, b, c = _legacy_chain(tmp_path)

    report = nw.backfill_traces(proj.root, execute=True)
    assert report["executed"] is True
    assert report["backfilled"] == 2
    assert _traced_targets(proj.root) == {b.id, c.id}
    # The point of the whole exercise: at-rest data now reads fresh — the
    # same verdict the old timestamp rule gave it.
    assert _stale_ids(proj.root) == set()


def test_idempotent_a_second_run_writes_nothing(tmp_path):
    proj, *_ = _legacy_chain(tmp_path)
    nw.backfill_traces(proj.root, execute=True)

    again = nw.backfill_traces(proj.root, execute=True)
    assert again["backfilled"] == 0
    assert again["already_traced"] == 2
    # Still exactly one trace per target — never rewritten.
    assert len(_traced_targets(proj.root)) == 2


def test_a_missing_parent_is_skipped_not_fabricated(tmp_path):
    proj = nw.Project.init(tmp_path / "p")
    ghost = uuid4()
    d = _legacy_derived(proj, (ghost,), body={"shot_id": "s01", "url": "d"})

    report = nw.backfill_traces(proj.root, execute=True)
    assert report["backfilled"] == 0
    assert [s["annotation_id"] for s in report["skipped"]] == [str(d.id)]
    assert "missing" in report["skipped"][0]["reason"]
    # The annotation stays honestly stale (upstream-missing), because the
    # hole is real and a fabricated trace would hide it.
    assert d.id in _stale_ids(proj.root)


def test_artifact_refs_are_skipped_with_their_reason(tmp_path):
    proj = nw.Project.init(tmp_path / "p")
    a_id = _authored(proj)
    mixed = _legacy_derived(proj, (a_id, "c" * 64), body={"url": "m"})

    report = nw.backfill_traces(proj.root, execute=True)
    assert report["backfilled"] == 0
    assert [s["annotation_id"] for s in report["skipped"]] == [str(mixed.id)]
    assert "artifact refs" in report["skipped"][0]["reason"]
    assert _traced_targets(proj.root) == set()


def test_annotations_written_through_the_chokepoint_are_left_alone(tmp_path):
    """The normal path already wrote a correct trace; the backfill must
    count it and never write a second."""
    proj = nw.Project.init(tmp_path / "p")
    a_id = _authored(proj)
    ann = Annotation(
        id=uuid4(),
        tier="render-result",
        reference=MediaRef(asset_id=proj.graph.asset_id, interval=_iv(0, 0)),
        body={"shot_id": "s01", "url": "modern"},
        body_schema_uri=DERIVED_SCHEMA,
        provenance=Provenance(
            was_generated_by="transform:modern@1",
            was_attributed_to="agent:modern",
            was_derived_from=[a_id],
            generated_at_time=RationalTime.now(),
            activity="derive",
        ),
    )
    proj.graph.add_annotation(ann)  # writes its own trace
    before = _traced_targets(proj.root)
    assert before == {ann.id}

    report = nw.backfill_traces(proj.root, execute=True)
    assert report["already_traced"] == 1
    assert report["backfilled"] == 0
    assert _traced_targets(proj.root) == before


def test_a_parent_edited_after_the_derive_is_not_blessed(tmp_path):
    """The old timestamp rule read this STALE (regeneration pending); blessing
    it would silently clear a real signal. It stays no-trace-stale — the same
    verdict — and lands in skipped where the operator can see it."""
    proj = nw.Project.init(tmp_path / "p")
    a_id = _authored(proj)  # authored NOW
    stale_child = Annotation(
        id=uuid4(),
        tier="render-result",
        reference=MediaRef(asset_id=proj.graph.asset_id, interval=_iv(0, 0)),
        body={"shot_id": "s01", "url": "old"},
        body_schema_uri=DERIVED_SCHEMA,
        provenance=Provenance(
            was_generated_by="transform:legacy@1",
            was_attributed_to="agent:legacy",
            was_derived_from=[a_id],
            # Derived at EPOCH — i.e. before its parent's edit stamp.
            generated_at_time=RationalTime.zero(),
            activity="derive",
        ),
    )
    with proj.graph._open() as store:
        store.add_tier(Tier(name="render-result", stereotype=TierStereotype.NONE))
        store.add(stale_child)

    report = nw.backfill_traces(proj.root, execute=True)
    assert report["backfilled"] == 0
    assert [s["annotation_id"] for s in report["skipped"]] == [str(stale_child.id)]
    assert "edited after" in report["skipped"][0]["reason"]
    assert stale_child.id in _stale_ids(proj.root)


def test_an_unusable_trace_is_reported_not_counted_healthy(tmp_path):
    """A trace under a foreign digest scheme reads stale forever
    (digest-scheme-changed). Counting it as already_traced would hand the
    operator an instrument that reads 'healthy' over a row that is not."""
    from lacing.digest import VALUE_DIGEST_SCHEME
    from nw.bodies import (
        VERIFYING_TRACE_BODY_SCHEMA_URI,
        VerifyingTraceBodyV1,
    )
    from nw.bodies.verifying_trace import UpstreamDigestV1

    proj = nw.Project.init(tmp_path / "p")
    a_id = _authored(proj)
    child = _legacy_derived(proj, (a_id,), body={"url": "c"})
    foreign = Annotation(
        id=uuid4(),
        tier=VERIFYING_TRACE_TIER,
        reference=MediaRef(asset_id=proj.graph.asset_id, interval=_iv(0, 0)),
        body=VerifyingTraceBodyV1(
            for_annotation_id=str(child.id),
            digest_scheme="ancient-scheme/v0",
            upstream=(
                UpstreamDigestV1(annotation_id=str(a_id), value_digest="0" * 64),
            ),
        ).model_dump(mode="json"),
        body_schema_uri=VERIFYING_TRACE_BODY_SCHEMA_URI,
        provenance=Provenance(
            was_generated_by="agent:foreign",
            was_attributed_to="agent:foreign",
            was_derived_from=[],
            generated_at_time=RationalTime.now(),
            activity="record",
        ),
    )
    with proj.graph._open() as store:
        store.add_tier(
            Tier(name=VERIFYING_TRACE_TIER, stereotype=TierStereotype.NONE)
        )
        store.add(foreign)

    report = nw.backfill_traces(proj.root, execute=True)
    assert report["traced_unusable"] == 1
    assert report["already_traced"] == 0
    assert report["backfilled"] == 0
    # Deliberately not overwritten — and therefore still stale.
    assert child.id in _stale_ids(proj.root)
    assert VALUE_DIGEST_SCHEME != "ancient-scheme/v0"


def test_stores_found_distinguishes_not_a_project_from_nothing_to_do(tmp_path):
    """A typo'd root reports all-zero counts; stores_found == 0 is what keeps
    that from reading as 'migrated clean' in an operator's loop."""
    empty = tmp_path / "not-a-project"
    empty.mkdir()
    report = nw.backfill_traces(empty)
    assert report["stores_found"] == 0
    assert report["examined"] == 0

    proj = nw.Project.init(tmp_path / "real")
    _authored(proj)
    assert nw.backfill_traces(proj.root)["stores_found"] >= 1
