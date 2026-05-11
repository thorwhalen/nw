# nw

**Narrative Workflow** — an application-orchestration framework for
audiovisual projects (music videos, explainers, podcast clips, slideshows).

A project is a folder. An "app" is a small specialization on top. `nw`
gives you the typed core — schema, folder facade, render workflow,
strategy registry, storyboard bridge, QA reports, and a provenance graph —
so apps don't have to.

```python
import nw

proj = nw.Project.init("my_video", song="track.mp3")
proj.add_character("alex", description="warm, deadpan")
proj.set_character_anchor("alex", "characters/alex/refs/headshot.png")
proj.upsert_shot(nw.ShotSpec(
    id="shot_01", start_s=0.0, end_s=8.0,
    characters=("alex",), render_strategy="lipsync",
))

prep = nw.prepare_shot(proj, "shot_01")        # local-only work
plan = nw.plan_render_shot(prep)               # pure data; inspect cost
print(f"estimated: ${plan.total_cost_usd:.2f}")
output = nw.execute_render(prep, plan, project=proj)  # the only billable phase
```

## Install

```bash
pip install nw
```

## What you get

### A typed project on disk

`nw.Project` is a small facade over a project folder. The folder is the
single source of truth: `project.json` holds project-level metadata; a
per-project lacing graph (`project.annot.sqlite`) holds sections, shots,
character/environment refs, and decisions.

```python
proj = nw.Project("path/to/project")        # opens existing
summary = proj.read_summary()               # typed ProjectSummary
spec = proj.read_spec()                     # typed ProjectSpec
proj.upsert_shot(shot)                      # graph-backed
proj.log_decision("retry_shot", shot_id="shot_03", reason="lipsync drift")
```

`Project.read_summary()` returns the small set of facts you usually want
at a glance: title, song path, counts of characters / sections / shots,
how many shots are rendered, which lifecycle stages have been reached.

### Plan → execute, with a budget gate

Rendering splits cleanly into three phases:

1. **prepare** (`nw.prepare_shot`) — local work: audio slice, anchor
   resolution, storyboard prompt assembly. No billable fal calls.
2. **plan** (`nw.plan_render_shot`) — pure data: returns a
   `falaw.Plan`. Inspect `plan.total_cost_usd` before executing.
3. **execute** (`nw.execute_render`) — the only phase that talks to fal.
   Materializes `shots/<id>/output.mp4` and records a render-decision in
   the project graph.

```python
prep = nw.prepare_shot(proj, "shot_01", upload=False)   # for dry-run / cost preview
plan = nw.plan_render_shot(prep, quality="balanced")
print(plan.total_cost_usd, [c.tool for c in plan.calls])

prep = nw.prepare_shot(proj, "shot_01")                 # upload=True for real run
output = nw.execute_render(prep, plan, project=proj)
```

### A pluggable strategy registry

Each shot has an open-string `render_strategy`. `nw.renderers` ships
five built-in strategies and lets apps register their own without
touching `nw`:

| name                  | what it does                                                  |
| --------------------- | ------------------------------------------------------------- |
| `lipsync`             | character anchor + audio → talking video (omnihuman)          |
| `image_to_video`      | env / fresh storyboard still → animated clip                  |
| `text_to_video`       | prompt-only short clip                                        |
| `still`               | image looped over audio (no video gen)                        |
| `composite_lipsync`   | character + environment + audio → composite, then talking video |

```python
nw.list_strategies()                       # ['composite_lipsync', 'image_to_video', ...]
nw.register_strategy("my_app_strategy", MyStrategy())
```

### A storyboard layer

