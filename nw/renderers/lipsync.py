"""Strategy: lipsync — character anchor + audio → talking video.

Calls ``falaw.animate_face`` (image+audio → talking video). Defaults to
``omnihuman/v1.5`` at ``quality="high"`` because the default ``ai-avatar``
hangs reliably (see muvid_project bugs_encountered.md, 2026-05-07).

If multiple characters are present, the first one is picked and a warning is
emitted — multi-character lipsync is composite_lipsync's territory (Phase 2).

model_overrides keys understood:
  - ``avatar`` — override the ``avatar_model_id`` (e.g. omnihuman, ai-avatar).
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from falaw import Plan, plan_animate_face
from lacing import Artifact

from ._common import download_to, trim_or_pad_video
from . import register_strategy

if TYPE_CHECKING:
    from ..workflow import ShotPreparation


class LipsyncStrategy:
    """``render_strategy="lipsync"``."""

    name = "lipsync"

    def plan(
        self,
        prep: "ShotPreparation",
        *,
        quality: str = "balanced",
        model_overrides: dict[str, str] | None = None,
    ) -> Plan:
        if not prep.character_anchor_paths:
            raise RuntimeError(
                f"lipsync: shot {prep.shot_id!r} has no character anchor "
                f"image. Use Project.set_character_anchor() to pick one."
            )
        if len(prep.character_anchor_paths) > 1:
            warnings.warn(
                f"lipsync: shot {prep.shot_id!r} has multiple characters "
                f"({list(prep.character_anchor_paths)}); v0 only lipsyncs the first."
            )

        char_name = next(iter(prep.character_anchor_paths))
        # URLs are only required at execute time; planning works with
        # local-path placeholders (the executor downstream of this still
        # needs URLs, but a plan-only inspection doesn't).
        image_url = prep.character_anchor_urls.get(
            char_name, f"<plan-only:{prep.character_anchor_paths[char_name]}>"
        )
        audio_url = prep.audio_slice_url or f"<plan-only:{prep.audio_slice_path}>"

        prompt = prep.shot.description or f"{char_name} singing"
        if prep.lyric_lines:
            prompt += " — lyrics: " + " / ".join(
                L["text"] for L in prep.lyric_lines if L.get("text")
            )

        avatar_model = (model_overrides or {}).get("avatar")
        # Use quality="high" by default for lipsync — that picks omnihuman,
        # avoiding the default ai-avatar that's known to hang.
        effective_quality = "high" if quality == "balanced" else quality

        call = plan_animate_face(
            image_url=image_url,
            audio_url=audio_url,
            prompt=prompt,
            quality=effective_quality,
            model_id=avatar_model,
            duration_s=prep.duration_s,
            metadata={
                "shot_id": prep.shot_id,
                "strategy": "lipsync",
                "character": char_name,
            },
        )
        return Plan(calls=(call,))

    def materialize(
        self,
        prep: "ShotPreparation",
        plan: Plan,
        artifacts: list[Artifact],
    ) -> Path:
        artifact = artifacts[0]
        if not artifact.url:
            raise RuntimeError(
                f"lipsync: avatar call for shot {prep.shot_id!r} returned no URL"
            )
        raw = prep.shot_dir / "raw.mp4"
        download_to(artifact.url, raw)
        final = prep.shot_dir / "output.mp4"
        return trim_or_pad_video(raw, prep.duration_s, final)


register_strategy("lipsync", LipsyncStrategy())
