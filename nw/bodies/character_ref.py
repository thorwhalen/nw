"""Body schema for character refs — pointers to a character folder.

URI: ``annot://schema/character-ref/v1``

A character-ref is the project-level *pointer* at a character folder
(``characters/<name>/``). The folder holds the canonical card.json,
reference images, voice samples. The annotation's body is intentionally
small: a name and a description. Anything richer lives in the folder.

Why a body schema rather than just a project.json field: with this
annotation, reelee can answer "what's downstream of this character"
across the whole graph without parsing project.json — the character-ref
annotation is the parent node in the provenance graph.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from lacing.schema import register_body_schema


CHARACTER_REF_BODY_SCHEMA_URI = "annot://schema/character-ref/v1"


class CharacterRefBodyV1(BaseModel):
    """Body of a character-ref annotation."""

    model_config = {"frozen": True, "extra": "forbid"}

    name: str = Field(..., description="Folder name under characters/.")
    description: str = Field(
        "", description="Short description; canonical card lives in card.json."
    )
    reference_image_urls: list[str] = Field(
        default_factory=list,
        description=(
            "URLs of reference images for this character — the lookbook "
            "the FE curates and a future ``panel_to_image.composite.*`` "
            "transform passes to the image-gen model as visual anchors "
            "for style consistency. Backwards-compatible (defaults to "
            "empty); v0.4-era dumps without this field load cleanly."
        ),
    )


register_body_schema(CHARACTER_REF_BODY_SCHEMA_URI, CharacterRefBodyV1)