`nw.storyboard` bridges [`artful`](https://pypi.org/project/artful)
storyboards into an `nw.Project`. Build one panel per shot, plan the
seed-image generation as a `falaw.Plan`, then execute:

```python
sb, intervals = nw.storyboard_from_shots(proj)
plan, panel_ids = nw.plan_render_panel_images(sb, quality="balanced")
sb = nw.execute_render_panel_images(proj, sb, plan, panel_ids)
nw.save_storyboard(proj, sb, panel_intervals=intervals)
```

### QA reports for renders

`nw.inspect` answers questions a successful render can't:
"did it come out the right length?", "is there a frozen-frame segment?",
"are there gaps between shots?":

```python
report = nw.shot_report(proj, "shot_01")
report.duration_within_tolerance          # False if Hailuo returned a short clip
report.has_long_freeze                    # True if a ≥1s frozen segment is detected

compose = nw.compose_report(proj)
compose.freeze_alerts                     # tuple of suspicious shots
compose.gaps                              # gaps between consecutive shots
```

### A provenance graph (and freshness queries)

All sections, shots, refs, and decisions live in a lacing annotation
graph with `was_derived_from` edges. That makes "what's downstream of
this change?" a one-line query:

```python
stale = nw.stale_after(proj.root, character_annotation_id)
upstream = nw.derived_from(proj.root, render_annotation_id)
shots = nw.annotations_at_tier(proj.root, "shot")
```

Pre-graph projects (and muvid fixtures) auto-migrate on first open;
the migration is idempotent and writes a sentinel under `.nw/`.

### Sibling experiments

Comparing four interpretations of the same song is a first-class
operation, not a shell loop:

```python
nw.clone_project("the_bells", "the_bells_v1_lipsync",
                 preserve=("song", "lyrics", "characters"),
                 reset=("script", "shots", "output", ".nw"))

# Apply the same operation across a cohort:
summaries = nw.summarize_all(["the_bells_v1", "the_bells_v2", "the_bells_v3"])
nw.apply_to_projects(roots, lambda p: nw.compose_report(p), parallel=True)
```

## Project layout

```
my_video/
  project.json                  # project-level metadata (title, song, style)
  project.annot.sqlite          # lacing graph: sections, shots, refs, decisions
  storyboard.annot.sqlite       # storyboard panels (created on save_storyboard)
  song/                         # master audio
  lyrics/                       # lyrics + alignment (alignment.annot)
  characters/<name>/
    card.json                   # card with reference_image_path (the "anchor")
    refs/                       # candidate images
    selected/                   # curator-picked images
  environments/<name>/
    establishing.png            # the environment anchor
  shots/<shot_id>/
    audio.wav                   # the song over [start_s, end_s]
    shot.json                   # mirror of the shot spec
    output.mp4                  # the rendered shot
  output/
    final.mp4                   # composed timeline
  .nw/
    decisions.jsonl             # tail-grep-able decision audit
    migrated_to_graph           # migration sentinel
```

## API at a glance

```python
# Folder facade
nw.Project, nw.Project.init, nw.CharacterImage

# Schema
nw.ProjectSpec, nw.ProjectSummary, nw.SectionSpec, nw.ShotSpec,
nw.CharacterRef, nw.EnvironmentRef, nw.SongInfo, nw.SCHEMA_VERSION

# Workflow
nw.prepare_shot, nw.plan_render_shot, nw.execute_render, nw.ShotPreparation

# Strategies
nw.Strategy, nw.get_strategy, nw.list_strategies,
nw.register_strategy, nw.strategies

# Storyboard
nw.open_storyboard, nw.save_storyboard, nw.storyboard_from_shots,
nw.plan_render_panel_images, nw.execute_render_panel_images,
nw.storyboard_db_path, nw.project_asset_id

# Inspect / QA
nw.shot_report, nw.compose_report, nw.ShotReport, nw.ComposeReport,
nw.FrozenSegment, nw.Gap

# Graph / provenance
nw.ProjectGraph, nw.derived_from, nw.descendants_of, nw.stale_after,
nw.annotations_at_tier, nw.iter_all_annotations

# Experiments
nw.clone_project, nw.apply_to_projects, nw.summarize_all

# Migration
nw.migrate_to_graph, nw.is_migrated
```

## Design notes

- **SSOT on the folder.** Every typed value comes from `project.json` plus
  the project graph. Tools never have to invent their own storage.
- **Open render strategies.** `render_strategy` is an open string, not a
  closed enum, so apps register strategies without modifying `nw`.
- **Plan-then-execute.** Cost is computed and inspectable before the
  network goes near a credit card. Plans built with `upload=False` are
  refused at execute time — they exist for inspection only.
- **Provenance by default.** Every render and every curator decision is
  written to the graph with `was_derived_from`, so freshness analysis
  (reelee-style) is a graph walk, not a heuristic.

## Dependencies

`pydantic`, [`falaw`](https://pypi.org/project/falaw) (fal-AI planner),
[`lacing`](https://pypi.org/project/lacing) (annotation graph),
[`xdol`](https://pypi.org/project/xdol) (registry),
[`artful`](https://pypi.org/project/artful) (storyboard).

Optional system tools: `ffmpeg` / `ffprobe` for audio slicing and QA reports.

## License

MIT — see [LICENSE](LICENSE).
