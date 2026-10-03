# nw.workflow

Workflow: prepare → plan → execute, for a **video shot**.

**Scope — read this before extending anything here.** This module and
[`nw.renderers`](nw.renderers.html.md#module-nw.renderers) are the *shot* render unit: they bake in
[`nw.schema.ShotSpec`](nw.schema.html.md#nw.schema.ShotSpec), an open-string `render_strategy`, and an
`output.mp4`. They are **not** the render-kind-agnostic engine, and they are
**not** the place to add a new render *kind*. That engine is
[`nw.transforms`](nw.transforms.html.md#nw.transforms), whose `plan`/`execute` split is the same shape over
arbitrary annotation kinds — it is what reelee’s production video path and
braidio’s audio-weave path both ride.

\*\*This is a layer *under* that engine, not a path parallel to it — and it is
not dead code.\*\* The engine’s shot arrow is built on top of this module:
`nw/transforms/_adapters/render_strategy.py`, which publishes every
`shot_to_render_result.fal.<strategy>` Transform, calls [`prepare_shot()`](#nw.workflow.prepare_shot)
in both its `plan` (`upload=params.upload`) and its `execute`
(`upload=False`); and [`plan_render_shot()`](#nw.workflow.plan_render_shot) / [`execute_render()`](#nw.workflow.execute_render) both
call [`nw.renderers.get_strategy()`](nw.renderers.html.md#nw.renderers.get_strategy). So a “cleanup” here — dropping
[`prepare_shot()`](#nw.workflow.prepare_shot)’s `upload=` keyword, deleting this module as unused —
silently breaks the Transform path that the docs hold up as the correct one.

What is true, and narrower than “legacy” sounds, is a statement about \*entry
points\*: measured 2026-08-27, [`prepare_shot()`](#nw.workflow.prepare_shot), [`plan_render_shot()`](#nw.workflow.plan_render_shot),
[`execute_render()`](#nw.workflow.execute_render) and [`nw.renderers.get_strategy()`](nw.renderers.html.md#nw.renderers.get_strategy) have **zero** call
sites outside nw (muvid has its own `muvid.schema.ShotSpec` and its own
ffmpeg strategies). Nothing downstream drives these functions *directly* today,
and new callers should go through the Transform registry instead — but inside
nw they are load-bearing. See `misc/docs/Rendering Provenance and Partial
Re-render.md` and nw#9.

The render pipeline has three phases, each cleanly separable:

1. **Prepare** ([`prepare_shot()`](#nw.workflow.prepare_shot)) — *local* work: extract the audio slice
   for the shot, find character/environment anchor images, gather lyric lines,
   build the storyboard prompt. No fal calls. Output: [`ShotPreparation`](#nw.workflow.ShotPreparation),
   a typed bundle of local file paths and prose.
2. **Plan** ([`plan_render_shot()`](#nw.workflow.plan_render_shot)) — *pure-data* work: given a
   [`ShotPreparation`](#nw.workflow.ShotPreparation), build a `falaw.Plan` of the fal calls
   > that will produce the shot. Returns the Plan + the list of artifacts
   > that haven’t been generated yet (e.g. uploads needed first). Still no
   > fal calls.
3. **Execute** ([`execute_render()`](#nw.workflow.execute_render)) — the *only* phase with fal contact.
   Uploads local files, drives the Plan, downloads outputs, trims/pads to
   the shot’s exact duration. Returns the final mp4 path.

This split is what enables:

- A budget gate that’s honest (cost is computed at plan time).
- Tests that exercise plan construction without a fal account.
- A UI that says “you’re about to spend $4.12, click confirm” before the
  network goes near a credit card.
- The “render then kill once audio.wav exists” hack from interface_design_plan
  (item #6) becomes one call: `prepare_shot(project, shot_id)`.

### Functions

| [`execute_render`](#nw.workflow.execute_render)(prep, plan, \*[, on_event, ...])   | Execute a Plan, materialize the result as `shot_dir/output.mp4`.   |
|----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------|
| [`plan_render_shot`](#nw.workflow.plan_render_shot)(prep, \*[, quality, ...])        | Build a `falaw.Plan` for rendering a prepared shot.                |
| [`prepare_shot`](#nw.workflow.prepare_shot)(project, shot_id, \*[, upload])      | Resolve all local inputs for rendering a shot.                     |

### Classes

| [`ShotPreparation`](#nw.workflow.ShotPreparation)(project_root, shot, ...[, ...])   | Local-only inputs for rendering a single shot.   |
|----------------------------------------------------------------------------------------------------|--------------------------------------------------|

### *class* nw.workflow.ShotPreparation(project_root, shot, shot_dir, audio_slice_path, audio_slice_url='', character_anchor_paths=<factory>, character_anchor_urls=<factory>, environment_anchor_path=None, environment_anchor_url='', lyric_lines=<factory>, storyboard_prompt='', global_style='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Local-only inputs for rendering a single shot.

Building a ShotPreparation is a pure-filesystem operation: no fal calls
that bill, no network beyond fal-storage uploads (which are free). The
upload step happens here so the resulting URLs are stable and the cache
key derived from them is honest.

Multiple downstream consumers (the planner, an inspection report, a UI
preview) can read this without re-doing the audio extraction.

#### audio_slice_path *: [Path](https://docs.python.org/3/library/pathlib.html#pathlib.Path)*

Local path to the song’s audio over [shot.start_s, shot.end_s].

#### audio_slice_url *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)*

fal-storage URL of the audio slice (set by [`prepare_shot()`](#nw.workflow.prepare_shot) when
a fal API key is available; empty otherwise — strategies that need URLs
will raise descriptively).

#### character_anchor_paths *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [Path](https://docs.python.org/3/library/pathlib.html#pathlib.Path)]*

Per-character path to the curated anchor image.

#### character_anchor_urls *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [str](https://docs.python.org/3/builtins/stdtypes.html#str)]*

Per-character fal-storage URL of the anchor image.

#### environment_anchor_path *: [Path](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [None](https://docs.python.org/3/builtins/constants.html#None)*

Path to the environment establishing image, or None.

#### environment_anchor_url *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)*

fal-storage URL of the environment image; empty if no env image.

#### lyric_lines *: [list](https://docs.python.org/3/builtins/stdtypes.html#list)[[dict](https://docs.python.org/3/builtins/stdtypes.html#dict)]*

List of `{"text", "start_s", "end_s", "line_index", "section"}` dicts.

#### storyboard_prompt *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)*

shot description + framing + camera + characters +
environment + style + lyric lines (when present).

* **Type:**
  Full prose prompt

### nw.workflow.execute_render(prep, plan, , on_event=None, use_cache=True, project=None)

Execute a Plan, materialize the result as `shot_dir/output.mp4`.

Refuses to execute a plan-only Plan (one whose arguments still contain
`<plan-only:...>` placeholders) — those exist so the planner can show
cost without any uploads, and need to be replaced with real URLs (call
[`prepare_shot()`](#nw.workflow.prepare_shot) with `upload=True`) before execute.

* **Parameters:**
  * **prep** ([`ShotPreparation`](#nw.workflow.ShotPreparation)) – The [`ShotPreparation`](#nw.workflow.ShotPreparation) the Plan was built for.
  * **plan** (`Plan`) – A `falaw.Plan` (typically from [`plan_render_shot()`](#nw.workflow.plan_render_shot)).
  * **on_event** – Optional event subscriber forwarded to the falaw call layer.
  * **use_cache** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – When True (default), routes via `cached_call_fal` so
    cache hits skip the network.
  * **project** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Project`](nw.project.html.md#nw.project.Project)]) – Optional `Project`. When given, a render-decision
    annotation is appended to the project graph after execution
    with `was_derived_from = (shot_annotation_id,)`, so reelee’s
    freshness queries (`descendants_of` / `stale_after`) walk
    from the shot to its render output.
* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)
* **Returns:**
  Path to `shot_dir/output.mp4` (trimmed/padded to `prep.duration_s`).

### nw.workflow.plan_render_shot(prep, , quality='balanced', model_overrides=None)

Build a `falaw.Plan` for rendering a prepared shot.

Dispatches on `prep.shot.render_strategy` via [`nw.renderers`](nw.renderers.html.md#module-nw.renderers).

* **Parameters:**
  * **prep** ([`ShotPreparation`](#nw.workflow.ShotPreparation)) – A [`ShotPreparation`](#nw.workflow.ShotPreparation) from [`prepare_shot()`](#nw.workflow.prepare_shot).
  * **quality** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Default quality tier passed to the strategy.
  * **model_overrides** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]) – Optional mapping of strategy-step → model_id, e.g.
    `{"avatar": "fal-ai/bytedance/omnihuman/v1.5"}` to bypass the
    default avatar model. The keys understood by each strategy are
    documented on the strategy itself.
* **Return type:**
  `Plan`
* **Returns:**
  A `falaw.Plan`. Caller can inspect `plan.total_cost_usd` and
  decide whether to `execute_plan(plan)`.

### nw.workflow.prepare_shot(project, shot_id, , upload=True)

Resolve all local inputs for rendering a shot.

No billable fal calls. When `upload=True` (the default), local files
are uploaded to fal-storage so the planner can build a Plan with stable
URLs (uploads are free; the cache key derived from those URLs is honest).
When `upload=False` (e.g. for tests or dry-run reporting), the URL
fields are left empty.

Idempotent in spirit but not byte-stable: fal-storage URLs include
expiring signatures, so two `prepare_shot` calls on the same project
produce different URLs. The local file paths are byte-stable.

* **Parameters:**
  * **project** ([`Project`](nw.project.html.md#nw.project.Project)) – An [`nw.Project`](nw.html.md#nw.Project) instance.
  * **shot_id** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – The shot’s id, as in `project.read_spec().shots[*].id`.
  * **upload** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – When True (default), upload local files to fal-storage and
    populate the `*_url` fields. When False, only the local paths
    are populated.
* **Return type:**
  [`ShotPreparation`](#nw.workflow.ShotPreparation)
* **Returns:**
  A [`ShotPreparation`](#nw.workflow.ShotPreparation) with local paths (and URLs if `upload`)
  ready to plan.
