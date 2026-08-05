# nw

**Narrative Workflow** — the substrate audiovisual production apps are built on.

A project is a folder. A **genre** — music video, explainer, commentary weave,
slideshow — is the reusable specialization on top: pure data, declared over the
substrate, carrying no engine of its own. `nw` owns the engine: the typed
project facade, the `prepare → plan → execute` split with a cost gate, the
Transform contract, an async job layer, a provenance graph with freshness
queries, and QA reports. Apps (`reelee`, `muvid`, `braidio`) supply their own
body schemas, Transforms, and genres — without modifying `nw`.

```python
import nw

# Declare a kind of production (apps do this once, in their own package).
nw.register_genre(
    nw.Genre(
        slug="slideshow",
        title="Slideshow",
        description="Stills over narration, assembled to a video.",
        transform_names=("clips_to_animatic.ffmpeg",),
        projection_entrypoint="clips_to_animatic.ffmpeg",
        templates=(nw.Template(slug="lecture", title="Lecture deck"),),
    )
)

nw.list_genres()  # ['slideshow']
nw.genre_catalog()  # JSON-able: what a CLI / HTTP route / MCP tool serves
nw.resolve_genre(
    "slideshow", "lecture"
)  # {'genre': ..., 'template': ..., 'params': {...}}
```

## Install

```bash
pip install nw
```

## Genre and Template — the central abstraction

A `Genre` is a **reusable definition of a production kind**. It is a frozen
dataclass that *references* substrate pieces by name rather than owning them:

| field                   | what it declares                                                                    |
| ----------------------- | ----------------------------------------------------------------------------------- |
| `body_schema_uris`      | the lacing body schemas (`annot://schema/<kind>/vN`) its artifacts validate against  |
| `transform_names`       | the `nw.transforms` entries forming its pipeline DAG                                 |
| `strategy_names`        | the optional `nw.renderers` strategies it dispatches to                              |
| `projection_entrypoint` | the final assemble/render step producing the delivered artifact                      |
| `templates`             | named presets *within* the genre (see below)                                         |
| `intake_kinds`          | the "what are you making?" answers that select this genre                            |
| `cost_profile`          | a short tag routing the cost gate to the right estimator                             |
| `defaults`              | the "start from scratch" params                                                      |
| `status`                | `available` · `experimental` · `planned`                                             |

A **`Template`** is a named preset *within* a genre — a filled-in default
configuration ("Deep Dive", "Children's book", "Math explainer"). The substrate
owns a Template's *identity* (slug / title / description) and carries a
genre-defined `params` payload it deliberately does **not** interpret; the app
that owns the genre validates and resolves those params. That keeps a genre
self-describing for any consumer (a CLI, an HTTP catalog, an MCP connector)
while app-specific meaning stays in the app.

`nw` ships **no** built-in genres. Concrete genres register themselves from
their own packages, so adding one is a one-file registration — the same
open-closed shape as `nw.transforms` and `nw.renderers`:

```python
nw.register_genre(nw.Genre(slug="music-video", title="Music video", ...))
nw.get_genre("music-video").is_ready()  # every declared transform/strategy present?
nw.recommend_genre("essay")  # an intake answer -> a genre slug (or None)
```

### Genre → project: resolve, initialize, create

Three registries connect a chosen genre to an actual project. Each is optional,
and each is registered by the genre's **owning app** — so a host that aggregates
many genres can serve any of them without knowing which app owns which.

```python
# 1. resolve — pure: (genre, template) -> the creation envelope
nw.resolve_genre("music-video", "cinematic_clip")
# {'genre': 'music-video', 'template': 'cinematic_clip', 'params': {...}}

# 2. initialize — the side-effecting twin: seed a freshly-created project
proj = nw.Project.init("my_video")
nw.initialize_genre("music-video", proj, template="cinematic_clip")

# 3. create — for a *plugged-in* genre a host aggregates but doesn't own:
#    the owning app supplies "make a project for this in the caller's own space"
nw.create_genre_project("commentary-weave", caller_id, "ep_01")
```

An initializer must confine its side effects to the project it is given, so a
failed create can be reverted by removing the project folder —
`create_genre_project` rolls back automatically and is all-or-nothing.

