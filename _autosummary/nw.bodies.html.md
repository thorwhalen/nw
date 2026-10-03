# nw.bodies

Body schemas for nw’s project-graph annotations.

Importing this package registers the schemas with lacing so any annotation
with a matching `body_schema_uri` validates correctly. The schemas are:

- `annot://schema/section/v1`        — timeline section (verse, scene-1, …)
- `annot://schema/shot/v1`           — renderable visual unit
- `annot://schema/character-ref/v1`  — pointer to a character folder
- `annot://schema/environment-ref/v1` — pointer to an environment folder
- `annot://schema/decision/v1`       — provenance-rich decision log entry
- `annot://schema/render-result/v1`  — output of a render Transform
- `annot://schema/verifying-trace/v1` — upstream value digests, for early cutoff
- `annot://schema/unproduced-output/v1` — why a planned output was never
  produced (nw#44), retired by a later successful retry
- `annot://schema/genre-envelope/v1` — the resolved {genre, template, params}
  the project was created as (singleton per project)

These are deliberately small and project-agnostic. Reelee will be able to
walk the same graph for freshness analysis (“what’s downstream of this
character description?”) without bespoke storage.

### Functions

| [`build_verifying_trace`](#nw.bodies.build_verifying_trace)(\*, for_annotation_id, ...)   | Build the trace annotation for one derived annotation, or `None`.   |
|------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------|

### Classes

| [`CharacterRefBodyV1`](#nw.bodies.CharacterRefBodyV1)(\*\*data)     | Body of a character-ref annotation.                 |
|-----------------------------------------------------------------------------------|-----------------------------------------------------|
| [`DecisionBodyV1`](#nw.bodies.DecisionBodyV1)(\*\*data)         | Body of a decision annotation.                      |
| [`EnvironmentRefBodyV1`](#nw.bodies.EnvironmentRefBodyV1)(\*\*data)   | Body of an environment-ref annotation.              |
| [`GenreEnvelopeBodyV1`](#nw.bodies.GenreEnvelopeBodyV1)(\*\*data)    | Body of the (singleton) genre-envelope annotation.  |
| [`RenderResultBodyV1`](#nw.bodies.RenderResultBodyV1)(\*\*data)     | Body of a render-result annotation.                 |
| [`SectionBodyV1`](#nw.bodies.SectionBodyV1)(\*\*data)          | Body of a section annotation.                       |
| [`ShotBodyV1`](#nw.bodies.ShotBodyV1)(\*\*data)             | Body of a shot annotation.                          |
| [`UnproducedOutputBodyV1`](#nw.bodies.UnproducedOutputBodyV1)(\*\*data) | Body of an unproduced-output record.                |
| [`UpstreamDigestV1`](#nw.bodies.UpstreamDigestV1)(\*\*data)       | One `(upstream annotation, its value digest)` pair. |
| [`VerifyingTraceBodyV1`](#nw.bodies.VerifyingTraceBodyV1)(\*\*data)   | Body of a verifying-trace annotation.               |

### *class* nw.bodies.CharacterRefBodyV1(\*\*data)

Bases: `BaseModel`

Body of a character-ref annotation.

Every field beyond `name` is optional with a benign default, so dumps
written by any earlier version of this schema load unchanged — this is
an **additive** enrichment of v1, not a new version, and needs no
lacing migration.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.bodies.DecisionBodyV1(\*\*data)

Bases: `BaseModel`

Body of a decision annotation.

`kind` names the operation (e.g. `"render_shot"`, `"set_character_anchor"`,
`"clone_project"`). `payload` is a free-form dict so producers don’t
need a schema-versioned table per kind. If a kind earns a richer schema
later, it can graduate into its own body URI without disturbing this one.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.bodies.EnvironmentRefBodyV1(\*\*data)

Bases: `BaseModel`

Body of an environment-ref annotation.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.bodies.GenreEnvelopeBodyV1(\*\*data)

Bases: `BaseModel`

Body of the (singleton) genre-envelope annotation.

Field-for-field the `nw.genres.resolve_genre()` envelope, so the
persisted record and the creation-time contract can never drift apart.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.bodies.RenderResultBodyV1(\*\*data)

Bases: `BaseModel`

Body of a render-result annotation.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.bodies.SectionBodyV1(\*\*data)

Bases: `BaseModel`

Body of a section annotation.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.bodies.ShotBodyV1(\*\*data)

Bases: `BaseModel`

Body of a shot annotation.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.bodies.UnproducedOutputBodyV1(\*\*data)

Bases: `BaseModel`

Body of an unproduced-output record.

`status` mirrors `nw.transforms.fanout.UnitStatus`’s two
unproduced cases: `"failed"` (the call itself failed) or `"blocked"`
(an upstream call in the same plan failed first). `upstream` is stored
for the `call_index` fallback identity (see the module docstring); it
is not itself a sufficient key.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.bodies.UpstreamDigestV1(\*\*data)

Bases: `BaseModel`

One `(upstream annotation, its value digest)` pair.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.bodies.VerifyingTraceBodyV1(\*\*data)

Bases: `BaseModel`

Body of a verifying-trace annotation.

`digest_scheme` is recorded rather than assumed: lacing documents that
changing `VALUE_FIELDS` or the canonicalisation is a breaking
cache-invalidation event and bumps the scheme string. A trace written
under an older scheme is not comparable, so [`nw.freshness`](nw.freshness.html.md#module-nw.freshness) treats
the mismatch as *unverifiable* (therefore stale) instead of comparing
digests that mean different things.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### nw.bodies.build_verifying_trace(, for_annotation_id, parent_ids, upstream, asset_id)

Build the trace annotation for one derived annotation, or `None`.

* **Parameters:**
  * **for_annotation_id** ([`UUID`](https://docs.python.org/3/library/uuid.html#uuid.UUID)) – Id of the annotation being described.
  * **parent_ids** ([`Iterable`](https://docs.python.org/3/library/typing.html#typing.Iterable)[[`UUID`](https://docs.python.org/3/library/uuid.html#uuid.UUID) | [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Its `provenance.was_derived_from` — annotation ids
    (`UUID`) and artifact asset ids (64-hex `str`, nw#55).
    Duplicates are collapsed, order preserved.
  * **upstream** ([`Sequence`](https://docs.python.org/3/library/typing.html#typing.Sequence)[`Annotation`]) – The resolved parent annotations. \*\*Must cover every
    annotation id in 

    ```
    ``
    ```

    parent_ids\`\`\*\* — a trace that omits a parent
    would let that parent change unnoticed. Asset ids need no
    resolving: they are recorded as they are.
  * **asset_id** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – The project’s asset id, for the sentinel reference.
* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[`Annotation`]
* **Returns:**
  The trace annotation, or `None` when there is nothing to verify
  (no parents) or the trace would be incomplete (a parent could not be
  resolved, or its value could not be digested). `None` is the safe
  answer in both cases: [`nw.freshness`](nw.freshness.html.md#module-nw.freshness) reads *no trace* as
  *unverifiable*, so the annotation keeps today’s conservative
  reachability behaviour instead of being silently declared fresh.

### Modules

| [`character_ref`](nw.bodies.character_ref.html.md#module-nw.bodies.character_ref)         | Body schema for character refs — pointers to a character folder.     |
|-------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------|
| [`decision`](nw.bodies.decision.html.md#module-nw.bodies.decision)                   | Body schema for decision-log entries.                                |
| [`environment_ref`](nw.bodies.environment_ref.html.md#module-nw.bodies.environment_ref)     | Body schema for environment refs.                                    |
| [`genre_envelope`](nw.bodies.genre_envelope.html.md#module-nw.bodies.genre_envelope)       | Body schema for the project's resolved genre envelope.               |
| [`render_result`](nw.bodies.render_result.html.md#module-nw.bodies.render_result)         | Body schema for render results — the output of a render Transform.   |
| [`section`](nw.bodies.section.html.md#module-nw.bodies.section)                     | Body schema for timeline sections (verse, chorus, scene-1, …).       |
| [`shot`](nw.bodies.shot.html.md#module-nw.bodies.shot)                           | Body schema for shots — the renderable visual unit.                  |
| [`unproduced_output`](nw.bodies.unproduced_output.html.md#module-nw.bodies.unproduced_output) | Body schema for unproduced-output records — nw#44.                   |
| [`verifying_trace`](nw.bodies.verifying_trace.html.md#module-nw.bodies.verifying_trace)     | Body schema for verifying traces — what makes early cutoff possible. |
