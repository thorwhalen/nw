"""Body schema for the project's resolved genre envelope.

URI: ``annot://schema/genre-envelope/v1``

The persisted form of the creation envelope :func:`nw.genres.resolve_genre`
returns — ``{genre, template, params}`` — so "what genre/template/params
created this project?" stays answerable after the create call returns
(nw#32). Before this schema existed the envelope went to the *caller* and
nowhere else: reopen the project tomorrow and nothing in it said what genre
it was, so nothing downstream (planner scoping, genre presets on a later
run, a host aggregating another app's genre) could be genre-conditioned.

Why an annotation rather than a ``ProjectSpec`` field
-----------------------------------------------------
``project.json`` is deliberately round-trip-compatible with muvid's
``ProjectSpec`` for ``schema_version=1`` (nw#30), and its
``extra="ignore"`` means a foreign reader's load/save cycle would silently
*drop* an unmodelled genre key — failing quietly, the worst failure shape.
The graph is nw's SSOT direction, carries provenance for free, and the
precedent (:mod:`nw.bodies.decision` — a timeless, project-local, typed
record under a sentinel zero-duration reference) already exists. Same
shape here, singleton per project: stored under the ``genre-envelope``
tier, replaced in place on re-initialization.

``params`` is the *resolved* payload — the effective values after template
and defaults merged — and stays opaque to the substrate, exactly as in
:class:`nw.genres.Template`: the app that owns the genre gives it meaning.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from lacing.schema import register_body_schema


GENRE_ENVELOPE_BODY_SCHEMA_URI = "annot://schema/genre-envelope/v1"
GENRE_ENVELOPE_TIER = "genre-envelope"


class GenreEnvelopeBodyV1(BaseModel):
    """Body of the (singleton) genre-envelope annotation.

    Field-for-field the :func:`nw.genres.resolve_genre` envelope, so the
    persisted record and the creation-time contract can never drift apart.
    """

    model_config = {"frozen": True, "extra": "forbid"}

    genre: str = Field(..., description="The genre slug the project was created as.")
    template: Optional[str] = Field(
        default=None,
        description="The chosen template slug, or None for 'start from scratch'.",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "The *resolved* creation params — template + defaults merged. "
            "Opaque to nw; the genre's owning app interprets them."
        ),
    )


register_body_schema(GENRE_ENVELOPE_BODY_SCHEMA_URI, GenreEnvelopeBodyV1)
