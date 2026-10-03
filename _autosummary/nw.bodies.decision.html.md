# nw.bodies.decision

Body schema for decision-log entries.

URI: `annot://schema/decision/v1`

A decision is a typed, project-local provenance record: which character
anchor was picked, which model overrode the default, why a shot was
retried. Today nw also writes them to `.nw/decisions.jsonl` for
quick tail-grepping; the canonical form is this annotation.

Decisions are stored as **timeless** annotations (no interval) under a
sentinel zero-duration MediaRef pointing at the project’s asset_id.
Reelee will surface them in inspector / network views as the audit
trail for “why did this come out this way?”.

### Classes

| [`DecisionBodyV1`](#nw.bodies.decision.DecisionBodyV1)(\*\*data)   | Body of a decision annotation.   |
|-----------------------------------------------------------------------------|----------------------------------|

### *class* nw.bodies.decision.DecisionBodyV1(\*\*data)

Bases: `BaseModel`

Body of a decision annotation.

`kind` names the operation (e.g. `"render_shot"`, `"set_character_anchor"`,
`"clone_project"`). `payload` is a free-form dict so producers don’t
need a schema-versioned table per kind. If a kind earns a richer schema
later, it can graduate into its own body URI without disturbing this one.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].