The naming rationale (Genre / Template over `kind` / `format` / `recipe` / …) is
in [thorwhalen/nw#10](https://github.com/thorwhalen/nw/issues/10) and the
architecture discussion that settled it.

## Transforms — the A → B arrow

Every step in an audiovisual workflow is a `Transform`: screenplay → treatment,
beat → storyboard panel, panel → image, clips → animatic, shot → rendered clip.
A Transform is a **swappable, costed function from A-annotations to
B-annotations**, in two phases:

1. **`plan()`** — *pure data*. Returns a `falaw.Plan` plus *skeleton* output
   annotations that already carry provenance, so even a dry-run inspection shows
   what will be produced, from what, and at what cost. No billable calls.
2. **`execute()`** — runs the Plan, completes the skeletons with real artifact
   references, writes them to the project graph, and returns a `TransformResult`
   with *actual* cost and cache savings.

Transforms are keyed by name in an `xdol.Registry`, following
`<from_kind>_to_<to_kind>[.<flavor>[.<variant>]]`:

```python
nw.register_transform(MyTransform())
t = nw.get_transform("beat_to_panel.llm.default")
plan, skeleton = t.plan(proj, inputs, params=params)
result = t.execute(proj, plan, skeleton)
result.cost_usd_actual, result.cache_hit_savings_usd
```

Most Transforms subclass `nw.BaseTransform` and override only `plan()` plus the
class-level `name` / `input_kinds` / `output_kind` / `params_model` /
`is_batch`. `params_model` is a Pydantic model, which is what gives an MCP
server or a CLI a JSON Schema for the Transform for free.

## prepare → plan → execute, with a budget gate

The shot-render workflow makes the same split concrete, and it is why cost is
knowable before the network goes near a credit card:

1. **prepare** (`nw.prepare_shot`) — local work: audio slice, anchor resolution,
   storyboard prompt assembly. No billable calls.
2. **plan** (`nw.plan_render_shot`) — pure data: a `falaw.Plan`. Inspect
   `plan.total_cost_usd` before executing.
3. **execute** (`nw.execute_render`) — the only phase that talks to fal.
   Materializes `shots/<id>/output.mp4` and records a render-decision in the graph.

```python
prep = nw.prepare_shot(proj, "shot_01", upload=False)  # dry-run / cost preview
plan = nw.plan_render_shot(prep, quality="balanced")
print(plan.total_cost_usd, [c.tool for c in plan.calls])

prep = nw.prepare_shot(proj, "shot_01")  # upload=True for the real run
output = nw.execute_render(prep, plan, project=proj)
```

Plans built with `upload=False` are refused at execute time — they exist for
inspection only.

## A typed project on disk

`nw.Project` is a small facade over a project folder. The folder is the single
source of truth: `project.json` holds project-level metadata; a per-project
lacing graph (`project.annot.sqlite`) holds sections, shots, character /
environment refs, and decisions.

```python
proj = nw.Project.init("my_video", song="track.mp3")
proj.add_character("alex", description="warm, deadpan")
proj.set_character_anchor("alex", "characters/alex/refs/headshot.png")
proj.upsert_shot(
    nw.ShotSpec(
        id="shot_01",
        start_s=0.0,
        end_s=8.0,
        characters=("alex",),
        render_strategy="lipsync",
    )
)

proj.read_summary()  # typed ProjectSummary: title, counts, lifecycle stages
proj.read_spec()  # typed ProjectSpec
proj.log_decision("retry_shot", shot_id="shot_03", reason="lipsync drift")
```

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

Pre-graph projects (and muvid fixtures) auto-migrate on first open; the
migration is idempotent and writes a sentinel under `.nw/`.

## Provenance and freshness

All sections, shots, refs, and decisions live in a lacing annotation graph with
`was_derived_from` edges, so "what's downstream of this change?" is a query, not
a heuristic:

```python
downstream = nw.descendants_of(proj.root, character_annotation_id)
stale = nw.stale_after(proj.root, character_annotation_id)
upstream = nw.derived_from(proj.root, render_annotation_id)
shots = nw.annotations_at_tier(proj.root, "shot")
```

See [Known limits](#known-limits) for what `stale_after` does and does not do today.

## Async jobs

`nw.jobs` is a project-scoped facade over [`au`](https://pypi.org/project/au)
for render work too long to sit inside an HTTP request. A *job* is one long,
cancellable unit of work with a durable id, a persistent terminal state, live
progress and a learned ETA, a cost, and a cancel entry keyed by that id:

```python
gate = nw.jobs.estimate(proj, "panel.animate", params)  # cost gate, no enqueue
if not gate["requires_approval"]:
    job = nw.jobs.enqueue(proj, "panel.animate", params)
    nw.jobs.get_job(proj, job.job_id)
    nw.jobs.cancel_job(proj, job.job_id)
nw.jobs.list_jobs(proj)
nw.jobs.to_dict(job)  # the JSON a task tray renders
```

Unknown cost always requires approval. Resubmitting while a job with the same
idempotency key is live returns the existing job rather than launching a
duplicate. Every tunable is keyword-configurable via `nw.jobs.JobsConfig`.

## Render strategies

Each shot carries an open-string `render_strategy`. `nw.renderers` ships five
built-in strategies and lets apps register their own:

| name                | what it does                                                    |
| ------------------- | --------------------------------------------------------------- |
| `lipsync`           | character anchor + audio → talking video (omnihuman)            |
| `image_to_video`    | env / fresh storyboard still → animated clip                    |
| `text_to_video`     | prompt-only short clip                                          |
| `still`             | image looped over audio (no video gen)                          |
| `composite_lipsync` | character + environment + audio → composite, then talking video |

```python
nw.list_strategies()  # ['composite_lipsync', 'image_to_video', ...]
nw.register_strategy("my_strategy", MyStrategy())
```

Each built-in strategy is also adapted into the Transform world as
`shot_to_render_result.fal.<strategy>`, so the two registries stay one pipeline
rather than two.

## Storyboard layer

`nw.storyboard` bridges [`artful`](https://pypi.org/project/artful) storyboards
into an `nw.Project` — one panel per shot, seed-image generation planned as a
`falaw.Plan`, then executed:

```python
sb, intervals = nw.storyboard_from_shots(proj)
plan, panel_ids = nw.plan_render_panel_images(sb, quality="balanced")
sb = nw.execute_render_panel_images(proj, sb, plan, panel_ids)
nw.save_storyboard(proj, sb, panel_intervals=intervals)
```

## QA reports

`nw.inspect` answers what a successful render can't: "did it come out the right
length?", "is there a frozen-frame segment?", "are there gaps between shots?"

```python
report = nw.shot_report(proj, "shot_01")
report.duration_within_tolerance  # False if the model returned a short clip
report.has_long_freeze  # True if a ≥1s frozen segment is detected

compose = nw.compose_report(proj)
compose.freeze_alerts  # tuple of suspicious shots
compose.gaps  # gaps between consecutive shots
```

## Sibling experiments

Comparing four interpretations of the same song is a first-class operation, not
a shell loop:

```python
nw.clone_project(
    "the_bells",
    "the_bells_v1_lipsync",
    preserve=("song", "lyrics", "characters"),
    reset=("script", "shots", "output", ".nw"),
)

summaries = nw.summarize_all(["the_bells_v1", "the_bells_v2", "the_bells_v3"])
nw.apply_to_projects(roots, lambda p: nw.compose_report(p), parallel=True)
```

## API at a glance

```python
# Genres — the reusable production specialization
(
    nw.Genre,
    nw.Template,
    nw.genres,
    nw.register_genre,
    nw.get_genre,
    nw.list_genres,
)
(
    nw.genre_catalog,
    nw.describe_genre,
    nw.recommend_genre,
    nw.resolve_defaults,
    nw.GENRE_STATUSES,
)
(
    nw.GenreResolver,
    nw.genre_resolvers,
    nw.register_genre_resolver,
    nw.resolve_genre,
)
(
    nw.GenreInitializer,
    nw.genre_initializers,
    nw.register_genre_initializer,
    nw.initialize_genre,
)
(
    nw.GenreProjectFactory,
    nw.genre_project_factories,
    nw.register_genre_project_factory,
    nw.has_genre_project_factory,
    nw.create_genre_project,
)

# Transforms — the A -> B arrow
(
    nw.Transform,
    nw.BaseTransform,
    nw.TransformInputs,
    nw.TransformResult,
)
nw.transforms, nw.register_transform, nw.get_transform, nw.list_transforms

# Folder facade
nw.Project, nw.Project.init, nw.CharacterImage

# Schema
(
    nw.ProjectSpec,
    nw.ProjectSummary,
    nw.SectionSpec,
    nw.ShotSpec,
)
nw.CharacterRef, nw.EnvironmentRef, nw.SongInfo, nw.SCHEMA_VERSION

# Render workflow
nw.prepare_shot, nw.plan_render_shot, nw.execute_render, nw.ShotPreparation

# Async jobs
nw.jobs.estimate, nw.jobs.enqueue, nw.jobs.list_jobs
nw.jobs.get_job, nw.jobs.cancel_job, nw.jobs.to_dict, nw.jobs.JobsConfig

# Strategies
(
    nw.Strategy,
    nw.get_strategy,
    nw.list_strategies,
)
nw.register_strategy, nw.strategies

# Storyboard
(
    nw.open_storyboard,
    nw.save_storyboard,
    nw.storyboard_from_shots,
)
(
    nw.plan_render_panel_images,
    nw.execute_render_panel_images,
)
nw.storyboard_db_path, nw.project_asset_id

# Inspect / QA
(
    nw.shot_report,
    nw.compose_report,
    nw.ShotReport,
    nw.ComposeReport,
)
nw.FrozenSegment, nw.Gap

# Graph / provenance
(
    nw.ProjectGraph,
    nw.derived_from,
    nw.descendants_of,
    nw.stale_after,
)
nw.annotations_at_tier, nw.iter_all_annotations, nw.open_project_stores

# Experiments
nw.clone_project, nw.apply_to_projects, nw.summarize_all

# Migration
nw.migrate_to_graph, nw.is_migrated
```

## Design notes

- **SSOT on the folder.** Every typed value comes from `project.json` plus the
  project graph. Tools never have to invent their own storage.
- **Genres are declarations, not engines.** `Project`, the prepare → plan →
  execute split, freshness, `nw.jobs`, and the cost gate are all genre-agnostic
  and serve every genre unchanged.
- **Open registries throughout.** Genres, Transforms, and render strategies are
  all `xdol.Registry` entries keyed by string, so apps extend `nw` without
  modifying it. `on_conflict="error"` keeps one plugin from silently shadowing
  another's.
- **Plan-then-execute.** Cost is computed and inspectable before anything bills.
- **Provenance by default.** Every render and every curator decision is written
  to the graph with `was_derived_from`, so freshness analysis is a graph walk,
  not a heuristic.

Longer rationale lives in `misc/docs/` — *Rendering Provenance and Partial
Re-render* and *Execution Semantics and Fan-out*.

## Known limits

Two places where the substrate currently promises less than it looks like it does:

- **`stale_after` has no early cutoff** — it is `descendants_of` under another
  name, pure reachability over `was_derived_from`, comparing no timestamp and no
  content digest. A regeneration that changes nothing still reports everything
  downstream as stale
  ([#24](https://github.com/thorwhalen/nw/issues/24)). `descendants_of` — "what
  is downstream of this?" — is exactly right as it stands; it is `stale_after`
  that owes you a comparison.
- **`BaseTransform.execute` has no failure isolation** — one failing call in a
  fan-out Plan raises, and no annotations reach the graph for the calls that did
  succeed ([#25](https://github.com/thorwhalen/nw/issues/25)).

## Dependencies

`pydantic`, [`falaw`](https://pypi.org/project/falaw) (fal-AI planner),
[`lacing`](https://pypi.org/project/lacing) (annotation graph),
[`xdol`](https://pypi.org/project/xdol) (registry),
[`artful`](https://pypi.org/project/artful) (storyboard),
[`au`](https://pypi.org/project/au) (async job substrate),
[`dol`](https://pypi.org/project/dol) (storage).

Optional system tools: `ffmpeg` / `ffprobe` for audio slicing and QA reports.

## License

MIT — see [LICENSE](LICENSE).
