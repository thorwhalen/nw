"""Strategy: composite_lipsync — character + environment + audio → talking video.

The keystone deliverable from interface_design_plan.md item E. Two-call plan:

1. ``composite_character_in_environment`` (Flux Kontext): take the character
   anchor and the environment anchor, produce one composited still where the
   character is *in* the environment.
2. ``animate_face`` (omnihuman): take that composited still + the audio slice,
   produce a lipsynced talking video.

The second call references the first via the ``<from 0>`` placeholder, so the
Plan is self-contained — caller can inspect ``plan.total_cost_usd`` before
either call fires.

Failure modes handled at plan time:
- No character anchor → plan() raises with a clear message.
- No environment anchor → plan() raises (composite needs both inputs).

For the case where the user has only a character (no environment), use the
plain ``lipsync`` strategy, which lipsyncs the character image directly.

model_overrides keys understood:
  - ``image_edit`` — override the composite model (e.g. flux-pro/kontext/max).
  - ``avatar``     — override the lipsync model.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from falaw import (
    Plan,
    plan_animate_face,
    plan_composite_character_in_environment,
)
from lacing import Artifact

from ._common import download_to, trim_or_pad_video, upload_local_file
from . import register_strategy

if TYPE_CHECKING:
    from ..workflow import ShotPreparation


class CompositeLipsyncStrategy:
    """``render_strategy="composite_lipsync"``.

    The "Thor in a bell tower playing piano, lipsynced to the song" strategy.
    """

    name = "composite_lipsync"

    def plan(
        self,
        prep: "ShotPreparation",
        *,
        quality: str = "balanced",
        model_overrides: dict[str, str] | None = None,
    ) -> Plan:
        if not prep.character_anchor_paths:
            raise RuntimeError(
                f"composite_lipsync: shot {prep.shot_id!r} has no character anchor. "
                "Use Project.set_character_anchor() to pick one."
            )
        if prep.environment_anchor_path is None:
            raise RuntimeError(
                f"composite_lipsync: shot {prep.shot_id!r} has no environment anchor. "
                "Either set shot.environment to a project environment with an "
                "establishing image, or use the plain 'lipsync' strategy "
                "(which works without an environment)."
            )

        char_name = next(iter(prep.character_anchor_paths))
        # URL fields are only needed at execute time; in plan-only mode the
        # placeholder strings just stand in for "the planner saw the local
        # path; the real URL will be filled in at execute time." If the user
        # built `prep` with upload=True, we use the actual URLs.
        char_url = prep.character_anchor_urls.get(
            char_name, f"<plan-only:{prep.character_anchor_paths[char_name]}>"
        )
        env_url = prep.environment_anchor_url or (
            f"<plan-only:{prep.environment_anchor_path}>"
        )
        audio_url = prep.audio_slice_url or f"<plan-only:{prep.audio_slice_path}>"

        overrides = model_overrides or {}
        compose_quality = quality
        avatar_quality = (
            "high" if quality == "balanced" else quality
        )  # default omnihuman

        # Per-shot composition prompt: include the shot's framing/action so
        # Kontext does more than just "place the person there" — it places
        # them doing the right thing.
        shot_action = prep.shot.description or f"{char_name} in the scene"
        compose_prompt = (
            f"Place the person from the first image into the scene from the "
            f"second image. Preserve the person's identity exactly (face, hair, "
            f"build, age, clothing). Match the environment's lighting, palette, "
            f"and atmosphere. The person is: {shot_action}."
        )
        if prep.shot.framing:
            compose_prompt += f" Framing: {prep.shot.framing}."
        if prep.shot.camera:
            compose_prompt += f" Camera: {prep.shot.camera}."
        if prep.global_style:
            compose_prompt += f" Style: {prep.global_style}."

        compose_call = plan_composite_character_in_environment(
            character_image_url=char_url,
            environment_image_url=env_url,
            prompt=compose_prompt,
            quality=compose_quality,
            model_id=overrides.get("image_edit"),
            metadata={
                "shot_id": prep.shot_id,
                "strategy": "composite_lipsync",
                "step": "composite",
                "character": char_name,
            },
        )

        # Lipsync prompt: lyric lines steer the omnihuman delivery.
        lipsync_prompt = prep.shot.description or f"{char_name} singing"
        if prep.lyric_lines:
            lipsync_prompt += " — lyrics: " + " / ".join(
                L["text"] for L in prep.lyric_lines if L.get("text")
            )

        avatar_call = plan_animate_face(
            image_url="<from 0>",  # rewritten to compose_call's URL at execute time
            audio_url=audio_url,
            prompt=lipsync_prompt,
            quality=avatar_quality,
            model_id=overrides.get("avatar"),
            duration_s=prep.duration_s,
            metadata={
                "shot_id": prep.shot_id,
                "strategy": "composite_lipsync",
                "step": "lipsync",
                "character": char_name,
            },
        )

        return Plan(calls=(compose_call, avatar_call))

    def materialize(
        self,
        prep: "ShotPreparation",
        plan: Plan,
        artifacts: list[Artifact],
    ) -> Path:
        # Persist the intermediate composite still for inspection.
        compose_artifact, avatar_artifact = artifacts
        if compose_artifact.url:
            download_to(compose_artifact.url, prep.shot_dir / "composite.png")
        if not avatar_artifact.url:
            raise RuntimeError(
                f"composite_lipsync: avatar call for shot {prep.shot_id!r} returned no URL"
            )
        raw = prep.shot_dir / "raw.mp4"
        download_to(avatar_artifact.url, raw)
        final = prep.shot_dir / "output.mp4"
        return trim_or_pad_video(raw, prep.duration_s, final)


register_strategy("composite_lipsync", CompositeLipsyncStrategy())
