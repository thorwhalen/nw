"""Body schema for environment refs.

URI: ``annot://schema/environment-ref/v1``

Same pattern as character-ref: small project-level pointer at an
``environments/<name>/`` folder.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from lacing.schema import register_body_schema


ENVIRONMENT_REF_BODY_SCHEMA_URI = "annot://schema/environment-ref/v1"


class EnvironmentRefBodyV1(BaseModel):
    """Body of an environment-ref annotation."""

    model_config = {"frozen": True, "extra": "forbid"}

    name: str = Field(..., description="Folder name under environments/.")
    description: str = Field("", description="Short description.")
    reference_image_urls: tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            "URLs of reference images for this environment — the lookbook "
            "the FE curates and a future ``panel_to_image.composite.*`` "
            "transform passes to the image-gen model as visual anchors "
            "for style consistency. Backwards-compatible (defaults to "
            "empty); v0.4-era dumps without this field load cleanly."
        ),
    )


register_body_schema(ENVIRONMENT_REF_BODY_SCHEMA_URI, EnvironmentRefBodyV1)
