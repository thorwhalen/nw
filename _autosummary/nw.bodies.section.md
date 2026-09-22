# nw.bodies.section

Body schema for timeline sections (verse, chorus, scene-1, …).

URI: `annot://schema/section/v1`

A section is a labeled span of a project’s master timeline. Sections are
typically non-overlapping but the schema doesn’t enforce that — apps can
encode their own constraints.

### Classes

| [`SectionBodyV1`](#nw.bodies.section.SectionBodyV1)(\*\*data)   | Body of a section annotation.   |
|----------------------------------------------------------------------------|---------------------------------|

### *class* nw.bodies.section.SectionBodyV1(\*\*data)

Bases: `BaseModel`

Body of a section annotation.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].
