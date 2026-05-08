"""Tests for nw.storyboard — Storyboard ↔ Project bridge."""

from __future__ import annotations

import struct
import sys
import types
from pathlib import Path

import pytest

import nw
from nw.schema import SectionSpec, ShotSpec, SongInfo


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("FALAW_DATA_DIR", str(tmp_path / "_falaw"))
    monkeypatch.setenv("FALAW_CACHE_DIR", str(tmp_path / "_falaw" / "cache"))
    from falaw.events import clear_subscribers
    from falaw.journal import _default_journal
    _default_journal.cache_clear()
    clear_subscribers()
    yield
    clear_subscribers()
    _default_journal.cache_clear()


def _patch_fal(monkeypatch, *, image_url="http://x/panel.png"):
    captured: list[dict] = []

    def subscribe(application, *, arguments, with_logs, on_queue_update):
        captured.append({"application": application, "arguments": dict(arguments)})
        return {"images": [{"url": image_url, "content_type": "image/png"}]}

    fake = types.SimpleNamespace(InProgress=type("IP", (), {}), subscribe=subscribe)
    monkeypatch.setitem(sys.modules, "fal_client", fake)
    return captured


def _seed_project_with_shots(tmp_path) -> nw.Project:
    proj = nw.Project.init(tmp_path / "p")
    song = proj.root / "song" / "song.wav"
    song.write_bytes(_minimal_wav_bytes())
    proj.update_spec(
        song=SongInfo(audio_path="song/song.wav", duration_s=10.0)
    )
    proj.set_global_style("noir, candlelight")
    proj.upsert_section(SectionSpec(id="v", start_s=0.0, end_s=10.0))
    proj.upsert_shot(ShotSpec(
        id="s01", start_s=0.0, end_s=4.0,
        section_id="v", description="Bell tower at moonlight",
        framing="wide", camera="slow push-in",
    ))
    proj.upsert_shot(ShotSpec(
        id="s02", start_s=4.0, end_s=10.0,
        section_id="v", description="Thor at the piano",
        framing="medium", camera="static",
    ))
    return proj


def _minimal_wav_bytes() -> bytes:
    sample_rate = 8000
    n_frames = sample_rate * 10
    riff_size = 36 + n_frames
    return (
        b"RIFF" + struct.pack("<I", riff_size) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate, 1, 8)
        + b"data" + struct.pack("<I", n_frames)
        + (b"\x80" * n_frames)
    )


# --- project_asset_id ------------------------------------------------------


def test_project_asset_id_uses_song_hash(tmp_path):
    proj = _seed_project_with_shots(tmp_path)
    asset_id = nw.project_asset_id(proj)
    # SHA-256 hex
    assert len(asset_id) == 64
    assert all(c in "0123456789abcdef" for c in asset_id)


def test_project_asset_id_falls_back_when_no_song(tmp_path):
    proj = nw.Project.init(tmp_path / "p")
    aid = nw.project_asset_id(proj)
    assert len(aid) == 64


# --- storyboard_from_shots -------------------------------------------------


def test_storyboard_from_shots_one_panel_per_shot(tmp_path):
    proj = _seed_project_with_shots(tmp_path)
    sb, intervals = nw.storyboard_from_shots(proj)
    assert len(sb.panels) == 2
    # Style and title carry from the project.
    assert sb.style == "noir, candlelight"
    # Each panel points at its source shot.
    shot_ids = {p.shot_id for p in sb.panels}
    assert shot_ids == {"s01", "s02"}
    # Captions inherit shot.description.
    captions = {p.caption for p in sb.panels}
    assert "Bell tower at moonlight" in captions
    assert "Thor at the piano" in captions
    # Intervals match shot times.
    assert len(intervals) == 2


# --- open / save round-trip ------------------------------------------------


def test_open_returns_empty_when_nothing_saved(tmp_path):
    proj = _seed_project_with_shots(tmp_path)
    sb = nw.open_storyboard(proj)
    assert sb.panels == ()
    assert sb.asset_id == nw.project_asset_id(proj)


def test_save_then_open_roundtrip(tmp_path):
    proj = _seed_project_with_shots(tmp_path)
    sb, intervals = nw.storyboard_from_shots(proj, title="The Bells", style="noir-x")
    nw.save_storyboard(proj, sb, panel_intervals=intervals)
    # Reload.
    loaded = nw.open_storyboard(proj)
    assert loaded.title == "The Bells"
    assert loaded.style == "noir-x"
    assert len(loaded.panels) == 2
    panel_ids = {p.panel_id for p in loaded.panels}
    assert panel_ids == {p.panel_id for p in sb.panels}


def test_save_is_idempotent(tmp_path):
    """Re-saving replaces existing panels rather than accumulating."""
    proj = _seed_project_with_shots(tmp_path)
    sb1, ivs1 = nw.storyboard_from_shots(proj)
    nw.save_storyboard(proj, sb1, panel_intervals=ivs1)

    # Build a second, smaller storyboard and save it.
    from artful import PanelBody, Storyboard
    sb2 = Storyboard(
        title="just one",
        asset_id=sb1.asset_id,
        panels=(PanelBody(panel_id="newer", caption="x"),),
    )
    from lacing import TimeInterval
    nw.save_storyboard(
        proj, sb2,
        panel_intervals={"newer": TimeInterval.from_seconds(0, 5)},
    )

    loaded = nw.open_storyboard(proj)
    assert len(loaded.panels) == 1
    assert loaded.panels[0].panel_id == "newer"
    assert loaded.title == "just one"


