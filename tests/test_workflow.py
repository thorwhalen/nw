"""Tests for nw.workflow — prepare_shot / plan_render_shot / execute_render."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nw import (
    Project,
    ShotSpec,
    SectionSpec,
    list_strategies,
    plan_render_shot,
    prepare_shot,
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Isolate falaw cache + journal so cache hits don't bleed across tests."""
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
    """Stub fal_client.upload_file so prepare_shot(upload=True) works offline."""
    counter = [0]

    def upload_file(path):
        counter[0] += 1
        return f"https://fal.storage/test/upload-{counter[0]}.bin"

    fake = types.SimpleNamespace(
        InProgress=type("IP", (), {"__init__": lambda s, l: None}),
        upload_file=upload_file,
    )
    monkeypatch.setitem(sys.modules, "fal_client", fake)


def _seed_project_with_one_shot(tmp_path, *, strategy: str, has_song: bool = True,
                                 has_character: bool = False,
                                 has_environment: bool = False) -> Project:
    proj = Project.init(tmp_path / "p")
    if has_song:
        # Make a tiny silent wav so song_path() returns something that exists.
        song = proj.root / "song" / "song.wav"
        song.write_bytes(_minimal_wav_bytes())
        from nw.schema import SongInfo
        proj.update_spec(song=SongInfo(audio_path="song/song.wav", duration_s=10.0, sample_rate=8000, bitrate=64000))

    if has_character:
        proj.add_character("thor", description="narrator")
        anchor = proj.character_dir("thor") / "selected" / "thor.png"
        anchor.parent.mkdir(parents=True, exist_ok=True)
        anchor.write_bytes(b"PNG-bytes-here")
        proj.set_character_anchor("thor", anchor)

    if has_environment:
        proj.add_environment("bell_tower", description="Gothic")
        env_img = proj.environment_dir("bell_tower") / "establishing.png"
        env_img.write_bytes(b"PNG-env-here")

    proj.upsert_section(SectionSpec(id="verse", start_s=0.0, end_s=10.0))
    proj.upsert_shot(
        ShotSpec(
            id="s01",
            start_s=0.0, end_s=8.0,
            section_id="verse",
            render_strategy=strategy,
            characters=("thor",) if has_character else (),
            environment="bell_tower" if has_environment else "",
            description="bell tower at moonlight",
            framing="medium",
        )
    )
    return proj


def _minimal_wav_bytes() -> bytes:
    """A tiny valid WAV file (44 bytes header + 0 samples) for testing."""
    import struct
    sample_rate = 8000
    bytes_per_sample = 1
    n_channels = 1
    n_frames = sample_rate * 10  # 10 seconds
    bits_per_sample = bytes_per_sample * 8
    block_align = n_channels * bytes_per_sample
    byte_rate = sample_rate * block_align
    data_size = n_frames * block_align
    riff_size = 36 + data_size
    header = (
        b"RIFF"
        + struct.pack("<I", riff_size)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, n_channels, sample_rate, byte_rate, block_align, bits_per_sample)
        + b"data"
        + struct.pack("<I", data_size)
    )
    return header + (b"\x80" * data_size)


# --- registry --------------------------------------------------------------


def test_built_in_strategies_registered():
    names = list_strategies()
    for expected in ("lipsync", "image_to_video", "text_to_video", "still"):
        assert expected in names


# --- prepare_shot ----------------------------------------------------------


def test_prepare_shot_extracts_audio_slice(tmp_path, monkeypatch):
    _patch_fal_storage(monkeypatch)
    proj = _seed_project_with_one_shot(tmp_path, strategy="still", has_character=True)
    prep = prepare_shot(proj, "s01", upload=False)
    assert prep.audio_slice_path.exists()
    assert prep.audio_slice_path.name == "audio.wav"


def test_prepare_shot_no_upload_leaves_urls_empty(tmp_path):
    proj = _seed_project_with_one_shot(tmp_path, strategy="still", has_character=True)
    prep = prepare_shot(proj, "s01", upload=False)
    assert prep.audio_slice_url == ""
    assert prep.character_anchor_urls == {}
    assert prep.environment_anchor_url == ""


def test_prepare_shot_upload_populates_urls(tmp_path, monkeypatch):
    _patch_fal_storage(monkeypatch)
    proj = _seed_project_with_one_shot(
        tmp_path, strategy="lipsync", has_character=True
    )
    prep = prepare_shot(proj, "s01", upload=True)
    assert prep.audio_slice_url.startswith("https://")
    assert "thor" in prep.character_anchor_urls
    assert prep.character_anchor_urls["thor"].startswith("https://")


def test_prepare_shot_resolves_environment(tmp_path, monkeypatch):
    _patch_fal_storage(monkeypatch)
    proj = _seed_project_with_one_shot(
        tmp_path, strategy="image_to_video", has_environment=True
    )
    prep = prepare_shot(proj, "s01", upload=True)
    assert prep.environment_anchor_path is not None
    assert prep.environment_anchor_path.name == "establishing.png"
    assert prep.environment_anchor_url.startswith("https://")


def test_prepare_shot_unknown_id_raises(tmp_path):
    proj = _seed_project_with_one_shot(tmp_path, strategy="still", has_character=True)
    with pytest.raises(KeyError, match="No shot 'nope'"):
        prepare_shot(proj, "nope", upload=False)


# --- plan_render_shot ------------------------------------------------------


