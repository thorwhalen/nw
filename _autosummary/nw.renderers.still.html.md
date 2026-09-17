# nw.renderers.still

Strategy: still — image looped over audio (no fal video gen).

Two paths:

- If the shot has an environment anchor or a character anchor on disk, no
  image-gen call is needed. The strategy returns a Plan with **zero** fal
  calls (cost = 0); `materialize()` just runs ffmpeg locally to loop
  the image over the audio slice.
- If neither anchor is set, plans one `generate_image` call to make a
  fresh storyboard still, then loops it locally.

This is the cheapest strategy — useful for sections where motion would be
distracting, or for placeholder rendering during development.

model_overrides keys understood:

> - `image` — image-gen model when generating a fresh still.

### Classes

| [`StillStrategy`](#nw.renderers.still.StillStrategy)()   | `render_strategy="still"`.   |
|--------------------------------------------------------------------|------------------------------|

### *class* nw.renderers.still.StillStrategy

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

`render_strategy="still"`.
