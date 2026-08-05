"""Body schema for character refs — pointers to a character folder.

URI: ``annot://schema/character-ref/v1``

A character-ref is the project-level *pointer* at a character folder
(``characters/<name>/``). The folder holds the canonical card.json,
reference images, voice samples. The annotation's body carries the
*identity-stable* description of the character — the facts that have to be
re-asserted in every prompt that depicts them (costume, palette,
distinguishing features) — while anything bulkier lives in the folder.

Why a body schema rather than just a project.json field: with this
annotation, reelee can answer "what's downstream of this character"
across the whole graph without parsing project.json — the character-ref
annotation is the parent node in the provenance graph.

**One vocabulary across the ecosystem.** The stable-attribute fields below
are named to match ``artful.schema.ModelSheet``, which already models the
same concepts for a *rendered* model sheet. A character-ref is the
textual/authorial side and a model sheet is the rendered side of the same
character, so ``palette_anchors``, ``distinguishing_features`` and
``do_not_do`` are deliberately identical in name *and* type. The one
concept that does **not** overlap is costume: ``ModelSheet.costume_set``
maps a costume label to a *render-result annotation id*, whereas a
character-ref needs the costume as prose a prompt builder can inject —
hence :attr:`CharacterRefBodyV1.costume` (a ``str``), not ``costume_set``.
That is a difference of kind, not a naming drift.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from lacing.schema import register_body_schema


CHARACTER_REF_BODY_SCHEMA_URI = "annot://schema/character-ref/v1"


class CharacterRefBodyV1(BaseModel):
    """Body of a character-ref annotation.

    Every field beyond ``name`` is optional with a benign default, so dumps
    written by any earlier version of this schema load unchanged — this is
    an **additive** enrichment of v1, not a new version, and needs no
    lacing migration.
    """

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

    # -- stable attributes (aligned to artful.ModelSheet) --------------------

    costume: str = Field(
        "",
        description=(
            "The character's default wardrobe, as prose a prompt builder "
            'can inject verbatim — "grey tweed jacket, green flat cap". '
            "Deliberately a string, not ``ModelSheet.costume_set`` (which "
            "maps a costume label to a render-result annotation id): this "
            "is the authorial description, that one is the rendered sheet."
        ),
    )
    age: str = Field(
        "",
        description=(
            'Free-text age or life-stage — "late 50s", "child", "elder". '
            "Free text rather than an int because it is a prompt slot, and "
            'because "weathered late 50s" carries more than a number does.'
        ),
    )
    default_setting: str = Field(
        "",
        description=(
            "Environment affinity — where this character is normally found. "
            "A prompt-slot fallback for panels that name no environment; it "
            "does not override an explicit environment-ref."
        ),
    )
    distinguishing_features: tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            'Free-text feature notes — "left eye scar", "red bandana". '
            "Same name, type and meaning as "
            "``artful.ModelSheet.distinguishing_features``."
        ),
    )
    palette_anchors: tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            "Colour anchors for this character (hex or named). Same name, "
            "type and meaning as ``artful.ModelSheet.palette_anchors``."
        ),
    )
    do_not_do: tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            "Negative directives scoped to this character — things a "
            "depiction of them must never contain. Same name and type as "
            "``artful.ModelSheet.do_not_do``. "
            "**Scope is deliberately unspecified here.** nw types the field "
            "and persists it; it does not prescribe how a consumer applies "
            "it. Whether a negation is re-asserted on every prompt that "
            "references the character, or only on prompts touching the "
            "attribute it negates, is an open owner decision — and the two "
            "readings differ in cost and in drift behaviour. Until it is "
            "settled a consumer picking a policy owns that choice; nw does "
            "not imply one."
        ),
    )


register_body_schema(CHARACTER_REF_BODY_SCHEMA_URI, CharacterRefBodyV1)
