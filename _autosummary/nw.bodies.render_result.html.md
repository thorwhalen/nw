# nw.bodies.render_result

Body schema for render results — the output of a render Transform.

URI: `annot://schema/render-result/v1`

A render-result records that a shot was rendered: which strategy ran, where
the output landed, the video `lacing.Artifact` it produced, and the
cost as quoted at plan time (`total_estimated_cost_usd` is an as-of figure,
never a current one — see its field description and [`nw.pricing`](nw.pricing.html.md#module-nw.pricing)). Its
`provenance.was_derived_from` includes the shot annotation’s id, so a
freshness traversal from the shot finds the render.

This is the `output_kind` of the render-strategy Transforms (see
`nw.transforms._adapters.render_strategy`). It is intentionally small —
the heavy data (the actual mp4) is the referenced Artifact, not the body.

### Classes

| [`RenderResultBodyV1`](#nw.bodies.render_result.RenderResultBodyV1)(\*\*data)   | Body of a render-result annotation.   |
|---------------------------------------------------------------------------------|---------------------------------------|

### *class* nw.bodies.render_result.RenderResultBodyV1(\*\*data)

Bases: `BaseModel`

Body of a render-result annotation.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].
