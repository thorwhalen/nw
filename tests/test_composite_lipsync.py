"""Tests for nw.renderers.composite_lipsync — the keystone deliverable.

This is the strategy that produces "Thor in a bell tower playing piano,
lipsynced to the song" — the user's actual goal from the muvid_project run.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from nw import (
    Project,
    SectionSpec,
    ShotSpec,
    list_strategies,
    plan_render_shot,
    prepare_shot,
)


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


def _patch_fal_storage(monkeypatch):
    counter = [0]

    def upload_file(path):
        counter[0] += 1
        return f"https://fal.storage/test/upload-{counter[0]}.bin"

    fake = types.SimpleNamespace(
        InProgress=type("IP", (), {"__init__": lambda s, l: None}),
        upload_file=upload_file,
    )
    monkeypatch.setitem(sys.modules, "fal_client", fake)


def _seed_project_for_thor_in_bell_tower(tmp_path) -> Project:
    """The fixture: a project with Thor (character) and a bell-tower env, one shot."""
    proj = Project.init(tmp_path / "p")

    # Song
    song = proj.root / "song" / "song.wav"
    song.write_bytes(_minimal_wav_bytes())
    from nw.schema import SongInfo
    proj.update_spec(
        song=SongInfo(audio_path="song/song.wav", duration_s=10.0, sample_rate=8000, bitrate=64000)
    )

    # Character
    proj.add_character("thor", description="contemplative, mid-30s, wintry palette")
    anchor = proj.character_dir("thor") / "selected" / "thor.png"
    anchor.parent.mkdir(parents=True, exist_ok=True)
    anchor.write_bytes(b"PNG-thor")
    proj.set_character_anchor("thor", anchor)

    # Environment
    proj.add_environment("bell_tower", description="Gothic, frosted, candlelight")
    env_img = proj.environment_dir("bell_tower") / "establishing.png"
    env_img.write_bytes(b"PNG-tower")

    # Section + shot
    proj.upsert_section(SectionSpec(id="verse", start_s=0.0, end_s=10.0))
    proj.upsert_shot(
        ShotSpec(
            id="s01",
            start_s=0.0, end_s=8.0,
            section_id="verse",
            render_strategy="composite_lipsync",
            characters=("thor",),
            environment="bell_tower",
            description="Thor stands at the bell tower, playing piano",
            framing="medium",
            camera="locked",
        )
    )
    return proj


def _minimal_wav_bytes() -> bytes:
    import struct
    sample_rate = 8000
    n_frames = sample_rate * 10
    data_size = n_frames
    riff_size = 36 + data_size
    header = (
        b"RIFF"
        + struct.pack("<I", riff_size)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate, 1, 8)
        + b"data"
        + struct.pack("<I", data_size)
    )
    return header + (b"\x80" * data_size)


# --- registration ----------------------------------------------------------


def test_composite_lipsync_is_registered():
    assert "composite_lipsync" in list_strategies()


# --- planning --------------------------------------------------------------


def test_composite_lipsync_plan_has_two_calls(tmp_path, monkeypatch):
    _patch_fal_storage(monkeypatch)
    proj = _seed_project_for_thor_in_bell_tower(tmp_path)
    prep = prepare_shot(proj, "s01")
    plan = plan_render_shot(prep)
    assert len(plan.calls) == 2
    assert plan.calls[0].tool == "composite_character_in_environment"
    assert plan.calls[1].tool == "animate_face"


def test_composite_lipsync_plan_avatar_uses_placeholder(tmp_path, monkeypatch):
    """Second call must reference the first via "<from 0>" (executor rewrites)."""
    _patch_fal_storage(monkeypatch)
    proj = _seed_project_for_thor_in_bell_tower(tmp_path)
    prep = prepare_shot(proj, "s01")
    plan = plan_render_shot(prep)
    assert plan.calls[1].arguments["image_url"] == "<from 0>"


def test_composite_lipsync_plan_uses_omnihuman_by_default(tmp_path, monkeypatch):
    _patch_fal_storage(monkeypatch)
    proj = _seed_project_for_thor_in_bell_tower(tmp_path)
    prep = prepare_shot(proj, "s01")
    plan = plan_render_shot(prep)
    avatar_call = plan.calls[1]
    assert "omnihuman" in avatar_call.application


def test_composite_lipsync_cost_is_known(tmp_path, monkeypatch):
    """Both compose model and omnihuman are priced — total cost > 0 and known."""
    _patch_fal_storage(monkeypatch)
    proj = _seed_project_for_thor_in_bell_tower(tmp_path)
    prep = prepare_shot(proj, "s01")
    plan = plan_render_shot(prep)
    assert plan.has_unknown_costs is False
    assert plan.total_cost_usd > 0


def test_composite_lipsync_compose_prompt_includes_action(tmp_path, monkeypatch):
    """The composite prompt must include the shot's description so Kontext
    composes the character DOING something, not just standing there."""
    _patch_fal_storage(monkeypatch)
    proj = _seed_project_for_thor_in_bell_tower(tmp_path)
    prep = prepare_shot(proj, "s01")
    plan = plan_render_shot(prep)
    compose_prompt = plan.calls[0].arguments["prompt"]
    assert "playing piano" in compose_prompt or "bell tower" in compose_prompt


def test_composite_lipsync_model_overrides(tmp_path, monkeypatch):
    _patch_fal_storage(monkeypatch)
    proj = _seed_project_for_thor_in_bell_tower(tmp_path)
    prep = prepare_shot(proj, "s01")
    plan = plan_render_shot(
        prep,
        model_overrides={
            "image_edit": "fal-ai/flux-pro/kontext/max",
            "avatar": "fal-ai/ai-avatar",
        },
    )
    assert plan.calls[0].application == "fal-ai/flux-pro/kontext/max"
    assert plan.calls[1].application == "fal-ai/ai-avatar"


# --- failure modes ---------------------------------------------------------


def test_composite_lipsync_without_environment_raises(tmp_path, monkeypatch):
    """No environment → composite is impossible. Plan-time error, not silent fallback."""
    _patch_fal_storage(monkeypatch)
    proj = Project.init(tmp_path / "p")
    song = proj.root / "song" / "song.wav"
    song.write_bytes(_minimal_wav_bytes())
    from nw.schema import SongInfo
    proj.update_spec(song=SongInfo(audio_path="song/song.wav", duration_s=10.0))
    proj.add_character("thor")
    a = proj.character_dir("thor") / "selected" / "t.png"
    a.parent.mkdir(parents=True, exist_ok=True)
    a.write_bytes(b"x")
    proj.set_character_anchor("thor", a)
    proj.upsert_section(SectionSpec(id="v", start_s=0, end_s=8))
    proj.upsert_shot(
        ShotSpec(
            id="s01", start_s=0, end_s=8, section_id="v",
            render_strategy="composite_lipsync",
            characters=("thor",),
            environment="",  # missing
            description="x",
        )
    )
    prep = prepare_shot(proj, "s01")
    with pytest.raises(RuntimeError, match="no environment anchor"):
        plan_render_shot(prep)


def test_composite_lipsync_without_character_raises(tmp_path, monkeypatch):
    _patch_fal_storage(monkeypatch)
    proj = Project.init(tmp_path / "p")
    song = proj.root / "song" / "song.wav"
    song.write_bytes(_minimal_wav_bytes())
    from nw.schema import SongInfo
    proj.update_spec(song=SongInfo(audio_path="song/song.wav", duration_s=10.0))
    proj.add_environment("env")
    (proj.environment_dir("env") / "establishing.png").write_bytes(b"x")
    proj.upsert_section(SectionSpec(id="v", start_s=0, end_s=8))
    proj.upsert_shot(
        ShotSpec(
            id="s01", start_s=0, end_s=8, section_id="v",
            render_strategy="composite_lipsync",
            characters=(),  # missing
            environment="env",
            description="x",
        )
    )
    prep = prepare_shot(proj, "s01")
    with pytest.raises(RuntimeError, match="no character anchor"):
        plan_render_shot(prep)
