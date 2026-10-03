# nw.bodies.shot

Body schema for shots — the renderable visual unit.

URI: `annot://schema/shot/v1`

A shot is a timeline-locked visual unit with a render strategy and
references to the characters / environment in frame. The interval lives
on the annotation’s `lacing.MediaRef` (so it shares an interval
space with sections, lyric alignments, viseme tracks, and storyboard
panels).

The render output (`output.mp4`) is a separate `lacing.Artifact`
whose `provenance.was_derived_from` includes this shot’s annotation id.
That’s what enables reelee’s “what’s downstream of this shot?” queries.

### Classes

| [`ShotBodyV1`](#nw.bodies.shot.ShotBodyV1)(\*\*data)   | Body of a shot annotation.   |
|-------------------------------------------------------------------------|------------------------------|

### *class* nw.bodies.shot.ShotBodyV1(\*\*data)

Bases: `BaseModel`

Body of a shot annotation.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].
