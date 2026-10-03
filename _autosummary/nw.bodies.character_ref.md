# nw.bodies.character_ref

Body schema for character refs — pointers to a character folder.

URI: `annot://schema/character-ref/v1`

A character-ref is the project-level *pointer* at a character folder
(`characters/<name>/`). The folder holds the canonical card.json,
reference images, voice samples. The annotation’s body carries the
*identity-stable* description of the character — the facts that have to be
re-asserted in every prompt that depicts them (costume, palette,
distinguishing features) — while anything bulkier lives in the folder.

Why a body schema rather than just a project.json field: with this
annotation, reelee can answer “what’s downstream of this character”
across the whole graph without parsing project.json — the character-ref
annotation is the parent node in the provenance graph.

**One vocabulary across the ecosystem.** The stable-attribute fields below
are named to match `artful.schema.ModelSheet`, which already models the
same concepts for a *rendered* model sheet. A character-ref is the
textual/authorial side and a model sheet is the rendered side of the same
character, so `palette_anchors`, `distinguishing_features` and
`do_not_do` are deliberately identical in name *and* type. The one
concept that does **not** overlap is costume: `ModelSheet.costume_set`
maps a costume label to a *render-result annotation id*, whereas a
character-ref needs the costume as prose a prompt builder can inject —
hence `CharacterRefBodyV1.costume` (a `str`), not `costume_set`.
That is a difference of kind, not a naming drift.

### Classes

| [`CharacterRefBodyV1`](#nw.bodies.character_ref.CharacterRefBodyV1)(\*\*data)   | Body of a character-ref annotation.   |
|---------------------------------------------------------------------------------|---------------------------------------|

### *class* nw.bodies.character_ref.CharacterRefBodyV1(\*\*data)

Bases: `BaseModel`

Body of a character-ref annotation.

Every field beyond `name` is optional with a benign default, so dumps
written by any earlier version of this schema load unchanged — this is
an **additive** enrichment of v1, not a new version, and needs no
lacing migration.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].
