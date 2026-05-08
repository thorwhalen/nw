"""Tests for nw.inspect — shot_report + compose_report against real ffmpeg."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from nw import (
    Project,
    SectionSpec,
    ShotSpec,
    compose_report,
    shot_report,
)


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


pytestmark = pytest.mark.skipif(
    not _ffmpeg_available(), reason="ffmpeg/ffprobe not on PATH"
)


def _make_video(path: Path, *, duration_s: float = 3.0, frozen: bool = False) -> None:
    """Build a tiny test mp4. ``frozen=True`` produces a freeze-frame video."""
    if frozen:
        # A solid color held for `duration_s` — every frame identical.
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi",
            "-i", f"color=c=blue:size=160x120:duration={duration_s}:rate=24",
            "-f", "lavfi",
            "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100",
            "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi",
            "-i", f"testsrc=duration={duration_s}:size=160x120:rate=24",
            "-f", "lavfi",
            "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100",
            "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(path),
        ]
    subprocess.run(cmd, check=True, capture_output=True)


def _seed_project(tmp_path) -> Project:
    proj = Project.init(tmp_path / "p")
    from nw.schema import SongInfo
    song = proj.root / "song" / "song.wav"
    song.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
    proj.update_spec(song=SongInfo(audio_path="song/song.wav", duration_s=8.0))
    proj.upsert_section(SectionSpec(id="v", start_s=0.0, end_s=8.0))
    proj.upsert_shot(ShotSpec(id="s01", start_s=0.0, end_s=3.0))
    proj.upsert_shot(ShotSpec(id="s02", start_s=3.0, end_s=8.0))
    return proj


def test_shot_report_no_render_returns_unrendered(tmp_path):
    proj = _seed_project(tmp_path)
    rep = shot_report(proj, "s01")
    assert rep.duration_s == 0.0
    assert rep.has_video is False
    assert rep.duration_within_tolerance is False


def test_shot_report_correct_duration_within_tolerance(tmp_path):
    proj = _seed_project(tmp_path)
    output = proj.shot_dir("s01") / "output.mp4"
    _make_video(output, duration_s=3.0)
    rep = shot_report(proj, "s01")
    assert 2.9 <= rep.duration_s <= 3.1
    assert rep.target_duration_s == 3.0
    assert rep.duration_within_tolerance is True
    assert rep.has_video is True
    assert rep.has_audio is True
    assert rep.fps > 0
    assert rep.frame_count > 0


def test_shot_report_short_clip_flagged(tmp_path):
    """The v3 fixture symptom: rendered duration < target."""
    proj = _seed_project(tmp_path)
    output = proj.shot_dir("s01") / "output.mp4"
    _make_video(output, duration_s=1.0)  # target is 3.0
    rep = shot_report(proj, "s01")
    assert rep.duration_within_tolerance is False


def test_shot_report_detects_freeze(tmp_path):
    """Freeze detection: a solid-color video has every frame identical."""
    proj = _seed_project(tmp_path)
    output = proj.shot_dir("s01") / "output.mp4"
    _make_video(output, duration_s=3.0, frozen=True)
    rep = shot_report(proj, "s01", freeze_sample_fps=4.0)
    assert rep.has_long_freeze is True
    assert rep.freeze_total_s > 1.0
    assert len(rep.frozen_segments) >= 1


def test_shot_report_no_freeze_in_dynamic_video(tmp_path):
    """testsrc has constant motion — no freeze segments expected."""
    proj = _seed_project(tmp_path)
    output = proj.shot_dir("s01") / "output.mp4"
    _make_video(output, duration_s=3.0, frozen=False)
    rep = shot_report(proj, "s01")
    assert rep.has_long_freeze is False


def test_compose_report_aggregates_per_shot(tmp_path):
    proj = _seed_project(tmp_path)
    _make_video(proj.shot_dir("s01") / "output.mp4", duration_s=3.0)
    _make_video(proj.shot_dir("s02") / "output.mp4", duration_s=5.0)
    rep = compose_report(proj)
    assert len(rep.shots) == 2
    assert rep.has_final is False  # no compose run yet
    assert rep.target_duration_s == 8.0


def test_compose_report_freeze_alerts(tmp_path):
    proj = _seed_project(tmp_path)
    _make_video(proj.shot_dir("s01") / "output.mp4", duration_s=3.0, frozen=True)
    _make_video(proj.shot_dir("s02") / "output.mp4", duration_s=5.0, frozen=False)
    rep = compose_report(proj)
    alerts = rep.freeze_alerts
    assert len(alerts) == 1
    assert alerts[0].shot_id == "s01"


def test_compose_report_detects_gap(tmp_path):
    """Two shots with a gap between them should show up as a Gap."""
    proj = Project.init(tmp_path / "p")
    proj.upsert_shot(ShotSpec(id="s01", start_s=0.0, end_s=3.0))
    proj.upsert_shot(ShotSpec(id="s02", start_s=5.0, end_s=8.0))  # gap [3,5]
    rep = compose_report(proj)
    assert len(rep.gaps) == 1
    gap = rep.gaps[0]
    assert gap.after_shot_id == "s01"
    assert gap.before_shot_id == "s02"
    assert gap.duration_s == 2.0