def test_plan_lipsync_single_call_high_quality(tmp_path, monkeypatch):
    _patch_fal_storage(monkeypatch)
    proj = _seed_project_with_one_shot(tmp_path, strategy="lipsync", has_character=True)
    prep = prepare_shot(proj, "s01")
    plan = plan_render_shot(prep)
    assert len(plan.calls) == 1
    call = plan.calls[0]
    assert call.tool == "animate_face"
    # Default quality="balanced" gets bumped to "high" in lipsync to pick omnihuman.
    assert "omnihuman" in call.application
    assert call.estimated_cost_usd > 0
    assert call.metadata["strategy"] == "lipsync"


def test_plan_lipsync_avatar_override(tmp_path, monkeypatch):
    _patch_fal_storage(monkeypatch)
    proj = _seed_project_with_one_shot(tmp_path, strategy="lipsync", has_character=True)
    prep = prepare_shot(proj, "s01")
    plan = plan_render_shot(
        prep, model_overrides={"avatar": "fal-ai/ai-avatar"}
    )
    assert plan.calls[0].application == "fal-ai/ai-avatar"


def test_plan_lipsync_without_character_raises(tmp_path, monkeypatch):
    _patch_fal_storage(monkeypatch)
    proj = _seed_project_with_one_shot(tmp_path, strategy="lipsync", has_character=False)
    prep = prepare_shot(proj, "s01")
    with pytest.raises(RuntimeError, match="no character anchor"):
        plan_render_shot(prep)


def test_plan_image_to_video_with_environment_one_call(tmp_path, monkeypatch):
    _patch_fal_storage(monkeypatch)
    proj = _seed_project_with_one_shot(
        tmp_path, strategy="image_to_video", has_environment=True
    )
    prep = prepare_shot(proj, "s01")
    plan = plan_render_shot(prep)
    # Environment present → only the i2v call needed (no fresh-still gen).
    assert len(plan.calls) == 1
    assert plan.calls[0].tool == "image_to_video"


def test_plan_image_to_video_without_environment_two_calls(tmp_path, monkeypatch):
    _patch_fal_storage(monkeypatch)
    proj = _seed_project_with_one_shot(
        tmp_path, strategy="image_to_video", has_environment=False
    )
    prep = prepare_shot(proj, "s01")
    plan = plan_render_shot(prep)
    # No environment → fresh-still gen + i2v = 2 calls.
    assert len(plan.calls) == 2
    assert plan.calls[0].tool == "generate_image"
    assert plan.calls[1].tool == "image_to_video"
    # Second call references the first via placeholder.
    assert plan.calls[1].arguments.get("image_url") == "<from 0>"


def test_plan_text_to_video_single_call(tmp_path, monkeypatch):
    _patch_fal_storage(monkeypatch)
    proj = _seed_project_with_one_shot(tmp_path, strategy="text_to_video")
    prep = prepare_shot(proj, "s01")
    plan = plan_render_shot(prep)
    assert len(plan.calls) == 1
    assert plan.calls[0].tool == "text_to_video"


def test_plan_still_with_anchor_zero_calls(tmp_path, monkeypatch):
    _patch_fal_storage(monkeypatch)
    proj = _seed_project_with_one_shot(
        tmp_path, strategy="still", has_environment=True
    )
    prep = prepare_shot(proj, "s01")
    plan = plan_render_shot(prep)
    # When an anchor exists, "still" can run entirely locally.
    assert len(plan.calls) == 0
    assert plan.total_cost_usd == 0.0


def test_plan_still_without_anchor_one_call(tmp_path, monkeypatch):
    _patch_fal_storage(monkeypatch)
    proj = _seed_project_with_one_shot(
        tmp_path, strategy="still", has_character=False, has_environment=False
    )
    prep = prepare_shot(proj, "s01")
    plan = plan_render_shot(prep)
    assert len(plan.calls) == 1
    assert plan.calls[0].tool == "generate_image"


def test_plan_unknown_strategy_raises(tmp_path, monkeypatch):
    _patch_fal_storage(monkeypatch)
    proj = _seed_project_with_one_shot(
        tmp_path, strategy="weird_strategy", has_character=True
    )
    prep = prepare_shot(proj, "s01")
    with pytest.raises(KeyError, match="No render strategy 'weird_strategy'"):
        plan_render_shot(prep)


# --- end-to-end with stubbed fal --------------------------------------------


def test_end_to_end_lipsync_planning_only(tmp_path, monkeypatch):
    """The full happy-path planning workflow, no execute."""
    _patch_fal_storage(monkeypatch)
    proj = _seed_project_with_one_shot(tmp_path, strategy="lipsync", has_character=True)

    # Step 1: prepare (local + uploads).
    prep = prepare_shot(proj, "s01")
    assert prep.shot_id == "s01"

    # Step 2: plan (pure data, no fal).
    plan = plan_render_shot(prep)
    assert len(plan.calls) == 1
    assert plan.has_unknown_costs is False
    cost = plan.total_cost_usd
    assert cost > 0  # should be ~0.10/s * 8s = 0.80 for omnihuman

    # Inspect: budget gate works.
    if cost > 100:
        pytest.fail(f"cost {cost} suspiciously high; check pricing data")

    # All cost predictions are based on populated cost_estimate, not None.
    assert all(c.estimated_cost_usd is not None for c in plan.calls)
