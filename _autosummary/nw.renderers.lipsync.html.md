# nw.renderers.lipsync

Strategy: lipsync — character anchor + audio → talking video.

Calls `falaw.animate_face` (image+audio → talking video). Defaults to
`omnihuman/v1.5` at `quality="high"` because the default `ai-avatar`
hangs reliably (see muvid_project bugs_encountered.md, 2026-05-07).

If multiple characters are present, the first one is picked and a warning is
emitted — multi-character lipsync is composite_lipsync’s territory (Phase 2).

model_overrides keys understood:

> - `avatar` — override the `avatar_model_id` (e.g. omnihuman, ai-avatar).

### Classes

| [`LipsyncStrategy`](#nw.renderers.lipsync.LipsyncStrategy)()   | `render_strategy="lipsync"`.   |
|----------------------------------------------------------------------|--------------------------------|

### *class* nw.renderers.lipsync.LipsyncStrategy

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

`render_strategy="lipsync"`.
