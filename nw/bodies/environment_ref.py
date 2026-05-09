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


register_body_schema(ENVIRONMENT_REF_BODY_SCHEMA_URI, EnvironmentRefBodyV1)
