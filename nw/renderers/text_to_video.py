"""Strategy: text_to_video — prompt-only short clip.

Single-call: ``falaw.text_to_video``. No image inputs.

model_overrides keys understood:
  - ``text_to_video`` — t2v model (e.g. seedance, veo3).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from falaw import (
    CallPlan,
    Plan,
    make_call_plan,
)
from falaw.cost import estimate_call_cost
from falaw.registry import get_model, pick_model
from lacing import Artifact

from ._common import download_to, trim_or_pad_video
from . import register_strategy

if TYPE_CHECKING:
    from ..workflow import ShotPreparation


class TextToVideoStrategy:
    """``render_strategy="text_to_video"``."""

    name = "text_to_video"

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
                f"text_to_video: shot {prep.shot_id!r} has no description."
            )
        overrides = model_overrides or {}
        model_id = overrides.get("text_to_video")
        if model_id:
            record = get_model(model_id)
        else:
            record = pick_model(category="text_to_video", quality_tier=quality)
        cost = estimate_call_cost(record, seconds=prep.duration_s)

        call = make_call_plan(
            tool="text_to_video",
            application=record.id,
            arguments={"prompt": prompt},
            output_kind="video",
            estimated_cost_usd=cost,
            metadata={"shot_id": prep.shot_id, "strategy": "text_to_video"},
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
                f"text_to_video: call for shot {prep.shot_id!r} returned no URL"
            )
        raw = prep.shot_dir / "raw.mp4"
        download_to(artifact.url, raw)
        final = prep.shot_dir / "output.mp4"
        return trim_or_pad_video(raw, prep.duration_s, final)


register_strategy("text_to_video", TextToVideoStrategy())
