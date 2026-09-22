# nw.storyboard

Storyboard ↔ Project bridge.

A storyboard lives at `<project_root>/storyboard.annot.sqlite` (a lacing
`SqliteStore`). Each panel is an `annot://schema/storyboard-panel/v1`
`lacing.Annotation`; the storyboard’s own asset_id is the project’s
song hash, so panels share an interval space with the project’s shots and
alignment.

Public surface:

- `open_storyboard(project)()` — load the project’s storyboard, or return
  an empty one if none exists yet.
- `save_storyboard(project, sb, *, panel_intervals)()` — persist panels
  into the project’s lacing store.
- `storyboard_from_shots(project)()` — convenience: build a Storyboard
  with one panel per shot, intervals matching the shots.
- `plan_render_panel_images(sb, *, quality, model_overrides)()` — a
  Plan with one `generate_image` call per panel that doesn’t yet have a
  `role="seed"` image. Pure data; cost-aware.
- `execute_render_panel_images(project, sb, plan)()` — execute the Plan,
  download images into `storyboard/` under the project, return an updated
  Storyboard with the new `role="seed"` PanelImages attached.

The artful package is the storyboard *data layer*; nw.storyboard wires it
into a folder-backed nw project.

### Functions

| [`execute_render_panel_images`](#nw.storyboard.execute_render_panel_images)(project, ...[, ...])   | Execute `plan`, download each artifact, attach a PanelImage.            |
|-----------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------|
| [`open_storyboard`](#nw.storyboard.open_storyboard)(project)                           | Load the project's storyboard.                                          |
| [`plan_render_panel_images`](#nw.storyboard.plan_render_panel_images)(storyboard, \*[, ...])    | Build a Plan that generates a seed image for each panel that lacks one. |
| [`project_asset_id`](#nw.storyboard.project_asset_id)(project)                          | The asset_id used for storyboard panel references.                      |
| [`save_storyboard`](#nw.storyboard.save_storyboard)(project, storyboard, \*, ...)      | Persist a Storyboard into the project's SqliteStore.                    |
| [`storyboard_db_path`](#nw.storyboard.storyboard_db_path)(project)                        | Return the path to the project's storyboard SQLite store.               |
| [`storyboard_from_shots`](#nw.storyboard.storyboard_from_shots)(project, \*[, title, style]) | Build a one-panel-per-shot draft Storyboard from a project's shots.     |

### nw.storyboard.execute_render_panel_images(project, storyboard, plan, panel_ids, , on_event=None, use_cache=True, on_failure='halt')

Execute `plan`, download each artifact, attach a PanelImage.

Returns a NEW `Storyboard` (input `storyboard` is unchanged) with
the materialized seed images attached as `role="seed"` PanelImages.

Files land under `<project_root>/storyboard/<panel_id>.png`. The
PanelImage record stores both the project-relative path and the
artifact_id (content hash via lacing.Artifact), so downstream consumers
can prefer one or the other.

`on_failure` is nw#25’s policy, and this is the function the issue names
as **nw’s real fan-out shape** — one `generate_image` per panel. Under
`"isolate"` a panel whose call failed is simply left without a seed image;
every panel that rendered keeps its own, instead of one content-filtered
panel discarding the whole batch. `"halt"` is the default and unchanged.

Panels are matched to outcomes **by index into the plan**, never by position
in a shortened artifact list — the latter attaches panel 48’s image to panel
47 the moment one call drops out.

* **Return type:**
  `Storyboard`

### nw.storyboard.open_storyboard(project)

Load the project’s storyboard. Returns an empty one if not present.

* **Return type:**
  `Storyboard`

### nw.storyboard.plan_render_panel_images(storyboard, , quality='balanced', image_size='landscape_16_9', model_id=None, only_missing=True)

Build a Plan that generates a seed image for each panel that lacks one.

* **Parameters:**
  * **storyboard** (`Storyboard`) – The `artful.Storyboard`.
  * **quality** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – image-gen quality tier.
  * **image_size** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – “landscape_16_9” by default; respects the storyboard’s
    aspect when it can be mapped to a falaw size, otherwise uses
    this default.
  * **model_id** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Override the image-gen model. Defaults to whatever
    `falaw.pick_model(category="image", quality_tier=quality)`
    picks (e.g. flux/dev at balanced).
  * **only_missing** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – When True (default), skip panels that already have a
    `role="seed"` image. When False, plan one call per panel
    regardless.
* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[`Plan`, [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]
* **Returns:**
  `(plan, panel_ids)` — the Plan, and the panel ids in the same
  order as the Plan’s calls (so [`execute_render_panel_images()`](#nw.storyboard.execute_render_panel_images)
  knows which panel each artifact belongs to).

### nw.storyboard.project_asset_id(project)

The asset_id used for storyboard panel references.

Uses the SHA-256 of the project’s song bytes when available, so the
asset_id matches whatever a downstream consumer would compute via
`lacing.hash_file()`. Falls back to a stable derived id when the
song isn’t available yet.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### nw.storyboard.save_storyboard(project, storyboard, , panel_intervals, was_attributed_to='user:nw', was_generated_by='agent:nw.storyboard')

Persist a Storyboard into the project’s SqliteStore.

Wipes the existing storyboard panels (under the default tier) so the
save is idempotent — re-running with edited panels replaces them rather
than accumulating duplicates.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### nw.storyboard.storyboard_db_path(project)

Return the path to the project’s storyboard SQLite store.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### nw.storyboard.storyboard_from_shots(project, , title=None, style=None)

Build a one-panel-per-shot draft Storyboard from a project’s shots.

Each panel’s caption defaults to the shot’s description, framing and
camera carry over, and the panel’s `shot_id` points back at the shot.
No images are attached yet — use [`plan_render_panel_images()`](#nw.storyboard.plan_render_panel_images) to
generate them.

Returns `(storyboard, panel_intervals)` so the caller can feed both
into [`save_storyboard()`](#nw.storyboard.save_storyboard).

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[`Storyboard`, [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), `TimeInterval`]]
