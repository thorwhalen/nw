"""Tests for nw.migrate + nw.graph — graph SSOT migration and traversals."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import UUID, uuid4

import pytest

import nw
from nw.bodies import (
    SECTION_BODY_SCHEMA_URI,
    SHOT_BODY_SCHEMA_URI,
    SectionBodyV1,
    ShotBodyV1,
)
from nw.migrate import (
    is_migrated,
    migrate_to_graph,
    project_graph_db_path,
)


def _seed_pre_graph_project(tmp_path: Path, name: str = "p") -> Path:
    """Build a project.json by hand in the pre-graph format (no sentinel)."""
    root = tmp_path / name
    root.mkdir()
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
        (root / sub).mkdir(exist_ok=True)
    spec = {
        "schema_version": 1,
        "title": "pre-graph project",
        "song": None,
        "characters": [{"name": "thor", "description": "the singer"}],
        "environments": [{"name": "tower", "description": "Gothic"}],
        "sections": [
            {
                "id": "verse",
                "start_s": 0.0,
                "end_s": 8.0,
                "label": "verse",
                "energy": "low",
                "mood": "noir",
            }
        ],
        "shots": [
            {
                "id": "s01",
                "start_s": 0.0,
                "end_s": 8.0,
                "section_id": "verse",
                "render_strategy": "lipsync",
                "environment": "tower",
                "characters": ["thor"],
                "description": "Thor singing",
                "camera": "static",
                "framing": "medium",
                "notes": "",
            }
        ],
        "global_style": "noir",
        "notes": "",
    }
    (root / "project.json").write_text(json.dumps(spec, indent=2))
    return root


# --- migration: idempotency, side-effects ----------------------------------


def test_migrate_writes_sentinel_and_creates_graph_db(tmp_path):
    root = _seed_pre_graph_project(tmp_path)
    assert not is_migrated(root)

    counts = migrate_to_graph(root)
    assert counts["sections"] == 1
    assert counts["shots"] == 1
    assert counts["characters"] == 1
    assert counts["environments"] == 1
    assert is_migrated(root)
    assert project_graph_db_path(root).exists()


def test_migrate_is_idempotent(tmp_path):
    root = _seed_pre_graph_project(tmp_path)
    migrate_to_graph(root)
    second = migrate_to_graph(root)
    assert second.get("already_migrated") == 1


def test_migrate_backs_up_original_project_json(tmp_path):
    root = _seed_pre_graph_project(tmp_path)
    migrate_to_graph(root)
    backup = root / ".nw" / "project.json.pre-graph.bak"
    assert backup.exists()
    # Backup retains the original arrays.
    bak = json.loads(backup.read_text())
    assert "shots" in bak
    assert len(bak["shots"]) == 1


def test_migrate_trims_project_json(tmp_path):
    """After migration, project.json is the metadata-only view."""
    root = _seed_pre_graph_project(tmp_path)
    migrate_to_graph(root)
    trimmed = json.loads((root / "project.json").read_text())
    # graph entities removed:
    assert "shots" not in trimmed
    assert "sections" not in trimmed
    assert "characters" not in trimmed
    assert "environments" not in trimmed
    # but metadata preserved:
    assert trimmed["title"] == "pre-graph project"
    assert trimmed["global_style"] == "noir"
    # tombstone pointer:
    assert trimmed["_graph_db"] == "project.annot.sqlite"


def test_project_auto_migrates_on_init(tmp_path):
    """Constructing Project on a pre-graph project triggers migration."""
    root = _seed_pre_graph_project(tmp_path)
    proj = nw.Project(root)
    assert is_migrated(proj.root)
    spec = proj.read_spec()
    # Graph round-trip: shots and sections come back via the graph synthesis.
    assert len(spec.shots) == 1
    assert spec.shots[0].id == "s01"
    assert spec.shots[0].render_strategy == "lipsync"
    assert spec.sections[0].id == "verse"
    assert spec.characters[0].name == "thor"
    assert spec.environments[0].name == "tower"


# --- new project (init): graph-native from the start -----------------------


def test_init_marks_new_project_migrated(tmp_path):
    proj = nw.Project.init(tmp_path / "fresh", title="Fresh")
    assert is_migrated(proj.root)
    spec = proj.read_spec()
    assert spec.title == "Fresh"
    assert spec.shots == ()


def test_init_then_upsert_then_read_round_trip(tmp_path):
    proj = nw.Project.init(tmp_path / "p", title="rt")
    proj.add_character("thor", description="x")
    proj.add_environment("tower", description="y")
    proj.upsert_section(
        nw.SectionSpec(id="verse", start_s=0.0, end_s=4.0, label="verse")
    )
    proj.upsert_shot(
        nw.ShotSpec(id="s01", start_s=0.0, end_s=4.0, render_strategy="lipsync")
    )

    spec = proj.read_spec()
    assert {c.name for c in spec.characters} == {"thor"}
    assert {e.name for e in spec.environments} == {"tower"}
    assert {s.id for s in spec.sections} == {"verse"}
    assert {s.id for s in spec.shots} == {"s01"}


def test_remove_shot_via_write_spec_clears_graph(tmp_path):
    """Writing a spec without a previously-present shot removes it from the graph."""
    proj = nw.Project.init(tmp_path / "p")
    proj.upsert_shot(nw.ShotSpec(id="s01", start_s=0.0, end_s=4.0))
    proj.upsert_shot(nw.ShotSpec(id="s02", start_s=4.0, end_s=8.0))
    spec = proj.read_spec()
    new_spec = spec.model_copy(
        update={"shots": tuple(s for s in spec.shots if s.id != "s01")}
    )
    proj.write_spec(new_spec)
    after = proj.read_spec()
    assert {s.id for s in after.shots} == {"s02"}


# --- decisions go to the graph + the JSONL audit -------------------------


def test_log_decision_persists_to_graph(tmp_path):
    proj = nw.Project.init(tmp_path / "p")
    proj.log_decision("test_event", a=1, note="hello")
    decisions = proj.graph.decisions()
    assert len(decisions) >= 1
    found = next(d for d in decisions if d.body.kind == "test_event")
    assert found.body.payload["a"] == 1
    # JSONL audit also written.
    audit = (proj.root / ".nw" / "decisions.jsonl").read_text()
    assert "test_event" in audit


# --- nw.graph traversals ---------------------------------------------------


def test_descendants_of_walks_provenance_graph(tmp_path):
    """A 3-deep chain: A → B → C. descendants_of(A) returns {B, C}."""
    proj = nw.Project.init(tmp_path / "p")
    g = proj.graph

    # Root annotation: a section.
    a_id = g.upsert_section(
        SectionBodyV1(section_id="s", label="seed"),
        interval=_iv(0, 4),
    )

    # B derives from A.
    b_id = g.append_decision(
        nw.bodies.DecisionBodyV1(kind="step_b", payload={}),
        was_derived_from=(a_id,),
    )

    # C derives from B.
    c_id = g.append_decision(
        nw.bodies.DecisionBodyV1(kind="step_c", payload={}),
        was_derived_from=(b_id,),
    )

    # An unrelated decision D shouldn't appear.
    d_id = g.append_decision(
        nw.bodies.DecisionBodyV1(kind="unrelated", payload={}),
    )

    desc = nw.descendants_of(proj.root, a_id)
    desc_ids = {ann.id for ann in desc}
    assert b_id in desc_ids
    assert c_id in desc_ids
    assert d_id not in desc_ids
    assert a_id not in desc_ids  # source itself is not in its own descendants


def test_stale_after_is_not_an_alias_for_descendants_of(tmp_path):
    """The two verbs answer different questions and must be able to disagree.

    Replaces ``test_stale_after_is_alias_for_descendants_of`` (nw#24). The
    alias is the defect: reachability reports everything downstream of a
    change whether or not anything about it is out of date.

    Kept here, next to the ``descendants_of`` test it used to mirror, so the
    contrast is visible; the full behaviour lives in ``test_freshness.py``.
    """
    proj = nw.Project.init(tmp_path / "p")
    g = proj.graph
    a_id = g.upsert_section(
        SectionBodyV1(section_id="s", label="seed"),
        interval=_iv(0, 4),
    )
    b_id = g.append_decision(
        nw.bodies.DecisionBodyV1(kind="step_b", payload={}),
        was_derived_from=(a_id,),
    )

    # B is reachable from A — that has not changed and must not.
    assert {a.id for a in nw.descendants_of(proj.root, a_id)} == {b_id}
    # …but nothing about A has changed since B was written, so B is not stale.
    assert nw.stale_after(proj.root, a_id) == []


def test_derived_from_returns_one_hop(tmp_path):
    proj = nw.Project.init(tmp_path / "p")
    g = proj.graph
    a_id = g.upsert_section(SectionBodyV1(section_id="s"), interval=_iv(0, 4))
    b_id = g.append_decision(
        nw.bodies.DecisionBodyV1(kind="b", payload={}),
        was_derived_from=(a_id,),
    )
    c_id = g.append_decision(
        nw.bodies.DecisionBodyV1(kind="c", payload={}),
        was_derived_from=(b_id,),
    )
    # derived_from(c) returns {b}, not {a, b}.
    parents = nw.derived_from(proj.root, c_id)
    parent_ids = {p.id for p in parents}
    assert parent_ids == {b_id}


def test_annotations_at_tier_filters(tmp_path):
    proj = nw.Project.init(tmp_path / "p")
    proj.upsert_shot(nw.ShotSpec(id="s01", start_s=0, end_s=4))
    proj.upsert_shot(nw.ShotSpec(id="s02", start_s=4, end_s=8))
    proj.upsert_section(nw.SectionSpec(id="v", start_s=0, end_s=8, label="v"))

    shots = nw.annotations_at_tier(proj.root, "shot")
    sections = nw.annotations_at_tier(proj.root, "section")
    assert len(shots) == 2
    assert len(sections) == 1


def _iv(start_s: float, end_s: float):
    """Helper to build a TimeInterval."""
    from fractions import Fraction
    from lacing import RationalTime, TimeInterval, DEFAULT_RATE

    def to_rt(s):
        return RationalTime.from_fraction(
            Fraction(round(s * DEFAULT_RATE), DEFAULT_RATE), rate=DEFAULT_RATE
        )

    return TimeInterval(start=to_rt(start_s), end=to_rt(end_s))


# --- project-aware execute_render records a render decision ----------------


def test_execute_render_with_project_writes_render_decision(tmp_path, monkeypatch):
    """When a Project is passed, execute_render appends a decision derived from the shot."""
    import sys, types, struct

    # Stub fal_client.upload_file + fal_client.subscribe.
    counter = [0]

    def upload_file(path):
        counter[0] += 1
        return f"https://fal.storage/test/u-{counter[0]}.bin"

    def subscribe(application, *, arguments, with_logs, on_queue_update):
        if "image_to_video" in application or "hailuo" in application:
            return {
                "video": {
                    "url": "http://x/v.mp4",
                    "duration": 4.0,
                    "content_type": "video/mp4",
                }
            }
        return {"images": [{"url": "http://x/img.png", "content_type": "image/png"}]}

    fake = types.SimpleNamespace(
        InProgress=type("IP", (), {"__init__": lambda s, l: None}),
        upload_file=upload_file,
        subscribe=subscribe,
    )
    monkeypatch.setitem(sys.modules, "fal_client", fake)

    # Minimal but real WAV for the audio probe.
    sr = 8000
    n = sr * 4
    wav = (
        b"RIFF"
        + struct.pack("<I", 36 + n)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sr, sr, 1, 8)
        + b"data"
        + struct.pack("<I", n)
        + (b"\x80" * n)
    )

    proj = nw.Project.init(tmp_path / "p", title="t")
    (proj.root / "song" / "song.wav").write_bytes(wav)
    from nw.schema import SongInfo

    proj.update_spec(song=SongInfo(audio_path="song/song.wav", duration_s=4.0))

    proj.upsert_section(nw.SectionSpec(id="v", start_s=0.0, end_s=4.0))
    proj.upsert_shot(
        nw.ShotSpec(
            id="s01",
            start_s=0.0,
            end_s=4.0,
            render_strategy="text_to_video",
            description="something visible",
        )
    )

    prep = nw.prepare_shot(proj, "s01", upload=True)
    plan = nw.plan_render_shot(prep)

    # Patch urlretrieve so download in materialize() doesn't hit the network.
    import urllib.request

    monkeypatch.setattr(
        urllib.request,
        "urlretrieve",
        lambda url, dst: (Path(dst).write_bytes(b"\x00" * 16), None),
    )

    # Patch ffmpeg trim/pad to be a no-op copy via shutil — we only care about the
    # render-decision side effect.
    import nw.renderers._common as rcom

    monkeypatch.setattr(
        rcom,
        "trim_or_pad_video",
        lambda src, target_s, dst: (shutil.copy2(src, dst), dst)[1],
    )

    out = nw.execute_render(prep, plan, project=proj, use_cache=False)
    assert out.exists()

    # The render decision was written to the graph and is downstream of the shot.
    decisions = proj.graph.decisions()
    render_decisions = [d for d in decisions if d.body.kind == "render_shot"]
    assert len(render_decisions) == 1
    rd = render_decisions[0].body.payload
    assert rd["shot_id"] == "s01"
    assert rd["strategy"] == "text_to_video"
    assert rd["total_estimated_cost_usd"] > 0

    # And reelee's freshness traversal from the shot finds the render decision.
    shot_ann = proj.graph.shots()[0]
    desc_ids = {a.id for a in nw.descendants_of(proj.root, shot_ann.annotation_id)}
    assert render_decisions[0].annotation_id in desc_ids

    # nw#24, on the real production write path rather than a hand-built graph:
    # reachable is not the same as stale. Nothing has changed since the render,
    # so there is nothing to redo…
    assert nw.stale_after(proj.root, shot_ann.annotation_id) == []
    # …and editing the shot brings it straight back.
    proj.upsert_shot(
        nw.ShotSpec(
            id="s01",
            start_s=0.0,
            end_s=4.0,
            render_strategy="text_to_video",
            description="something else entirely",
        )
    )
    stale_ids = {a.id for a in nw.stale_after(proj.root, shot_ann.annotation_id)}
    assert render_decisions[0].annotation_id in stale_ids
