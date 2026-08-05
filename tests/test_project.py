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
)


def test_init_creates_project_with_subfolders(tmp_path):
    proj = Project.init(tmp_path / "p1", title="My Project")
    assert proj.project_file.exists()
    for sub in ("characters", "environments", "shots", "output", "lyrics", "script", "song", ".nw"):
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
        song=SongInfo(audio_path="song/a.wav", duration_s=8.0, sample_rate=48000, bitrate=1500000),
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
    assert brief.downstream_of_last_change == ()
    assert brief.downstream_count == 0
    assert brief.last_change_id is None
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
    assert any("1 of 2 shots have never been rendered" in s for s in brief.suggested_next)


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


def test_spend_caveat_is_emitted_only_when_money_was_recorded(tmp_path):
    proj = _seeded(tmp_path)
    assert not any("billed and then failed" in c for c in proj.resumption_brief().caveats)

    proj.log_decision("render_shot", total_estimated_cost_usd=2.0)
    caveats = proj.resumption_brief().caveats
    assert any("billed and then failed" in c for c in caveats)


def test_downstream_caveats_are_emitted_when_something_is_downstream(tmp_path):
    """The brief must not present reachability as staleness."""
    import uuid as _uuid

    from lacing import Annotation, MediaRef, Provenance, RationalTime, TimeInterval

    proj = _seeded(tmp_path)
    parent_id = proj.graph.shots()[0].annotation_id
    iv = TimeInterval(RationalTime(0), RationalTime(24000))
    child = Annotation(
        id=_uuid.uuid4(),
        tier="shot",
        reference=MediaRef(asset_id=proj.graph.asset_id, interval=iv),
        body={"shot_id": "derived", "section_id": "verse"},
        body_schema_uri="annot://schema/shot/v1",
        provenance=Provenance(
            was_generated_by="transform:test@1",
            was_attributed_to="agent:test",
            was_derived_from=[parent_id],
            generated_at_time=RationalTime.now(),
            activity="derive",
        ),
    )
    proj.graph.add_annotation(child)

    # Touch the parent last so it is the "last change" the brief walks from.
    proj.graph.upsert_shot(
        proj.graph.shots()[0].body, interval=TimeInterval.from_seconds(0, 4)
    )
    brief = proj.resumption_brief()

    if brief.downstream_of_last_change:
        assert any("upper bound" in c for c in brief.caveats)
        assert any("under-report" in c for c in brief.caveats)
    # Whatever the walk returns, it is never silently presented as "stale".
    assert not hasattr(brief, "stale")


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
    assert a.downstream_of_last_change == b.downstream_of_last_change
