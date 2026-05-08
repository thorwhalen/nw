"""Strategy: image_to_video — env or fresh-storyboard still → animated clip.

Two-call workflow:

1. If the shot has an environment anchor, use it as the i2v seed (no image
   gen call needed). Otherwise, generate a fresh storyboard still via
   ``falaw.generate_image``.
2. Animate the seed with ``falaw.image_to_video``.

A future ``seed`` parameter (see interface_design_plan item D) will let
callers force the character anchor as the seed. For now, env > fresh-still.

model_overrides keys understood:
  - ``image``          — image-gen model when generating a fresh still.
  - ``image_to_video`` — i2v model (e.g. hailuo, kling, seedance).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from falaw import Plan, plan_generate_image, plan_image_to_video
from lacing import Artifact

from ._common import download_to, trim_or_pad_video, upload_local_file
from . import register_strategy

if TYPE_CHECKING:
    from ..workflow import ShotPreparation


class ImageToVideoStrategy:
    """``render_strategy="image_to_video"``."""

    name = "image_to_video"

    def plan(
        self,
        prep: "ShotPreparation",
        *,
        quality: str = "balanced",
        model_overrides: dict[str, str] | None = None,
    ) -> Plan:
        prompt = prep.storyboard_prompt or prep.shot.description
        if not prompt:
            raise RuntimeError(
                f"image_to_video: shot {prep.shot_id!r} has no description "
                f"or storyboard prompt to drive image gen."
            )
        overrides = model_overrides or {}

        calls: list = []
        if prep.environment_anchor_url:
            # Env image already uploaded by prepare_shot — i2v directly.
            seed_url_marker = prep.environment_anchor_url
        else:
            # Generate a still first; the executor rewrites "<from 0>" in the
            # next call's args to the still's URL once it's materialized.
            calls.append(
                plan_generate_image(
                    prompt=prompt,
                    quality=quality,
                    image_size="landscape_16_9",
                    model_id=overrides.get("image"),
                    metadata={"shot_id": prep.shot_id, "strategy": "image_to_video", "step": "still"},
                )
            )
            seed_url_marker = "<from 0>"

        i2v_call = plan_image_to_video(
            image_url=seed_url_marker,
            prompt=prompt,
            quality=quality,
            model_id=overrides.get("image_to_video"),
            duration_s=prep.duration_s,
            extra={"duration": max(1, int(round(prep.duration_s)))},
            metadata={"shot_id": prep.shot_id, "strategy": "image_to_video", "step": "motion"},
        )
        calls.append(i2v_call)
        return Plan(calls=tuple(calls))

    def materialize(
        self,
        prep: "ShotPreparation",
        plan: Plan,
        artifacts: list[Artifact],
    ) -> Path:
        # The last artifact is always the i2v result.
        i2v_artifact = artifacts[-1]
        if not i2v_artifact.url:
            raise RuntimeError(
                f"image_to_video: i2v call for shot {prep.shot_id!r} returned no URL"
            )
        # If an intermediate still was generated, persist it for inspection.
        if len(artifacts) == 2:
            still_artifact = artifacts[0]
            if still_artifact.url:
                download_to(still_artifact.url, prep.shot_dir / "storyboard.png")

        raw = prep.shot_dir / "raw.mp4"
        download_to(i2v_artifact.url, raw)
        final = prep.shot_dir / "output.mp4"
        return trim_or_pad_video(raw, prep.duration_s, final)


register_strategy("image_to_video", ImageToVideoStrategy())
