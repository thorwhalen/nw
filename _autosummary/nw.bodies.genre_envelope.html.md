# nw.bodies.genre_envelope

Body schema for the project’s resolved genre envelope.

URI: `annot://schema/genre-envelope/v1`

The persisted form of the creation envelope `nw.genres.resolve_genre()`
returns — `{genre, template, params}` — so “what genre/template/params
created this project?” stays answerable after the create call returns
(nw#32). Before this schema existed the envelope went to the *caller* and
nowhere else: reopen the project tomorrow and nothing in it said what genre
it was, so nothing downstream (planner scoping, genre presets on a later
run, a host aggregating another app’s genre) could be genre-conditioned.

## Why an annotation rather than a `ProjectSpec` field

`project.json` is deliberately round-trip-compatible with muvid’s
`ProjectSpec` for `schema_version=1` (nw#30), and its
`extra="ignore"` means a foreign reader’s load/save cycle would silently
*drop* an unmodelled genre key — failing quietly, the worst failure shape.
The graph is nw’s SSOT direction, carries provenance for free, and the
precedent ([`nw.bodies.decision`](nw.bodies.decision.html.md#module-nw.bodies.decision) — a timeless, project-local, typed
record under a sentinel zero-duration reference) already exists. Same
shape here, singleton per project: stored under the `genre-envelope`
tier, replaced in place on re-initialization.

`params` is the *resolved* payload — the effective values after template
and defaults merged — and stays opaque to the substrate, exactly as in
[`nw.genres.Template`](nw.html.md#nw.Template): the app that owns the genre gives it meaning.

### Classes

| [`GenreEnvelopeBodyV1`](#nw.bodies.genre_envelope.GenreEnvelopeBodyV1)(\*\*data)   | Body of the (singleton) genre-envelope annotation.   |
|----------------------------------------------------------------------------------|------------------------------------------------------|

### *class* nw.bodies.genre_envelope.GenreEnvelopeBodyV1(\*\*data)

Bases: `BaseModel`

Body of the (singleton) genre-envelope annotation.

Field-for-field the `nw.genres.resolve_genre()` envelope, so the
persisted record and the creation-time contract can never drift apart.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].
