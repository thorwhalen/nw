# nw.renderers.image_to_video

Strategy: image_to_video — env or fresh-storyboard still → animated clip.

Two-call workflow:

1. If the shot has an environment anchor, use it as the i2v seed (no image
   gen call needed). Otherwise, generate a fresh storyboard still via
   `falaw.generate_image`.
2. Animate the seed with `falaw.image_to_video`.

A future `seed` parameter (see interface_design_plan item D) will let
callers force the character anchor as the seed. For now, env > fresh-still.

model_overrides keys understood:

> - `image`          — image-gen model when generating a fresh still.
> - `image_to_video` — i2v model (e.g. hailuo, kling, seedance).

### Classes

| [`ImageToVideoStrategy`](#nw.renderers.image_to_video.ImageToVideoStrategy)()   | `render_strategy="image_to_video"`.   |
|---------------------------------------------------------------------------|---------------------------------------|

### *class* nw.renderers.image_to_video.ImageToVideoStrategy

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

`render_strategy="image_to_video"`.
