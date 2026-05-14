"""Body schema for render results — the output of a render Transform.

URI: ``annot://schema/render-result/v1``

A render-result records that a shot was rendered: which strategy ran, where
the output landed, the video :class:`lacing.Artifact` it produced, and the
estimated cost. Its ``provenance.was_derived_from`` includes the shot
annotation's id, so a freshness traversal from the shot finds the render.

This is the ``output_kind`` of the render-strategy Transforms (see
``nw.transforms._adapters.render_strategy``). It is intentionally small —
the heavy data (the actual mp4) is the referenced Artifact, not the body.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from lacing.schema import register_body_schema


RENDER_RESULT_BODY_SCHEMA_URI = "annot://schema/render-result/v1"
RENDER_RESULT_TIER = "render-result"


class RenderResultBodyV1(BaseModel):
    """Body of a render-result annotation."""

    model_config = {"frozen": True, "extra": "forbid"}

    shot_id: str = Field(..., description="Id of the shot that was rendered.")
    strategy: str = Field(
        ..., description="Render strategy / Transform that produced this result."
    )
    output_path: str = Field(
        "",
        description=(
            "Project-relative path to the rendered ``output.mp4``. Empty in a "
            "skeleton annotation produced by ``plan``; filled by ``execute``."
        ),
    )
    artifact_id: Optional[str] = Field(
        None,
        description=(
            "``asset_id`` of the video :class:`lacing.Artifact`. ``None`` in a "
            "skeleton; filled by ``execute`` once the Plan has run."
        ),
    )
    duration_s: float = Field(
        0.0, description="Target duration of the shot, in seconds."
    )
    total_estimated_cost_usd: float = Field(
        0.0,
        description="Plan-time cost estimate for the render (sum of CallPlan costs).",
    )


register_body_schema(RENDER_RESULT_BODY_SCHEMA_URI, RenderResultBodyV1)
