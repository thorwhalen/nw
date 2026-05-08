"""Helpers shared by render strategies — uploads, trim/pad, prompt build."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def upload_local_file(path: Path) -> str:
    """Upload ``path`` to fal.ai's storage and return the temp URL.

    fal_client must be installed; raises ImportError otherwise (with a
    clear message — most call sites need fal anyway).
    """
    try:
        import fal_client  # type: ignore[import-untyped]
    except ImportError as e:
        raise ImportError(
            "fal-client is required for nw.renderers uploads. "
            "Install with: pip install fal-client"
        ) from e
    return fal_client.upload_file(str(path))


def download_to(url: str, dst: Path) -> Path:
    """Download a URL to ``dst`` (creates parent dirs). Returns ``dst``."""
    import urllib.request

    dst.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, str(dst))
    return dst


def trim_or_pad_video(src: Path, target_s: float, dst: Path) -> Path:
    """Cut/pad a video to exactly ``target_s`` seconds.

    Mirrors ``muvid.renderers._common.trim_video_to_duration`` (Phase 0.6 fix).
    Within 50ms → copy. Longer → cut via ``mixing.video.Video``. Shorter →
    pad with held last frame + audio silence via ``ffmpeg tpad/apad``. Falls
    back to a copy on any error so the caller always gets *some* file.
    """
    try:
        from mixing.video import Video  # type: ignore[import-not-found]
        v = Video(str(src))
        delta = target_s - v.duration

        if abs(delta) < 0.05:
            if src.resolve() != dst.resolve():
                shutil.copy2(src, dst)
            return dst

        if delta < 0:
            cut = v[0:target_s]
            cut.save(str(dst))
            return dst

        return _pad_video_to_duration(src, target_s, dst, pad_seconds=delta)
    except Exception:
        shutil.copy2(src, dst)
        return dst


def _pad_video_to_duration(
    src: Path, target_s: float, dst: Path, *, pad_seconds: float
) -> Path:
    if pad_seconds <= 0:
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        return dst
    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-vf", f"tpad=stop_mode=clone:stop_duration={pad_seconds:.3f}",
        "-af", f"apad=pad_dur={pad_seconds:.3f}",
        "-t", f"{target_s:.3f}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(dst),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return dst
    except (subprocess.CalledProcessError, FileNotFoundError):
        shutil.copy2(src, dst)
        return dst


def loop_image_with_audio(
    image: Path, audio: Path, target_s: float, dst: Path
) -> Path:
    """Build an mp4 that holds ``image`` for ``target_s`` seconds with
    ``audio`` as the soundtrack. ffmpeg-only; no fal calls.
    """
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image),
        "-i", str(audio),
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        "-t", f"{target_s:.3f}",
        "-movflags", "+faststart",
        str(dst),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dst
