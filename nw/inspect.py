"""QA helpers — typed reports about rendered shots.

A render finishing without an exception doesn't mean it's *right*. The v3
fixture in muvid_project rendered successfully but produced a 5.87s clip
when 8s were asked for. The four-second freeze in v4 was visible only by
extracting frames and md5'ing them. These reports surface those defects
without a manual ffprobe / ffmpeg dance.

Public surface:

- :func:`shot_report` — duration, frozen-frame segments, audio-video offset,
  whether the output is the requested length.
- :func:`compose_report` — same but for the final composed video; plus
  per-shot rollup, gaps between shots, freeze alerts.

Both return frozen Pydantic models for typed downstream use.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from .project import Project


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class FrozenSegment(BaseModel):
    """A run of consecutive frames whose pixels don't change.

    A short freeze (≤ 0.25s) is usually a model artifact; a long one (≥ 1s)
    is almost always a bug — Hailuo Pro returning a too-short clip + a tpad
    fallback that froze the last frame, etc.
    """
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


class ShotReport(BaseModel):
    """Inspection of one rendered shot."""

    model_config = {"frozen": True}

    shot_id: str
    output_path: str

    duration_s: float
    target_duration_s: float
    duration_within_tolerance: bool

    has_video: bool
    has_audio: bool

    frame_count: int
    fps: float

    frozen_segments: tuple[FrozenSegment, ...] = ()
    freeze_total_s: float = 0.0

    @property
    def has_long_freeze(self) -> bool:
        """Any freeze ≥ 1.0s is suspicious. Anything ≥ 0.5s is worth flagging."""
        return any(seg.duration_s >= 1.0 for seg in self.frozen_segments)


class Gap(BaseModel):
    """A gap on the timeline between two shots."""

    model_config = {"frozen": True}

    after_shot_id: str
    before_shot_id: str
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


class ComposeReport(BaseModel):
    """Inspection of the project-level final composed video."""

    model_config = {"frozen": True}

    output_path: Optional[str]
    has_final: bool

    total_duration_s: float
    target_duration_s: float

    shots: tuple[ShotReport, ...]
    gaps: tuple[Gap, ...] = ()

    @property
    def freeze_alerts(self) -> tuple[ShotReport, ...]:
        return tuple(s for s in self.shots if s.has_long_freeze)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def shot_report(
    project: Project,
    shot_id: str,
    *,
    freeze_sample_fps: float = 4.0,
    duration_tolerance_s: float = 0.1,
) -> ShotReport:
    """Inspect ``shots/<shot_id>/output.mp4`` and return a typed report.

    Args:
        project: The :class:`nw.Project`.
        shot_id: The shot id.
        freeze_sample_fps: How many frames per second to extract for the
            freeze detector (default 4 fps; a freeze must hold across at
            least two consecutive samples to count).
        duration_tolerance_s: Acceptable difference between actual and
            target duration before flagging.

    Returns:
        A :class:`ShotReport`.
    """
    spec = project.read_spec()
    shot = spec.shot(shot_id)
    if shot is None:
        raise KeyError(f"No shot {shot_id!r} in project {project.root}")

    output = project.shot_dir(shot_id) / "output.mp4"
    if not output.exists():
        # Emit an empty report — caller can treat this as "not yet rendered."
        return ShotReport(
            shot_id=shot_id,
            output_path=str(output),
            duration_s=0.0,
            target_duration_s=shot.duration_s,
            duration_within_tolerance=False,
            has_video=False,
            has_audio=False,
            frame_count=0,
            fps=0.0,
        )

    probe = _ffprobe(output)
    duration = probe["duration_s"]
    target = shot.duration_s
    within_tol = abs(duration - target) <= duration_tolerance_s

    frozen_segments = ()
    if probe["has_video"] and shutil.which("ffmpeg"):
        frozen_segments = tuple(
            _detect_frozen_segments(output, sample_fps=freeze_sample_fps)
        )

    freeze_total = sum(seg.duration_s for seg in frozen_segments)

    return ShotReport(
        shot_id=shot_id,
        output_path=str(output),
        duration_s=duration,
        target_duration_s=target,
        duration_within_tolerance=within_tol,
        has_video=probe["has_video"],
        has_audio=probe["has_audio"],
        frame_count=probe["frame_count"],
        fps=probe["fps"],
        frozen_segments=frozen_segments,
        freeze_total_s=freeze_total,
    )


def compose_report(
    project: Project,
    *,
    freeze_sample_fps: float = 4.0,
    duration_tolerance_s: float = 0.1,
) -> ComposeReport:
    """Per-shot reports + final-compose inspection in one call."""
    spec = project.read_spec()
    final = project.root / "output" / "final.mp4"
    has_final = final.exists()

    shots = tuple(
        shot_report(project, s.id,
                    freeze_sample_fps=freeze_sample_fps,
                    duration_tolerance_s=duration_tolerance_s)
        for s in spec.shots
    )

    gaps: list[Gap] = []
    sorted_shots = sorted(spec.shots, key=lambda s: s.start_s)
    for prev, nxt in zip(sorted_shots, sorted_shots[1:]):
        if nxt.start_s > prev.end_s + duration_tolerance_s:
            gaps.append(
                Gap(
                    after_shot_id=prev.id,
                    before_shot_id=nxt.id,
                    start_s=prev.end_s,
                    end_s=nxt.start_s,
                )
            )

    if has_final:
        final_probe = _ffprobe(final)
        total_duration = final_probe["duration_s"]
    else:
        total_duration = 0.0

    target_total = spec.song.duration_s if spec.song else (
        max((s.end_s for s in spec.shots), default=0.0)
    )

    return ComposeReport(
        output_path=str(final) if has_final else None,
        has_final=has_final,
        total_duration_s=total_duration,
        target_duration_s=target_total,
        shots=shots,
        gaps=tuple(gaps),
    )


# ---------------------------------------------------------------------------
# Internals — ffprobe + freeze detection
# ---------------------------------------------------------------------------


def _ffprobe(path: Path) -> dict:
    """Best-effort metadata probe via ffprobe. Falls back to zeros if unavailable."""
    if not shutil.which("ffprobe"):
        return {
            "duration_s": 0.0,
            "has_video": False,
            "has_audio": False,
            "frame_count": 0,
            "fps": 0.0,
        }
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_format",
                "-show_streams",
                "-of", "json",
                str(path),
            ],
            check=True, capture_output=True, text=True,
        )
        data = json.loads(out.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return {
            "duration_s": 0.0,
            "has_video": False,
            "has_audio": False,
            "frame_count": 0,
            "fps": 0.0,
        }

    duration = float(data.get("format", {}).get("duration") or 0.0)
    streams = data.get("streams", [])
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    frame_count = 0
    fps = 0.0
    for s in streams:
        if s.get("codec_type") == "video":
            fps_str = s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1"
            try:
                num, den = fps_str.split("/")
                if int(den) != 0:
                    fps = float(num) / float(den)
            except Exception:
                fps = 0.0
            try:
                frame_count = int(s.get("nb_frames") or 0)
            except Exception:
                frame_count = 0
            if frame_count == 0 and fps > 0:
                frame_count = int(round(duration * fps))
            break

    return {
        "duration_s": duration,
        "has_video": has_video,
        "has_audio": has_audio,
        "frame_count": frame_count,
        "fps": fps,
    }


def _detect_frozen_segments(
    video: Path, *, sample_fps: float = 4.0
) -> list[FrozenSegment]:
    """Extract frames at ``sample_fps`` and group consecutive identical ones.

    Identity = byte-md5 of the JPEG-encoded frame. Two consecutive samples
    with the same hash count as a frozen run; we emit a segment for runs of
    length ≥ 2 samples.

    This is the same trick the muvid_project agent had to do via shell: now
    one function call.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        # Extract sampled frames as JPEGs.
        cmd = [
            "ffmpeg", "-v", "error", "-y",
            "-i", str(video),
            "-vf", f"fps={sample_fps}",
            "-q:v", "2",
            str(td_path / "f_%05d.jpg"),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            return []

        frames = sorted(td_path.glob("f_*.jpg"))
        if len(frames) < 2:
            return []

        hashes = [hashlib.md5(p.read_bytes()).hexdigest() for p in frames]

    # Group consecutive identical hashes.
    segments: list[FrozenSegment] = []
    i = 0
    n = len(hashes)
    while i < n:
        j = i + 1
        while j < n and hashes[j] == hashes[i]:
            j += 1
        run_len = j - i
        if run_len >= 2:
            start_s = i / sample_fps
            end_s = j / sample_fps
            segments.append(FrozenSegment(start_s=start_s, end_s=end_s))
        i = j

    return segments
