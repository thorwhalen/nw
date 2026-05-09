"""Body schema for timeline sections (verse, chorus, scene-1, …).

URI: ``annot://schema/section/v1``

A section is a labeled span of a project's master timeline. Sections are
typically non-overlapping but the schema doesn't enforce that — apps can
encode their own constraints.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from lacing.schema import register_body_schema


SECTION_BODY_SCHEMA_URI = "annot://schema/section/v1"


class SectionBodyV1(BaseModel):
    """Body of a section annotation."""

    model_config = {"frozen": True, "extra": "forbid"}

    section_id: str = Field(
        ...,
        description=(
            "Stable id within a project (e.g. 'verse', 'scene-1'). "
            "Distinct from the annotation id."
        ),
    )
    label: str = Field("", description='Free-form: "verse", "chorus", "scene-1"…')
    energy: str = Field("", description='Free-form: "low" | "medium" | "high" | …')
    mood: str = Field("", description="Free-form mood hint.")


register_body_schema(SECTION_BODY_SCHEMA_URI, SectionBodyV1)
