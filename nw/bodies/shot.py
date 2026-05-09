"""Body schema for shots — the renderable visual unit.

URI: ``annot://schema/shot/v1``

A shot is a timeline-locked visual unit with a render strategy and
references to the characters / environment in frame. The interval lives
on the annotation's :class:`lacing.MediaRef` (so it shares an interval
space with sections, lyric alignments, viseme tracks, and storyboard
panels).

The render output (``output.mp4``) is a separate :class:`lacing.Artifact`
whose ``provenance.was_derived_from`` includes this shot's annotation id.
That's what enables reelee's "what's downstream of this shot?" queries.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from lacing.schema import register_body_schema


SHOT_BODY_SCHEMA_URI = "annot://schema/shot/v1"


class ShotBodyV1(BaseModel):
    """Body of a shot annotation."""

    model_config = {"frozen": True, "extra": "forbid"}

    shot_id: str = Field(
        ..., description="Stable id within a project (e.g. 's01')."
    )
    section_id: str = Field("", description="Optional pointer to a parent section.")
    render_strategy: str = Field(
        "image_to_video",
        description=(
            'Open string — apps register their own strategies via '
            'nw.renderers. Built-ins: "lipsync", "image_to_video", '
            '"text_to_video", "still", "composite_lipsync".'
        ),
    )
    environment: str = Field(
        "",
        description=(
            "Name of the environment-ref referenced by the shot. Empty for "
            "shots that don't pin an environment."
        ),
    )
    characters: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Names of character-refs in this shot.",
    )
    description: str = Field(
        "", description="Prose direction for the shot (drives the prompt)."
    )
    camera: str = Field("", description='Camera move: "static" | "slow push-in" | …')
    framing: str = Field("medium", description='Framing: "wide" | "medium" | "close" | …')
    notes: str = Field("", description="Director's notes; not a prompt component.")


register_body_schema(SHOT_BODY_SCHEMA_URI, ShotBodyV1)
