# nw.renderers.composite_lipsync

Strategy: composite_lipsync — character + environment + audio → talking video.

The keystone deliverable from interface_design_plan.md item E. Two-call plan:

1. `composite_character_in_environment` (Flux Kontext): take the character
   anchor and the environment anchor, produce one composited still where the
   character is *in* the environment.
2. `animate_face` (omnihuman): take that composited still + the audio slice,
   produce a lipsynced talking video.

The second call references the first via the `<from 0>` placeholder, so the
Plan is self-contained — caller can inspect `plan.total_cost_usd` before
either call fires.

Failure modes handled at plan time:

- No character anchor → plan() raises with a clear message.
- No environment anchor → plan() raises (composite needs both inputs).

For the case where the user has only a character (no environment), use the
plain `lipsync` strategy, which lipsyncs the character image directly.

model_overrides keys understood:

> - `image_edit` — override the composite model (e.g. flux-pro/kontext/max).
> - `avatar`     — override the lipsync model.

### Classes

| [`CompositeLipsyncStrategy`](#nw.renderers.composite_lipsync.CompositeLipsyncStrategy)()   | `render_strategy="composite_lipsync"`.   |
|-------------------------------------------------------------------------------|------------------------------------------|

### *class* nw.renderers.composite_lipsync.CompositeLipsyncStrategy

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

`render_strategy="composite_lipsync"`.

The “Thor in a bell tower playing piano, lipsynced to the song” strategy.
