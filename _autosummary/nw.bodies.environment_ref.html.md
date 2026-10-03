# nw.bodies.environment_ref

Body schema for environment refs.

URI: `annot://schema/environment-ref/v1`

Same pattern as character-ref: small project-level pointer at an
`environments/<name>/` folder.

### Classes

| [`EnvironmentRefBodyV1`](#nw.bodies.environment_ref.EnvironmentRefBodyV1)(\*\*data)   | Body of an environment-ref annotation.   |
|-----------------------------------------------------------------------------------|------------------------------------------|

### *class* nw.bodies.environment_ref.EnvironmentRefBodyV1(\*\*data)

Bases: `BaseModel`

Body of an environment-ref annotation.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].
