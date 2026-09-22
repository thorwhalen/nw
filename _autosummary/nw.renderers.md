# nw.renderers

Render strategies — pluggable, plan-producing, **shot-typed**.

**Scope.** A Strategy is the plug-in point of the *shot* render unit
([`nw.workflow`](nw.workflow.md#module-nw.workflow)) — and, through the adapter in
`nw/transforms/_adapters/render_strategy.py`, of the **general engine too**.
That adapter wraps *every* registered strategy at import time, so registering
one here publishes a `shot_to_render_result.fal.<name>` Transform for free,
and [`nw.genres.Genre`](nw.md#nw.Genre) validates a genre’s `strategy_names` against
this registry. The two registries are one pipeline with two front doors, not
two pipelines — the README says the same thing under “Render strategies”.

What a Strategy **cannot** express is a render that is not a video shot: it is
typed to a [`nw.workflow.ShotPreparation`](nw.workflow.md#nw.workflow.ShotPreparation) in and an `output.mp4` out.
So the split is by render *kind*, not by registry:

- a new way to render a **shot** → register a Strategy here, and get the
  Transform adaptation for free;
- a new render **kind** — audio weave, slideshow, anything whose input is not a
  shot — → register a [`nw.transforms.Transform`](nw.md#nw.Transform) directly; that registry
  is the render-kind-agnostic one.

See nw#9.

A *strategy* knows how to turn a [`nw.workflow.ShotPreparation`](nw.workflow.md#nw.workflow.ShotPreparation) into:

1. A `falaw.Plan` (pure data — [`Strategy.plan()`](#nw.renderers.Strategy.plan)).
2. A final `output.mp4` path, given the Plan’s executed Artifacts
   ([`Strategy.materialize()`](#nw.renderers.Strategy.materialize)).

Strategies are registered with an `xdol.Registry` keyed by name. Apps
can register their own strategies (e.g. `composite_lipsync`, `slideshow`,
`panel`) without modifying nw.

Built-in strategies (registered at import):

- `lipsync`            — character anchor + audio → talking video (omnihuman)
- `image_to_video`     — env / fresh storyboard still → animated clip
- `text_to_video`      — prompt-only short clip
- `still`              — image looped over audio (no video gen)
- `composite_lipsync`  — character + environment + audio → composite-then-talk
  : (“Thor in a bell tower playing piano, lipsynced”)

### Module Attributes

| [`strategies`](#nw.renderers.strategies)   | Public registry — apps add strategies via `strategies.register("name", impl)`.   |
|---------------------------------------------------------------|----------------------------------------------------------------------------------|

### Functions

| [`get_strategy`](#nw.renderers.get_strategy)(name)            | Look up a strategy by name; raises if unknown.   |
|--------------------------------------------------------------------------------|--------------------------------------------------|
| [`list_strategies`](#nw.renderers.list_strategies)()             | Return all registered strategy names (sorted).   |
| [`register_strategy`](#nw.renderers.register_strategy)(name, impl) | Register a strategy.                             |

### Classes

| [`Strategy`](#nw.renderers.Strategy)(\*args, \*\*kwargs)   | Render-strategy contract.   |
|---------------------------------------------------------------------------------|-----------------------------|

### *class* nw.renderers.Strategy(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

Render-strategy contract.

#### materialize(prep, plan, artifacts)

Turn executed Artifacts into `shot_dir/output.mp4`. May download +
run ffmpeg, but no fal calls.

* **Return type:**
  Path

#### plan(prep, , quality='balanced', model_overrides=None)

Build a `falaw.Plan` for the prepared shot. No fal calls.

* **Return type:**
  `Plan`

### nw.renderers.get_strategy(name)

Look up a strategy by name; raises if unknown.

* **Return type:**
  [`Strategy`](#nw.renderers.Strategy)

### nw.renderers.list_strategies()

Return all registered strategy names (sorted).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### nw.renderers.register_strategy(name, impl)

Register a strategy. Returns `impl` so it can be used inline.

* **Return type:**
  [`Strategy`](#nw.renderers.Strategy)

### nw.renderers.strategies *: Registry* *= <Registry nw.renderers>*

Public registry — apps add strategies via `strategies.register("name", impl)`.

### Modules

| [`composite_lipsync`](nw.renderers.composite_lipsync.md#module-nw.renderers.composite_lipsync)   | Strategy: composite_lipsync — character + environment + audio → talking video.   |
|------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------|
| [`image_to_video`](nw.renderers.image_to_video.md#module-nw.renderers.image_to_video)         | Strategy: image_to_video — env or fresh-storyboard still → animated clip.        |
| [`lipsync`](nw.renderers.lipsync.md#module-nw.renderers.lipsync)                       | Strategy: lipsync — character anchor + audio → talking video.                    |
| [`still`](nw.renderers.still.md#module-nw.renderers.still)                           | Strategy: still — image looped over audio (no fal video gen).                    |
| [`text_to_video`](nw.renderers.text_to_video.md#module-nw.renderers.text_to_video)           | Strategy: text_to_video — prompt-only short clip.                                |
