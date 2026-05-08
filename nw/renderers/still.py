"""Strategy: still — image looped over audio (no fal video gen).

Two paths:

- If the shot has an environment anchor or a character anchor on disk, no
  image-gen call is needed. The strategy returns a Plan with **zero** fal
  calls (cost = 0); :meth:`materialize` just runs ffmpeg locally to loop
  the image over the audio slice.
- If neither anchor is set, plans one ``generate_image`` call to make a
  fresh storyboard still, then loops it locally.

This is the cheapest strategy — useful for sections where motion would be
distracting, or for placeholder rendering during development.

model_overrides keys understood:
  - ``image`` — image-gen model when generating a fresh still.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from falaw import Plan, plan_generate_image
from lacing import Artifact

from ._common import download_to, loop_image_with_audio
from . import register_strategy

if TYPE_CHECKING:
    from ..workflow import ShotPreparation


class StillStrategy:
    """``render_strategy="still"``."""

    name = "still"

    def plan(
        self,
        prep: "ShotPreparation",
        *,
        quality: str = "balanced",
        model_overrides: dict[str, str] | None = None,
    ) -> Plan:
        if prep.environment_anchor_path or prep.character_anchor_paths:
            # No fal call needed — local image + ffmpeg only.
            return Plan(calls=())

        prompt = prep.storyboard_prompt or prep.shot.description
        if not prompt:
            raise RuntimeError(
                f"still: shot {prep.shot_id!r} has no anchor and no prompt; "
                f"can't generate a fresh still."
            )
        overrides = model_overrides or {}
        call = plan_generate_image(
            prompt=prompt,
            quality=quality,
            image_size="landscape_16_9",
            model_id=overrides.get("image"),
            metadata={"shot_id": prep.shot_id, "strategy": "still"},
        )
        return Plan(calls=(call,))

    def materialize(
        self,
        prep: "ShotPreparation",
        plan: Plan,
        artifacts: list[Artifact],
    ) -> Path:
        # Pick the still: env image > character image (first) > generated.
        still_path: Path
        if prep.environment_anchor_path is not None:
            still_path = prep.environment_anchor_path
        elif prep.character_anchor_paths:
            still_path = next(iter(prep.character_anchor_paths.values()))
        elif artifacts and artifacts[0].url:
            still_path = prep.shot_dir / "storyboard.png"
            download_to(artifacts[0].url, still_path)
        else:
            raise RuntimeError(
                f"still: no still available for shot {prep.shot_id!r} "
                "(no anchor, no generated image)."
            )

        final = prep.shot_dir / "output.mp4"
        return loop_image_with_audio(
            still_path, prep.audio_slice_path, prep.duration_s, final
        )


register_strategy("still", StillStrategy())