def test_save_logs_decision(tmp_path):
    proj = _seed_project_with_shots(tmp_path)
    sb, ivs = nw.storyboard_from_shots(proj)
    nw.save_storyboard(proj, sb, panel_intervals=ivs)
    log = (proj.root / ".nw" / "decisions.jsonl").read_text()
    assert "save_storyboard" in log
    assert '"panel_count": 2' in log


# --- plan_render_panel_images ----------------------------------------------


def test_plan_one_call_per_panel_with_caption(tmp_path):
    proj = _seed_project_with_shots(tmp_path)
    sb, _ = nw.storyboard_from_shots(proj)
    plan, panel_ids = nw.plan_render_panel_images(sb, quality="balanced")
    assert len(plan.calls) == 2
    assert len(panel_ids) == 2
    for call in plan.calls:
        assert call.tool == "generate_image"
        assert "flux" in call.application  # default image model
    # Cost is honest (not None).
    assert plan.has_unknown_costs is False


def test_plan_skips_panels_without_caption(tmp_path):
    proj = nw.Project.init(tmp_path / "p")
    from artful import Storyboard, PanelBody
    sb = Storyboard(
        title="t", asset_id="x",
        panels=(
            PanelBody(panel_id="p1", caption="have caption"),
            PanelBody(panel_id="p2", caption=""),  # skipped
        ),
    )
    plan, panel_ids = nw.plan_render_panel_images(sb)
    assert panel_ids == ["p1"]
    assert len(plan.calls) == 1


def test_plan_skips_panels_with_existing_seed_image(tmp_path):
    from artful import Storyboard, PanelBody, PanelImage
    sb = Storyboard(
        title="t", asset_id="x",
        panels=(
            PanelBody(panel_id="p1", caption="x", images=(PanelImage(path="a.png", role="seed"),)),
            PanelBody(panel_id="p2", caption="y"),
        ),
    )
    plan, panel_ids = nw.plan_render_panel_images(sb, only_missing=True)
    assert panel_ids == ["p2"]


def test_plan_re_renders_all_when_only_missing_false():
    from artful import Storyboard, PanelBody, PanelImage
    sb = Storyboard(
        title="t", asset_id="x",
        panels=(
            PanelBody(panel_id="p1", caption="x", images=(PanelImage(path="a.png", role="seed"),)),
            PanelBody(panel_id="p2", caption="y"),
        ),
    )
    plan, panel_ids = nw.plan_render_panel_images(sb, only_missing=False)
    assert panel_ids == ["p1", "p2"]
    assert len(plan.calls) == 2


def test_plan_includes_style_and_framing_in_prompt(tmp_path):
    proj = _seed_project_with_shots(tmp_path)
    sb, _ = nw.storyboard_from_shots(proj)
    plan, _ = nw.plan_render_panel_images(sb)
    p1 = plan.calls[0].arguments["prompt"]
    assert "noir" in p1.lower()
    assert "framing" in p1.lower()


# --- execute_render_panel_images -------------------------------------------


def test_execute_attaches_seed_images_and_downloads(tmp_path, monkeypatch):
    """End-to-end: plan + execute → updated storyboard with attached images."""
    # Patch fal_client subscribe to return a known URL, and patch URL download.
    captured = _patch_fal(monkeypatch, image_url="https://example.invalid/panel.png")

    # Patch urlretrieve so we don't actually hit the network.
    fake_image = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # minimal "PNG-shaped" bytes
    def fake_urlretrieve(url, dst):
        Path(dst).write_bytes(fake_image)
        return (dst, None)
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlretrieve", fake_urlretrieve)

    proj = _seed_project_with_shots(tmp_path)
    sb, _ = nw.storyboard_from_shots(proj)
    plan, panel_ids = nw.plan_render_panel_images(sb)
    updated = nw.execute_render_panel_images(proj, sb, plan, panel_ids, use_cache=False)

    # All panels now have a seed image.
    seeds_by_panel = {p.panel_id: [i for i in p.images if i.role == "seed"] for p in updated.panels}
    for pid, seeds in seeds_by_panel.items():
        assert len(seeds) == 1, f"{pid}: expected 1 seed, got {len(seeds)}"

    # Files were downloaded under storyboard/.
    storyboard_dir = proj.root / "storyboard"
    assert storyboard_dir.is_dir()
    image_files = sorted(storyboard_dir.glob("*.png"))
    assert len(image_files) == 2

    # Each PanelImage has a valid SHA-256 artifact_id matching the bytes.
    import hashlib
    expected = hashlib.sha256(fake_image).hexdigest()
    for panel in updated.panels:
        for img in panel.images:
            if img.role == "seed":
                assert img.artifact_id == expected


def test_execute_mismatched_panel_ids_raises():
    """plan_ids must match plan length."""
    from artful import Storyboard
    from falaw import Plan
    proj_root = Path("/tmp/no-such")  # we won't reach the file system
    sb = Storyboard(asset_id="x")
    # Build a fake plan (1 call) but pass 2 panel_ids.
    from falaw import CallPlan
    plan = Plan(calls=(CallPlan(tool="t", application="a", arguments={}, output_kind="image"),))
    class FakeProject:
        @property
        def root(self):
            return proj_root
    with pytest.raises(ValueError, match="panel_ids"):
        nw.execute_render_panel_images(FakeProject(), sb, plan, ["a", "b"])
