"""The checks nw itself ships — a small, honest default menu.

These are the three that need nothing nw does not already shell out to, and
they are here because each of them has already caught a real defect in a
finished film:

* **a missing stream** — a render that produced a video track and no audio, or
  an audio track and no video, and returned a path either way;
* **an encode that stopped early** — a ten-minute cut killed by the OOM killer
  two-thirds of the way through, whose container still reported the full
  duration, because the container takes its duration from the *audio* stream.
  Every duration check passed. Counting frames is what catches it;
* **a long freeze** — a model returning a too-short clip and a ``tpad``
  fallback holding the last frame for four seconds, which looks exactly like a
  deliberate hold until you measure it.

They are registered at import of :mod:`nw`, so they are on the menu. They are
not *run* by anything: see :mod:`nw.validation` on why validation is placed by
a caller and never assumed.

Every check here is built on :mod:`nw.inspect`, which is the older, direct form
of the same knowledge. That module stays: a caller who wants one typed report
about one shot should keep calling ``shot_report``. These wrap it for callers
who want a *selection* of checks scheduled and reported together.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .validation import Check, Finding, register_check

__all__ = ["as_path", "register_builtin_checks"]

#: A frame count this far below ``duration x fps`` is a stopped encode rather
#: than the ordinary case of an audio tail outlasting the picture. Measured: a
#: finished 59 s short runs ~23 frames (0.77 s) short because its narration ends
#: after its last panel; a killed encode was short by minutes.
_SHORT_ENCODE_TOLERANCE_S = 2.0

#: Below this a still frame is a hold; above it, it is a symptom.
_LONG_FREEZE_S = 1.0


def as_path(target: Any) -> Path:
    """The media file a target refers to.

    Checks are handed whatever the caller validates. These built-ins want a
    file, so they accept a path, a string, or anything with a ``path`` or
    ``output_path`` attribute — and say so plainly when they get none, rather
    than reporting a clean bill of health on something they never opened.
    """
    for attr in ("output_path", "path"):
        if hasattr(target, attr):
            target = getattr(target, attr)
            break
    if isinstance(target, (str, Path)):
        return Path(target)
    raise TypeError(
        f"{type(target).__name__} is not a media file and has no .path or "
        f".output_path; these checks validate a rendered file"
    )


def _probe(target: Any, _ctx: Mapping[str, Any]) -> tuple[list[Finding], dict]:
    from .inspect import _ffprobe

    path = as_path(target)
    if not path.exists():
        return (
            [
                Finding(
                    check="media.streams_present",
                    severity="error",
                    message="the file does not exist",
                    where=str(path),
                    remedy="check the render actually wrote where you are looking",
                )
            ],
            {},
        )
    probe = _ffprobe(path)
    findings = []
    for kind in ("video", "audio"):
        if not probe[f"has_{kind}"]:
            findings.append(
                Finding(
                    check="media.streams_present",
                    severity="error",
                    message=f"the file has no {kind} stream",
                    where=str(path),
                    remedy=(
                        f"the {kind} side of the render failed silently — "
                        f"ffmpeg returned 0 and wrote a file anyway"
                    ),
                    evidence=dict(probe),
                )
            )
    return findings, probe


def _encode_complete(target: Any, ctx: Mapping[str, Any]) -> tuple[list[Finding], None]:
    probe = ctx.get("media.streams_present") or {}
    if not probe.get("has_video") or not probe.get("fps"):
        return [], None
    path = as_path(target)
    expected = probe["duration_s"] * probe["fps"]
    actual = probe["frame_count"]
    if not actual:  # some containers do not carry nb_frames
        actual = _count_frames(path)
    if not actual:
        return (
            [
                Finding(
                    check="media.encode_complete",
                    severity="warn",
                    message="could not count video frames, so completeness is unknown",
                    where=str(path),
                    remedy="unknown is not a pass — count them another way before publishing",
                )
            ],
            None,
        )
    short_s = (expected - actual) / probe["fps"]
    if short_s > _SHORT_ENCODE_TOLERANCE_S:
        return (
            [
                Finding(
                    check="media.encode_complete",
                    severity="error",
                    message=(
                        f"the video stream is {short_s:.1f}s shorter than the file: "
                        f"{actual} frames where {expected:.0f} were expected"
                    ),
                    where=str(path),
                    remedy=(
                        "the encode did not finish — the container takes its duration "
                        "from the audio stream, so duration alone will not show this. "
                        "Re-encode, and check for an OOM kill"
                    ),
                    evidence={"frames": actual, "expected": round(expected), **probe},
                )
            ],
            None,
        )
    return [], None


def _count_frames(path: Path) -> int:
    import json
    import subprocess

    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-count_frames",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=nb_read_frames",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return int(json.loads(out.stdout)["streams"][0]["nb_read_frames"])
    except Exception:
        return 0


def _no_long_freeze(target: Any, ctx: Mapping[str, Any]) -> tuple[list[Finding], None]:
    from .inspect import _detect_frozen_segments

    probe = ctx.get("media.streams_present") or {}
    if not probe.get("has_video"):
        return [], None
    path = as_path(target)
    findings = [
        Finding(
            check="media.no_long_freeze",
            severity="error" if seg.duration_s >= 2 * _LONG_FREEZE_S else "warn",
            message=f"the picture is frozen for {seg.duration_s:.1f}s",
            where=f"{seg.start_s:.1f}s–{seg.end_s:.1f}s",
            remedy=(
                "usually a clip that came back shorter than asked for, held by a "
                "tpad fallback — check the shot's source, not the compose step"
            ),
            evidence={"start_s": seg.start_s, "end_s": seg.end_s},
        )
        for seg in _detect_frozen_segments(path)
        if seg.duration_s >= _LONG_FREEZE_S
    ]
    return findings, None


def register_builtin_checks() -> None:
    """Put nw's own checks on the menu. Idempotent."""
    from .validation import checks as registry

    if "media.streams_present" in registry:
        return

    register_check(
        Check(
            name="media.streams_present",
            summary="the file exists and has the video and audio streams it should",
            run=_probe,
            cost="cheap",
            requires_binaries=("ffprobe",),
            example_requests=(
                "is the video ok",
                "did the render actually work",
                "there is no sound",
                "the file will not play",
            ),
        )
    )
    register_check(
        Check(
            name="media.encode_complete",
            summary="the video stream runs the whole length of the file",
            run=_encode_complete,
            requires=("media.streams_present",),
            cost="cheap",
            requires_binaries=("ffprobe",),
            example_requests=(
                "the video stops early",
                "it cuts off before the end",
                "did the encode finish",
                "the last minute is missing",
            ),
        )
    )
    register_check(
        Check(
            name="media.no_long_freeze",
            summary="no run of identical frames long enough to read as a fault",
            run=_no_long_freeze,
            requires=("media.streams_present",),
            cost="dear",  # decodes the whole file
            parallel_safe=False,
            requires_binaries=("ffmpeg",),
            example_requests=(
                "the picture freezes",
                "it gets stuck on one frame",
                "the image stops moving",
            ),
        )
    )
