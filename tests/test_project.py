"""Tests for nw.Project — bootstrap, read/write, summary, character anchors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nw import (
    CharacterRef,
    EnvironmentRef,
    Project,
    ProjectSpec,
    SectionSpec,
    ShotSpec,
    SongInfo,
    descendants_of,
)
from nw.bodies import (
    VERIFYING_TRACE_TIER,
    CharacterRefBodyV1,
    DecisionBodyV1,
    EnvironmentRefBodyV1,
)
from nw.graph import iter_all_annotations
from nw.project import _CHARACTER_REF_FIELDS, _ENVIRONMENT_REF_FIELDS


def test_init_creates_project_with_subfolders(tmp_path):
    proj = Project.init(tmp_path / "p1", title="My Project")
    assert proj.project_file.exists()
    for sub in (
        "characters",
        "environments",
        "shots",
        "output",
        "lyrics",
        "script",
        "song",
        ".nw",
    ):
        assert (proj.root / sub).is_dir(), f"missing subfolder: {sub}"

    spec = proj.read_spec()
    assert spec.title == "My Project"
    assert spec.schema_version == 1


def test_init_refuses_existing_project(tmp_path):
    Project.init(tmp_path / "p", title="One")
    with pytest.raises(FileExistsError):
        Project.init(tmp_path / "p", title="Two")


def test_init_force_overwrites(tmp_path):
    Project.init(tmp_path / "p", title="One")
    proj = Project.init(tmp_path / "p", title="Two", force=True)
    # title gets overwritten only if a new spec was written; with force=True
    # we re-init and write a fresh spec.
    assert proj.read_spec().title == "Two"


def test_load_existing_project(tmp_path):
    src = tmp_path / "p1"
    Project.init(src, title="Loaded")
    proj = Project(src)
    assert proj.read_spec().title == "Loaded"


def test_load_nonexistent_project_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        Project(tmp_path / "nope")


def test_set_title_and_global_style(tmp_path):
    proj = Project.init(tmp_path / "p", title="Original")
    proj.set_title("Renamed")
    proj.set_global_style("noir, candlelight, deep blues")

    spec = proj.read_spec()
    assert spec.title == "Renamed"
    assert spec.global_style == "noir, candlelight, deep blues"


# --- characters --------------------------------------------------------------


def test_add_character_creates_dir_and_card(tmp_path):
    proj = Project.init(tmp_path / "p")
    proj.add_character("thor", description="The narrator")

    assert (proj.character_dir("thor") / "card.json").exists()
    assert (proj.character_dir("thor") / "refs").is_dir()
    assert (proj.character_dir("thor") / "selected").is_dir()

    spec = proj.read_spec()
    assert any(c.name == "thor" for c in spec.characters)


def test_add_character_idempotent(tmp_path):
    proj = Project.init(tmp_path / "p")
    proj.add_character("thor", description="v1")
    proj.add_character("thor", description="v2")

    spec = proj.read_spec()
    assert len([c for c in spec.characters if c.name == "thor"]) == 1
    assert spec.character("thor").description == "v2"


def test_list_character_images_separates_refs_from_selected(tmp_path):
    proj = Project.init(tmp_path / "p")
    proj.add_character("thor")

    # Create one ref and two selected images.
    refs_dir = proj.character_dir("thor") / "refs"
    sel_dir = proj.character_dir("thor") / "selected"
    (refs_dir / "ref_001.png").write_bytes(b"PNG")
    (sel_dir / "sel_001.jpg").write_bytes(b"JPG")
    (sel_dir / "sel_002.png").write_bytes(b"PNG")
    # Non-image file should be ignored.
    (refs_dir / "notes.txt").write_text("ignore me")

    images = proj.list_character_images("thor")
    assert len(images) == 3
    assert sum(i.from_ref for i in images) == 1
    assert sum(i.from_selected for i in images) == 2


def test_set_character_anchor_updates_card_and_marks_anchor(tmp_path):
    proj = Project.init(tmp_path / "p")
    proj.add_character("thor")
    sel_dir = proj.character_dir("thor") / "selected"
    img1 = sel_dir / "sel_001.jpg"
    img1.write_bytes(b"JPG")

    card = proj.set_character_anchor("thor", img1)
    assert card["reference_image_path"] == "characters/thor/selected/sel_001.jpg"

    images = proj.list_character_images("thor")
    anchors = [i for i in images if i.is_anchor]
    assert len(anchors) == 1
    assert anchors[0].path == img1


def test_set_character_anchor_refuses_out_of_character_image(tmp_path):
    proj = Project.init(tmp_path / "p")
    proj.add_character("thor")
    proj.add_character("aria")
    aria_img = proj.character_dir("aria") / "selected" / "a.png"
    aria_img.parent.mkdir(parents=True, exist_ok=True)
    aria_img.write_bytes(b"PNG")

    with pytest.raises(ValueError):
        proj.set_character_anchor("thor", aria_img)


# --- environments ------------------------------------------------------------


def test_add_environment(tmp_path):
    proj = Project.init(tmp_path / "p")
    proj.add_environment("bell_tower", description="Gothic, frosted")
    assert proj.environment_dir("bell_tower").is_dir()
    assert proj.read_spec().environment("bell_tower").description == "Gothic, frosted"


# --- shots / sections --------------------------------------------------------


def test_upsert_shot_persists_and_orders_by_start_s(tmp_path):
    proj = Project.init(tmp_path / "p")
    proj.upsert_shot(ShotSpec(id="s02", start_s=5.0, end_s=8.0))
    proj.upsert_shot(ShotSpec(id="s01", start_s=0.0, end_s=5.0))

    spec = proj.read_spec()
    assert [s.id for s in spec.shots] == ["s01", "s02"]
    # shot.json written per-shot too:
    assert (proj.shot_dir("s01") / "shot.json").exists()


def test_upsert_section(tmp_path):
    proj = Project.init(tmp_path / "p")
    proj.upsert_section(SectionSpec(id="verse", start_s=0.0, end_s=8.0, label="verse"))
    spec = proj.read_spec()
    assert spec.section("verse").label == "verse"


# --- summary -----------------------------------------------------------------


def test_read_summary_counts_and_stages(tmp_path):
    proj = Project.init(tmp_path / "p", title="Sum")
    proj.add_character("thor")
    proj.upsert_section(SectionSpec(id="verse", start_s=0, end_s=10, label="verse"))
    proj.upsert_shot(ShotSpec(id="s01", start_s=0, end_s=5))
    proj.upsert_shot(ShotSpec(id="s02", start_s=5, end_s=10))

    summary = proj.read_summary()
    assert summary.title == "Sum"
    assert summary.character_count == 1
    assert summary.section_count == 1
    assert summary.shot_count == 2
    assert summary.rendered_shot_count == 0
    assert "characters" in summary.stages_done
    assert "shots" in summary.stages_done
    assert "rendered:" not in " ".join(summary.stages_done)


def test_read_summary_marks_rendered_when_output_exists(tmp_path):
    proj = Project.init(tmp_path / "p")
    proj.upsert_shot(ShotSpec(id="s01", start_s=0, end_s=5))
    # Create the rendered output marker:
    (proj.shot_dir("s01") / "output.mp4").write_bytes(b"mp4")
    summary = proj.read_summary()
    assert summary.rendered_shot_count == 1
    assert any("rendered:" in s for s in summary.stages_done)


# --- decisions log -----------------------------------------------------------


def test_log_decision_appends_one_jsonl(tmp_path):
    proj = Project.init(tmp_path / "p")
    proj.log_decision("test_event", a=1, b="two")
    proj.log_decision("test_event", a=2)

    path = proj.root / ".nw" / "decisions.jsonl"
    lines = [json.loads(l) for l in path.read_text().splitlines()]
    assert len(lines) == 2
    assert lines[0]["a"] == 1
    assert lines[1]["a"] == 2


# --- spec equality / round-trip ---------------------------------------------


def test_spec_round_trips_through_disk(tmp_path):
    """Write a fully-populated spec, re-read it, equality must hold."""
    proj = Project.init(tmp_path / "p", title="rt")
    spec_in = ProjectSpec(
        title="rt",
        song=SongInfo(
            audio_path="song/a.wav", duration_s=8.0, sample_rate=48000, bitrate=1500000
        ),
        characters=(CharacterRef(name="thor", description="x"),),
        environments=(EnvironmentRef(name="env", description="y"),),
        sections=(SectionSpec(id="verse", start_s=0, end_s=8, label="verse"),),
        shots=(ShotSpec(id="s01", start_s=0, end_s=8, render_strategy="lipsync"),),
        global_style="noir",
        notes="hi",
    )
    proj.write_spec(spec_in)
    spec_out = proj.read_spec()
    assert spec_out == spec_in


# --- character stable attributes survive spec round-trips (nw#5) -------------


def _enriched(name: str = "thor") -> CharacterRef:
    return CharacterRef(
        name=name,
        description="the narrator",
        reference_image_urls=("https://example.invalid/thor.png",),
        costume="grey tweed jacket, green flat cap",
        age="late 50s",
        default_setting="frozen_belltower",
        distinguishing_features=("left eye scar",),
        palette_anchors=("#8899aa",),
        do_not_do=("no shamrocks",),
    )


def test_character_stable_attributes_round_trip_through_the_graph(tmp_path):
    proj = Project.init(tmp_path / "p", title="p")
    proj.update_spec(characters=(_enriched(),))

    got = Project(proj.root).read_spec().character("thor")
    assert got == _enriched()


def test_unrelated_spec_update_does_not_erase_character_attributes(tmp_path):
    """The regression this guards: write_spec rebuilds every character-ref body
    from the spec-level CharacterRef, so a field that exists only on the body is
    wiped by the next update_spec — however unrelated that update is."""
    proj = Project.init(tmp_path / "p", title="p")
    proj.update_spec(characters=(_enriched(),))

    proj.set_title("a completely unrelated change")

    got = Project(proj.root).read_spec().character("thor")
    assert got.costume == "grey tweed jacket, green flat cap"
    assert got.do_not_do == ("no shamrocks",)
    assert got.palette_anchors == ("#8899aa",)
    assert got.reference_image_urls == ("https://example.invalid/thor.png",)


def test_unrelated_spec_update_does_not_erase_environment_attributes(tmp_path):
    """The same data loss, on the environment tier.

    ``reference_image_urls`` is the lookbook the FE curates for a *location*;
    it was erased by every ``update_spec`` for as long as it existed, because
    ``EnvironmentRef`` did not mirror ``EnvironmentRefBodyV1``.
    """
    proj = Project.init(tmp_path / "p", title="p")
    env = EnvironmentRef(
        name="belltower",
        description="gothic, frosted",
        reference_image_urls=("https://example.invalid/belltower.png",),
    )
    proj.update_spec(environments=(env,))

    proj.set_title("a completely unrelated change")

    assert Project(proj.root).read_spec().environment("belltower") == env


@pytest.mark.parametrize(
    "body_model, spec_model, fields",
    [
        (CharacterRefBodyV1, CharacterRef, _CHARACTER_REF_FIELDS),
        (EnvironmentRefBodyV1, EnvironmentRef, _ENVIRONMENT_REF_FIELDS),
    ],
    ids=["character", "environment"],
)
def test_entity_body_and_spec_type_mirror_each_other(body_model, spec_model, fields):
    """Structural guard against the *recurrence* of the erasure bug.

    A developer adds a field to the body first. Without this assertion the
    suite stays green and the new field is silently erased by the next
    ``update_spec`` — coverage by enumeration only catches fields some test
    happens to name.
    """
    assert set(body_model.model_fields) == set(spec_model.model_fields) == set(fields)
    for name in fields:
        assert (
            body_model.model_fields[name].annotation
            == spec_model.model_fields[name].annotation
        ), f"{name!r} differs in type between the body and the spec type"


def test_re_adding_a_character_updates_description_only(tmp_path):
    proj = Project.init(tmp_path / "p", title="p")
    proj.update_spec(characters=(_enriched(),))

    proj.add_character("thor", description="revised")

    got = Project(proj.root).read_spec().character("thor")
    assert got.description == "revised"
    assert got.costume == "grey tweed jacket, green flat cap"
    assert got.do_not_do == ("no shamrocks",)


# --- resumption brief (nw#7) -------------------------------------------------


def test_resumption_brief_on_empty_project_is_benign(tmp_path):
    brief = Project.init(tmp_path / "p", title="p").resumption_brief()

    assert brief.title == "p"
    assert brief.recent_decisions == ()
    assert brief.downstream_of_last_authored_change == ()
    assert brief.downstream_count == 0
    assert brief.last_authored_change_id is None
    assert brief.last_session_at is None
    assert brief.gap_seconds is None
    assert brief.total_spend_usd == 0.0
    assert brief.caveats == ()
    assert brief.suggested_next == (
        "Empty project — register a song or import a script to start.",
    )


def _seeded(tmp_path):
    proj = Project.init(tmp_path / "p", title="p")
    proj.update_spec(
        sections=(SectionSpec(id="verse", start_s=0.0, end_s=8.0, label="verse"),),
        shots=(
            ShotSpec(id="s01", start_s=0.0, end_s=4.0, section_id="verse"),
            ShotSpec(id="s02", start_s=4.0, end_s=8.0, section_id="verse"),
        ),
    )
    return proj


def test_resumption_brief_reports_recent_decisions_newest_last(tmp_path):
    proj = _seeded(tmp_path)
    proj.log_decision("first_thing", note="a")
    proj.log_decision("second_thing", note="b")

    brief = proj.resumption_brief()
    kinds = [d.kind for d in brief.recent_decisions]
    assert kinds[-2:] == ["first_thing", "second_thing"]
    assert brief.recent_decisions[-1].payload == {"note": "b"}
    assert brief.recent_decisions[-1].at is not None


def test_resumption_brief_recent_caps_the_decision_tail(tmp_path):
    proj = _seeded(tmp_path)
    for i in range(5):
        proj.log_decision(f"d{i}")

    brief = proj.resumption_brief(recent=2)
    assert [d.kind for d in brief.recent_decisions] == ["d3", "d4"]


def test_resumption_brief_records_last_session_and_gap(tmp_path):
    proj = _seeded(tmp_path)
    brief = proj.resumption_brief()

    assert brief.last_session_at is not None
    assert brief.gap_seconds is not None and brief.gap_seconds >= 0.0
    assert brief.gap_seconds < 300  # just written


def test_resumption_brief_lists_unrendered_shots(tmp_path):
    proj = _seeded(tmp_path)
    (proj.shot_dir("s01")).mkdir(parents=True, exist_ok=True)
    (proj.shot_dir("s01") / "output.mp4").write_bytes(b"")

    brief = proj.resumption_brief()
    assert brief.unrendered_shot_ids == ("s02",)
    assert any(
        "1 of 2 shots have never been rendered" in s for s in brief.suggested_next
    )


def test_total_spend_prefers_actual_artifact_cost(tmp_path):
    proj = _seeded(tmp_path)
    proj.log_decision(
        "render_shot",
        artifacts=[{"cost_usd": 0.25}, {"cost_usd": 0.75}],
        total_estimated_cost_usd=99.0,
    )
    assert proj.total_spend_usd() == pytest.approx(1.0)


def test_total_spend_falls_back_to_estimate_when_no_artifact_cost(tmp_path):
    proj = _seeded(tmp_path)
    proj.log_decision("render_shot", artifacts=[], total_estimated_cost_usd=0.4)
    proj.log_decision("set_character_anchor", character="thor")  # no cost keys
    assert proj.total_spend_usd() == pytest.approx(0.4)
    assert proj.resumption_brief().total_spend_usd == pytest.approx(0.4)


def test_total_spend_counts_decisions_in_every_store_scope(tmp_path):
    """Money is money whichever scope recorded it.

    ``recent_decisions`` walks all scopes; summing only the project-graph
    scope made ``total_spend_usd`` **under**-report while the brief's caveat
    claimed an upper bound — the two fields disagreed about their own source.
    """
    import uuid as _uuid

    from lacing import (
        Annotation,
        MediaRef,
        Provenance,
        RationalTime,
        Tier,
        TierStereotype,
        TimeInterval,
    )

    from nw.graph import _scope_paths
    from nw.graph_backend import SCOPE_STORYBOARD, open_graph_store

    proj = _seeded(tmp_path)
    proj.log_decision("render_shot", total_estimated_cost_usd=1.0)

    store = open_graph_store(
        _scope_paths(proj.root)[SCOPE_STORYBOARD],
        asset_id=proj.graph.asset_id,
        scope=SCOPE_STORYBOARD,
    )
    store.add_tier(Tier(name="decision", stereotype=TierStereotype.NONE))
    store.add(
        Annotation(
            id=_uuid.uuid4(),
            tier="decision",
            reference=MediaRef(
                asset_id=proj.graph.asset_id,
                interval=TimeInterval(RationalTime(0), RationalTime(0)),
            ),
            body={"kind": "render_panel", "payload": {"total_estimated_cost_usd": 2.5}},
            body_schema_uri="annot://schema/decision/v1",
            provenance=Provenance(
                was_generated_by="agent:test",
                was_attributed_to="user:test",
                was_derived_from=[],
                generated_at_time=RationalTime.now(),
                activity="create",
            ),
        )
    )
    store.close()

    assert proj.total_spend_usd() == pytest.approx(3.5)
    kinds = {d.kind for d in proj.resumption_brief().recent_decisions}
    assert kinds == {"render_shot", "render_panel"}


def test_spend_caveat_is_emitted_only_when_money_was_recorded(tmp_path):
    proj = _seeded(tmp_path)
    assert not any(
        "billed and then failed" in c for c in proj.resumption_brief().caveats
    )

    proj.log_decision("render_shot", total_estimated_cost_usd=2.0)
    caveats = proj.resumption_brief().caveats
    assert any("billed and then failed" in c for c in caveats)


def _derive(proj, parent_id, *, tier="render-result", body=None, schema=None):
    """Append one annotation derived from ``parent_id``; return its id."""
    import uuid as _uuid

    from lacing import Annotation, MediaRef, Provenance, RationalTime, TimeInterval

    child = Annotation(
        id=_uuid.uuid4(),
        tier=tier,
        reference=MediaRef(
            asset_id=proj.graph.asset_id,
            interval=TimeInterval(RationalTime(0), RationalTime(24000)),
        ),
        body=body if body is not None else {"shot_id": "s01", "url": "x"},
        body_schema_uri=schema or "annot://schema/render-result/v1",
        provenance=Provenance(
            was_generated_by="transform:test@1",
            was_attributed_to="agent:test",
            was_derived_from=[parent_id],
            generated_at_time=RationalTime.now(),
            activity="derive",
        ),
    )
    proj.graph.add_annotation(child)
    return child.id


def _rendered_then_edited(tmp_path):
    """The realistic shape: a shot was rendered, then the user edited the shot.

    Returns ``(project, shot_annotation_id, render_result_id)``.
    """
    proj = _seeded(tmp_path)
    shot = proj.graph.shots()[0]
    render_id = _derive(proj, shot.annotation_id)
    proj.graph.upsert_shot(
        shot.body.model_copy(update={"description": "now with a green cap"}),
        interval=shot.interval,
    )
    return proj, shot.annotation_id, render_id


def test_brief_walks_from_the_last_authored_change_not_the_newest_annotation(tmp_path):
    """The walk must start at an *input*, not at whatever was written last.

    A descendant is by construction generated **after** its ancestor, so the
    newest annotation in a provenance graph is a leaf and its descendant set
    is empty. Walking from "the most recently generated non-decision
    annotation" is therefore inverted, and returns ``()`` here — the
    regression that made this field structurally dead.

    The derived annotation is written **last** on purpose: that is what
    distinguishes the two definitions.
    """
    proj = _seeded(tmp_path)
    shot = proj.graph.shots()[-1]  # the most recently authored entity
    render_id = _derive(proj, shot.annotation_id)

    brief = proj.resumption_brief()

    newest = max(
        (a for a in iter_all_annotations(proj.root) if a.tier != VERIFYING_TRACE_TIER),
        key=lambda a: a.provenance.generated_at_time.to_seconds(),
    )
    assert newest.id == render_id, "the derived annotation must be the newest one"

    assert brief.last_authored_change_id == str(shot.annotation_id)
    assert brief.last_authored_change_id != str(render_id)
    assert brief.downstream_of_last_authored_change == (str(render_id),)
    assert brief.downstream_count == 1
    assert any(
        "downstream of the last authored change" in s for s in brief.suggested_next
    )


def test_brief_reports_downstream_after_an_edit_to_an_already_rendered_shot(tmp_path):
    """The other order — render, then edit — is the case the user cares about."""
    proj, shot_id, render_id = _rendered_then_edited(tmp_path)

    brief = proj.resumption_brief()
    assert brief.last_authored_change_id == str(shot_id)
    assert brief.downstream_of_last_authored_change == (str(render_id),)


def test_brief_never_asks_the_user_to_regenerate_an_audit_row(tmp_path):
    """A decision derived from a shot is reachable but is not work to redo."""
    proj, shot_id, render_id = _rendered_then_edited(tmp_path)
    proj.graph.append_decision(
        DecisionBodyV1(kind="render_shot", payload={}), was_derived_from=(shot_id,)
    )

    brief = proj.resumption_brief()
    assert brief.downstream_of_last_authored_change == (str(render_id),)


def test_brief_ignores_verifying_traces(tmp_path):
    """A verifying trace must never be mistaken for an authored change.

    Both properties that make it a hazard hold at once: it carries **no**
    ``was_derived_from`` parents (deliberately — see
    :mod:`nw.bodies.verifying_trace`) and it is written *after* the
    annotation it describes. So under the un-guarded "no parents and not a
    decision" rule it would win ``_last_authored_change`` outright, and the
    brief would walk downstream of a bookkeeping row and report nothing.

    The derived write is **last** on purpose: that is what puts a trace at
    the top of the store and makes the guard non-vacuous.
    """
    proj = _seeded(tmp_path)
    shot_id = proj.graph.shots()[-1].annotation_id
    render_id = _derive(proj, shot_id)

    traces = [
        a for a in iter_all_annotations(proj.root) if a.tier == VERIFYING_TRACE_TIER
    ]
    assert traces, "guard is vacuous unless a trace was actually written"
    assert all(not t.provenance.was_derived_from for t in traces)
    newest = max(
        iter_all_annotations(proj.root),
        key=lambda a: a.provenance.generated_at_time.to_seconds(),
    )
    assert newest.tier == VERIFYING_TRACE_TIER, (
        "guard is vacuous unless a trace is the newest annotation in the store"
    )

    brief = proj.resumption_brief()
    assert brief.last_authored_change_id == str(shot_id)
    assert brief.downstream_of_last_authored_change == (str(render_id),)


def test_downstream_caveat_is_emitted_when_something_is_downstream(tmp_path):
    """The brief must not present reachability as staleness."""
    assert _seeded(tmp_path / "clean").resumption_brief().caveats == ()

    proj, _, _ = _rendered_then_edited(tmp_path / "dirty")

    brief = proj.resumption_brief()
    assert brief.downstream_of_last_authored_change  # the guard is not vacuous
    assert any("upper bound" in c for c in brief.caveats)
    # Reachability is never silently presented as "stale".
    assert not hasattr(brief, "stale")
    assert not any("under-report" in c for c in brief.caveats), (
        "the under-report caveat described the upsert-orphans-lineage bug; "
        "it is fixed, so the caveat must not be re-asserted"
    )


def test_editing_an_entity_keeps_its_downstream_reachable(tmp_path):
    """nw#34: an edit must not orphan the lineage recorded against the entity.

    ``upsert_*`` used to remove-then-insert with a fresh uuid4, so every
    ``was_derived_from`` edge pointing at an edited shot/character/environment
    was severed — the freshness walk then under-reported *to zero* for the
    most common edit there is.
    """
    proj = _seeded(tmp_path)
    shot = proj.graph.shots()[0]
    render_id = _derive(proj, shot.annotation_id)

    edited = shot.body.model_copy(update={"description": "now with a green cap"})
    returned = proj.graph.upsert_shot(edited, interval=shot.interval)

    assert returned == shot.annotation_id, "the entity's identity must survive an edit"
    assert proj.graph.shots()[0].body.description == "now with a green cap"
    assert [a.id for a in descendants_of(proj.root, shot.annotation_id)] == [render_id]


def test_a_no_op_upsert_does_not_count_as_a_change(tmp_path):
    """Re-writing an identical body must not touch ``generated_at_time``.

    ``write_spec`` re-upserts every entity on any spec write, so without this
    a bare ``set_title()`` would look like "the user just edited every shot".
    """
    proj = _seeded(tmp_path)
    shot = proj.graph.shots()[0]
    before = shot.annotation_id
    stamp = next(
        a.provenance.generated_at_time
        for a in iter_all_annotations(proj.root)
        if a.id == before
    )

    proj.set_title("Renamed")

    after = proj.graph.shots()[0]
    assert after.annotation_id == before
    assert (
        next(
            a.provenance.generated_at_time
            for a in iter_all_annotations(proj.root)
            if a.id == before
        )
        == stamp
    )


def test_resumption_brief_flags_characters_without_an_anchor(tmp_path):
    proj = _seeded(tmp_path)
    proj.add_character("thor", description="the narrator")

    brief = proj.resumption_brief()
    assert any("No reference image locked for: thor" in s for s in brief.suggested_next)


def test_resumption_brief_is_deterministic(tmp_path):
    proj = _seeded(tmp_path)
    proj.add_character("thor")
    proj.log_decision("render_shot", total_estimated_cost_usd=1.0)

    a = proj.resumption_brief()
    b = proj.resumption_brief()
    assert a.suggested_next == b.suggested_next
    assert a.caveats == b.caveats
    assert a.downstream_of_last_authored_change == b.downstream_of_last_authored_change
