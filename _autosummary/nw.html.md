# nw

nw — Narrative Workflow.

Application-orchestration framework for audiovisual projects. A project is
a folder; a **genre** (music video, explainer, podcast clip, slideshow) is a
reusable specialization on top — the first-class successor to what nw
informally called an “app” (see [`nw.genres`](nw.genres.html.md#nw.genres) and issue #10).

Public surface:

- [`Project`](#nw.Project) — folder facade: read/write spec, character anchors,
  shot upserts, decision log, typed summary, session-resumption brief.
- [`ProjectSummary`](#nw.ProjectSummary) — typed read view of a project.
- [`ResumptionBrief`](#nw.ResumptionBrief) — “where we left off”: decision tail, what the
  last *authored* change reaches downstream, recorded spend, deterministic
  next actions. Its `caveats` field carries what those numbers do *not*
  know.
- [`clone_project()`](#nw.clone_project) — replaces `cp -r` for sibling experiments.
- [`apply_to_projects()`](#nw.apply_to_projects) — replaces shell for-loops across roots.
- Schema types: [`ProjectSpec`](#nw.ProjectSpec), [`SectionSpec`](#nw.SectionSpec), [`ShotSpec`](#nw.ShotSpec),
  [`CharacterRef`](#nw.CharacterRef), [`EnvironmentRef`](#nw.EnvironmentRef), [`SongInfo`](#nw.SongInfo).
- `nw.workflow` — the `prepare` → `plan` → `execute` render split
  (Plan/Execute over rendering; records render-result provenance).
- `nw.renderers` — render strategies.
- `nw.genres` — production genres (the reusable project specialization).
- `nw.pricing` — re-quoting a *persisted* plan at today’s rates
  ([`current_quote()`](#nw.current_quote), [`PlanQuote`](#nw.PlanQuote)). Any stored cost figure is an
  as-of-then fact; reporting one as current under-quotes the run once falaw’s
  rate tables move, so read it back through here (nw#74).

On rendering provenance and partial re-render (why choices, not just content,
are recorded as linked artifacts), see
`misc/docs/Rendering Provenance and Partial Re-render.md`.

### Functions

| [`parse_ref`](#nw.parse_ref)(text)                                    | The ordinal in a spoken reference, or `None` if it isn't one.                                                                        |
|-----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------|
| [`format_ref`](#nw.format_ref)(n)                                      | The one spelling we print.                                                                                                           |
| [`genre_catalog`](#nw.genre_catalog)()                                    | Every registered genre as a JSON-able catalog entry (sorted by slug).                                                                |
| [`describe_genre`](#nw.describe_genre)(slug)                               | One genre's catalog entry (raises [`KeyError`](https://docs.python.org/3/builtins/exceptions.html#KeyError) if the slug is unknown). |
| [`recommend_genre`](#nw.recommend_genre)(kind)                              | The slug of the genre whose `intake_kinds` contains `kind` (first in slug order), or `None` when `kind` is falsy / unmatched.        |
| [`resolve_defaults`](#nw.resolve_defaults)(genre[, template])                | Resolve a genre (+ optional template) to the params for a new project.                                                               |
| [`register_genre_resolver`](#nw.register_genre_resolver)(slug, resolver)            | Register a resolver for a genre slug; returns it for inline use.                                                                     |
| [`resolve_genre`](#nw.resolve_genre)(genre[, template])                   | Resolve a genre (+ optional template) to the standard creation envelope.                                                             |
| [`register_genre_initializer`](#nw.register_genre_initializer)(slug, initializer)      | Register an initializer for a genre slug; returns it for inline use.                                                                 |
| [`initialize_genre`](#nw.initialize_genre)(genre, project, \*[, ...])        | Seed a freshly-created `project` for `genre` (+ optional `template`).                                                                |
| [`register_genre_project_factory`](#nw.register_genre_project_factory)(slug, factory)      | Register a project factory for a genre slug; returns it for inline use.                                                              |
| [`has_genre_project_factory`](#nw.has_genre_project_factory)(slug)                    | True iff a plugged-in project factory is registered for `slug`.                                                                      |
| [`create_genre_project`](#nw.create_genre_project)(genre, caller, ...[, ...])    | Create + seed a new project for a PLUGGED-IN `genre` in `caller`'s space.                                                            |
| [`annotations_at_tier`](#nw.annotations_at_tier)(project_root, tier)            | Return every annotation at the given tier across all of the project's stores.                                                        |
| [`apply_to_projects`](#nw.apply_to_projects)(roots, fn, \*[, parallel])       | Apply `fn` to each project at `roots` and collect the results.                                                                       |
| [`clone_project`](#nw.clone_project)(src_root, dst_root, \*[, ...])       | Clone an nw project to a new root.                                                                                                   |
| [`backfill_traces`](#nw.backfill_traces)(project_root, \*[, execute])       | Bless a pre-trace project so the verifying-trace rule can read it (nw#58).                                                           |
| [`collect_orphan_traces`](#nw.collect_orphan_traces)(project_root)                | Drop verifying traces whose target annotation no longer exists.                                                                      |
| [`compose_report`](#nw.compose_report)(project, \*[, ...])                 | Per-shot reports + final-compose inspection in one call.                                                                             |
| [`derived_from`](#nw.derived_from)(project_root, annotation_id)          | Return the annotations this one was directly derived from.                                                                           |
| [`descendants_of`](#nw.descendants_of)(project_root, ancestor_id)          | Return every annotation whose provenance chain leads back to `ancestor_id`.                                                          |
| [`execute_render`](#nw.execute_render)(prep, plan, \*[, on_event, ...])    | Execute a Plan, materialize the result as `shot_dir/output.mp4`.                                                                     |
| [`execute_render_panel_images`](#nw.execute_render_panel_images)(project, ...[, ...])   | Execute `plan`, download each artifact, attach a PanelImage.                                                                         |
| [`get_genre`](#nw.get_genre)(slug)                                    | Look up a genre by slug; raises [`KeyError`](https://docs.python.org/3/builtins/exceptions.html#KeyError) with the known slugs.      |
| [`get_strategy`](#nw.get_strategy)(name)                                 | Look up a strategy by name; raises if unknown.                                                                                       |
| [`get_transform`](#nw.get_transform)(name)                                | Look up a Transform instance by name; raises with the known names.                                                                   |
| [`is_migrated`](#nw.is_migrated)(project_root)                          | True iff this project has been migrated to the lacing graph.                                                                         |
| [`iter_all_annotations`](#nw.iter_all_annotations)(project_root)                 | Walk every annotation in every store under a project (any backend).                                                                  |
| [`list_genres`](#nw.list_genres)()                                      | Return all registered genre slugs (sorted).                                                                                          |
| [`list_strategies`](#nw.list_strategies)()                                  | Return all registered strategy names (sorted).                                                                                       |
| [`list_transforms`](#nw.list_transforms)()                                  | Return all registered Transform names (sorted).                                                                                      |
| [`migrate_to_graph`](#nw.migrate_to_graph)(project_root, \*[, backup, ...])  | Migrate `project_root`'s project.json into the lacing graph.                                                                         |
| [`open_project_stores`](#nw.open_project_stores)(project_root)                  | Yield an iterator of open stores, one per scope, honouring the backend.                                                              |
| [`open_storyboard`](#nw.open_storyboard)(project)                           | Load the project's storyboard.                                                                                                       |
| [`plan_render_panel_images`](#nw.plan_render_panel_images)(storyboard, \*[, ...])    | Build a Plan that generates a seed image for each panel that lacks one.                                                              |
| [`plan_render_shot`](#nw.plan_render_shot)(prep, \*[, quality, ...])         | Build a `falaw.Plan` for rendering a prepared shot.                                                                                  |
| [`cost_records`](#nw.cost_records)(plan)                                 | The JSON-able per-call cost rows nw persists in a decision payload.                                                                  |
| [`current_quote`](#nw.current_quote)(plan, \*[, pricers])                 | Re-quote `plan` at today's rates and report the result honestly.                                                                     |
| [`plan_from_cost_records`](#nw.plan_from_cost_records)(records)                    | Rebuild a re-quotable `falaw.Plan` from [`cost_records()`](#nw.cost_records) rows.                        |
| [`quote_from_cost_records`](#nw.quote_from_cost_records)(records, \*[, pricers])    | Today's price for the calls stored in a decision payload.                                                                            |
| [`quote_render_decision`](#nw.quote_render_decision)(payload, \*[, pricers])      | Today's price for a `render_shot` decision payload.                                                                                  |
| [`unquotable`](#nw.unquotable)(reason)                                 | A quote for something that could not be re-quoted at all.                                                                            |
| [`prepare_shot`](#nw.prepare_shot)(project, shot_id, \*[, upload])       | Resolve all local inputs for rendering a shot.                                                                                       |
| [`project_asset_id`](#nw.project_asset_id)(project)                          | The asset_id used for storyboard panel references.                                                                                   |
| [`register_genre`](#nw.register_genre)(genre)                              | Register a [`Genre`](#nw.Genre) under its `slug`; returns it for inline use.                       |
| [`register_strategy`](#nw.register_strategy)(name, impl)                      | Register a strategy.                                                                                                                 |
| [`register_transform`](#nw.register_transform)(name[, impl])                   | Register a Transform under `name`.                                                                                                   |
| [`transform_catalog`](#nw.transform_catalog)()                                | Every registered Transform as a JSON-able capability entry (sorted by name).                                                         |
| [`stamp_transform_identity`](#nw.stamp_transform_identity)(plan, transform)          | Fold `transform.impl_version` into every call's cache identity.                                                                      |
| [`work_item_instance_id`](#nw.work_item_instance_id)(transform_name, ...)         | The instance id of one fan-out unit: UUIDv5 of `(transform_name, mapping_key)`.                                                      |
| [`fan_out_plan`](#nw.fan_out_plan)(transform, project, items, \*, ...)   | Plan one Transform across `items` — each unit an ordinary `plan()` call.                                                             |
| [`fan_out_execute`](#nw.fan_out_execute)(transform, project, fan_out, \*)   | Execute a planned fan-out, one ordinary `transform.execute` per unit.                                                                |
| [`as_secrets`](#nw.as_secrets)(secrets)                                | Coerce a caller-supplied mapping to [`Secrets`](#nw.Secrets); empty → `None`.                        |
| [`redact`](#nw.redact)(text, secrets)                              | `text` with every secret value replaced by `<redacted:name>`.                                                                        |
| [`redact_exception`](#nw.redact_exception)(error, secrets)                   | The exception to re-raise so that nothing it *renders* carries a secret.                                                             |
| [`using_secrets`](#nw.using_secrets)(secrets)                             | Bind the secrets nw itself knows how to use, for the duration of a block.                                                            |
| [`save_storyboard`](#nw.save_storyboard)(project, storyboard, \*, ...)      | Persist a Storyboard into the project's SqliteStore.                                                                                 |
| [`shot_report`](#nw.shot_report)(project, shot_id, \*[, ...])           | Inspect `shots/<shot_id>/output.mp4` and return a typed report.                                                                      |
| [`menu`](#nw.menu)(\*[, cost])                                   | Every registered check, name-ordered — what a user chooses from.                                                                     |
| [`plan_checks`](#nw.plan_checks)(selection)                             | Order the selection into waves that may each run concurrently.                                                                       |
| [`register_check`](#nw.register_check)([check])                            | Add a check to the menu, as a call or as a decorator.                                                                                |
| [`suggest`](#nw.suggest)(request, \*[, include_paid])               | Checks whose `example_requests` look like what the user just asked for.                                                              |
| [`validate`](#nw.validate)(target, \*[, checks, max_workers, ...])   | Run `checks` against `target` and report.                                                                                            |
| [`all_stale`](#nw.all_stale)(project_root)                            | Every annotation that is currently stale, regardless of cause.                                                                       |
| [`stale_after`](#nw.stale_after)(project_root, changed_id)              | Return every annotation that `changed_id` actually invalidated.                                                                      |
| [`stale_verdicts`](#nw.stale_verdicts)(project_root, changed_id)           | Classify every annotation downstream of `changed_id`.                                                                                |
| [`stale_verdicts_all`](#nw.stale_verdicts_all)(project_root)                   | Classify every derived annotation in the project — the snapshot form.                                                                |
| [`storyboard_db_path`](#nw.storyboard_db_path)(project)                        | Return the path to the project's storyboard SQLite store.                                                                            |
| [`storyboard_from_shots`](#nw.storyboard_from_shots)(project, \*[, title, style]) | Build a one-panel-per-shot draft Storyboard from a project's shots.                                                                  |
| [`summarize_all`](#nw.summarize_all)(roots)                               | Convenience: return a [`ProjectSummary`](#nw.ProjectSummary) for each project.                              |

### Classes

| [`Deliverable`](#nw.Deliverable)(path, content_type, filename[, ...])   | A finished thing a person can watch, hear, or download.                                                                     |
|-----------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------|
| [`Resolver`](#nw.Resolver)(\*args, \*\*kwargs)                       | `resolve(email, project_id, artifact_id) -> Deliverable` — a genre's half.                                                  |
| [`BaseTransform`](#nw.BaseTransform)()                                    | Default [`Transform`](#nw.Transform) implementation.                                          |
| [`CharacterImage`](#nw.CharacterImage)(path, \*[, from_ref, ...])          | One image associated with a character.                                                                                      |
| [`CharacterRef`](#nw.CharacterRef)(\*\*data)                             | Pointer to a character folder under `characters/<name>/`.                                                                   |
| [`ComposeReport`](#nw.ComposeReport)(\*\*data)                            | Inspection of the project-level final composed video.                                                                       |
| [`FreshnessVerdict`](#nw.FreshnessVerdict)(annotation, is_stale, reason)     | Why one reachable annotation was judged stale (or not).                                                                     |
| [`DecisionEntry`](#nw.DecisionEntry)(\*\*data)                            | One entry of a project's decision log, flattened for display.                                                               |
| [`EnvironmentRef`](#nw.EnvironmentRef)(\*\*data)                           | Pointer to an environment folder under `environments/<name>/`.                                                              |
| [`FrozenSegment`](#nw.FrozenSegment)(\*\*data)                            | A run of consecutive frames whose pixels don't change.                                                                      |
| [`Gap`](#nw.Gap)(\*\*data)                                      | A gap on the timeline between two shots.                                                                                    |
| [`Genre`](#nw.Genre)(slug, title[, description, ...])             | A reusable definition of a *production kind* over the nw substrate.                                                         |
| [`Template`](#nw.Template)(slug, title[, description, params])       | A named preset ("subgenre") *within* a genre — a filled-in default config.                                                  |
| [`Project`](#nw.Project)(root, \*[, auto_migrate])                  | A folder-backed nw project.                                                                                                 |
| [`ProjectGraph`](#nw.ProjectGraph)(project_root)                         | Typed read/write facade over the project's lacing graph store.                                                              |
| [`StoredUnproducedOutput`](#nw.StoredUnproducedOutput)(annotation_id, body)        |                                                                                                                             |
| [`UnproducedOutputBodyV1`](#nw.UnproducedOutputBodyV1)(\*\*data)                   | Body of an unproduced-output record.                                                                                        |
| [`ProjectSpec`](#nw.ProjectSpec)(\*\*data)                              | The top-level project SSOT, persisted as `project.json`.                                                                    |
| [`ProjectSummary`](#nw.ProjectSummary)(\*\*data)                           | Typed read view of a project — what `muvid status` printed, but typed.                                                      |
| [`ResumptionBrief`](#nw.ResumptionBrief)(\*\*data)                          | A "where we left off" snapshot, returned by [`nw.Project.resumption_brief()`](#nw.Project.resumption_brief). |
| [`SectionSpec`](#nw.SectionSpec)(\*\*data)                              | A non-overlapping span of the project's master timeline.                                                                    |
| [`ShotPreparation`](#nw.ShotPreparation)(project_root, shot, ...[, ...])    | Local-only inputs for rendering a single shot.                                                                              |
| [`ShotReport`](#nw.ShotReport)(\*\*data)                               | Inspection of one rendered shot.                                                                                            |
| [`ShotSpec`](#nw.ShotSpec)(\*\*data)                                 | A timeline-locked visual unit.                                                                                              |
| [`SongInfo`](#nw.SongInfo)(\*\*data)                                 | Metadata for the master audio file.                                                                                         |
| [`Strategy`](#nw.Strategy)(\*args, \*\*kwargs)                       | Render-strategy contract.                                                                                                   |
| [`Transform`](#nw.Transform)(\*args, \*\*kwargs)                      | A swappable, costed function from A-annotations to B-annotations.                                                           |
| [`TransformInputs`](#nw.TransformInputs)(primary[, context])                | The annotations a Transform consumes.                                                                                       |
| [`TransformResult`](#nw.TransformResult)(annotations[, artifacts, ...])     | The outputs of a Transform's [`execute()`](#nw.Transform.execute).                                    |
| [`FailedOutput`](#nw.FailedOutput)(skeleton, status[, reason, ...])      | An output annotation that was planned but never produced.                                                                   |
| [`PlanQuote`](#nw.PlanQuote)(\*, total_usd, status, ...[, reason])    | Today's price for a persisted plan, with the stale figure alongside.                                                        |
| [`WorkItem`](#nw.WorkItem)(\*\*data)                                 | One unit of a fan-out — the PDG-shaped work item (nw#26).                                                                   |
| [`FanOutUnit`](#nw.FanOutUnit)(item, instance_id, plan, skeleton)      | One planned unit: a work item plus its ordinary Transform plan.                                                             |
| [`FanOutPlan`](#nw.FanOutPlan)(transform_name, units)                  | The planned fan-out — pure data, like every plan in this federation.                                                        |
| [`FanOutItemResult`](#nw.FanOutItemResult)(item, instance_id, status)        | One unit's outcome, aligned 1:1 with the plan's units.                                                                      |
| [`FanOutResult`](#nw.FanOutResult)(transform_name, items)                | A fan-out run: one [`FanOutItemResult`](#nw.FanOutItemResult) per planned unit, in order.            |
| [`Secrets`](#nw.Secrets)([mapping])                                 | A read-only `{provider_name: key}` mapping that never prints or persists.                                                   |
| [`Check`](#nw.Check)(name, summary, run[, requires, ...])         | One validation, and everything a scheduler and a menu need to know.                                                         |
| [`CheckResult`](#nw.CheckResult)(name[, findings, skipped, ...])        | What one check produced, including the case where it could not run.                                                         |
| [`Finding`](#nw.Finding)(check, severity, message[, where, ...])    | One thing a check noticed.                                                                                                  |
| [`ValidationReport`](#nw.ValidationReport)(target[, results, elapsed_s])     | Everything a [`validate()`](#nw.validate) run produced.                                      |

### Exceptions

| [`CacheModeConflict`](#nw.CacheModeConflict)       | `use_cache=False` and `force=True` were passed together.                                       |
|--------------------------------------------------------------------------|------------------------------------------------------------------------------------------------|
| [`ValidationError`](#nw.ValidationError)(report) | Raised by [`ValidationReport.raise_if_failed()`](#nw.ValidationReport.raise_if_failed). |

### *class* nw.BaseTransform

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Default [`Transform`](#nw.Transform) implementation.

Subclasses set the class attributes (`name`, `input_kinds`,
`output_kind`, optionally `params_model`) and implement `plan()`.
The default `execute()` runs the Plan, maps artifacts onto skeletons
1:1 via `_complete_annotation()`, writes to the project graph, and
reports cost. Transforms whose artifact→annotation mapping is not 1:1
(e.g. `clips_to_animatic`: N inputs → 1 output) override `execute()`.

That 1:1 mapping is an **invariant, checked before anything is spent**:
`execute()` raises when `len(skeleton) != len(plan.calls)`, the
same guard `nw.storyboard.execute_render_panel_images` already applies
to its own plan/id pairing. The zip below would otherwise stop at the
shorter sequence and drop the surplus with no error and no record —
harmless only for as long as the executor returns exactly one artifact
per call, which is precisely what per-call failure isolation changes.

#### generate_when *: [Literal](https://docs.python.org/3/library/typing.html#typing.Literal)['static', 'dynamic']* *= 'dynamic'*

When this Transform’s fan-out cardinality is knowable — `"static"`
or `"dynamic"` (nw#26). The default is `"dynamic"`: fail
expensive-looking, so an undeclared shape can never let a cost gate
quote a number for a cardinality nobody knows yet. Declare `"static"`
only when the work-item list is derivable from the graph before the
run (“one image per panel”).

#### impl_version *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)* *= '1'*

Behaviour version — bump on “same interface, changed behaviour”, never
rename the registry key for it. See the [`Transform`](#nw.Transform) Protocol for
the full contract. At the default, no cache salt is applied, so every
key ever issued stays byte-identical; the first real bump is the first
salt.

#### is_batch *: [bool](https://docs.python.org/3/builtins/functions.html#bool)* *= False*

Whether `plan()` consumes all of `inputs.primary` at once (batch)
or a single primary annotation (one-to-one — the default). See the
[`Transform`](#nw.Transform) Protocol for the full contract. Batch Transforms
(`extract_*`, `clips_to_animatic`) set this to `True`.

#### params_model

alias of [`None`](https://docs.python.org/3/builtins/constants.html#None)

### *exception* nw.CacheModeConflict

Bases: [`ValueError`](https://docs.python.org/3/builtins/exceptions.html#ValueError)

`use_cache=False` and `force=True` were passed together.

A `ValueError` subclass so callers that already catch `ValueError`
(and falaw’s own refusal of the same corner) keep working, while a caller
that wants to distinguish this one specific contradiction can.

### *class* nw.CharacterImage(path, , from_ref=False, from_selected=False, is_anchor=False)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One image associated with a character.

Returned by [`Project.list_character_images()`](#nw.Project.list_character_images). Distinguishes:

- `from_ref`: file lives under `characters/<name>/refs/` — a candidate
  from generation or upload.
- `from_selected`: under `characters/<name>/selected/` — curator-picked.
- `is_anchor`: this is the file the character card currently points at as
  the “use this image” anchor (lipsync seed, etc.).

### *class* nw.CharacterRef(\*\*data)

Bases: `BaseModel`

Pointer to a character folder under `characters/<name>/`.

The stable-attribute fields mirror
[`nw.bodies.CharacterRefBodyV1`](nw.bodies.html.md#nw.bodies.CharacterRefBodyV1) field-for-field, and that is
load-bearing rather than cosmetic: [`nw.Project.read_spec()`](#nw.Project.read_spec) builds
a `CharacterRef` from the graph body and
[`nw.Project.write_spec()`](#nw.Project.write_spec) writes the body back from the
`CharacterRef`. Any field present on the body but missing here is
**silently erased** by the next `update_spec` — which is what used to
happen to `reference_image_urls`. Add a field to one, add it to both.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'ignore'}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.Check(name, summary, run, requires=(), parallel_safe=True, cost='cheap', example_requests=(), requires_binaries=())

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One validation, and everything a scheduler and a menu need to know.

#### name

dotted and stable — it is what a user selects and what a
`requires` refers to.

#### summary

one line, for the menu.

#### run

`(target, context) -> findings`. May also return a
`(findings, produced)` pair when other checks depend on it.

#### requires

names of checks that must run first, whose `produced`
values arrive in `context`. Cycles raise at plan time.

#### parallel_safe

whether it may run alongside its independent peers.
`False` for anything that is not thread-safe or that saturates
the machine on its own (a full decode).

#### cost

rough wall-clock class — `"free"` (no subprocess),
`"cheap"` (seconds), `"dear"` (a full pass over the media), or
`"paid"` (spends money, e.g. a hosted OCR or transcription).
`"paid"` is never selected by [`suggest()`](#nw.suggest); it must be asked
for by name.

#### example_requests

things a person actually says that mean they want
this check. What lets a menu of forty be navigated, and what an
MCP surface matches against instead of exposing an enum.

#### requires_binaries

external programs it shells out to. A missing one
makes the check *skip with a reason*, never silently pass.

### *class* nw.CheckResult(name, findings=(), skipped='', error='', elapsed_s=0.0, produced=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

What one check produced, including the case where it could not run.

`skipped` and `error` are kept distinct from “found nothing”, because
conflating them is how a validation suite comes to report all-clear on a
machine where half of it never ran. A missing binary is not a pass.

#### *property* ok *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

Ran, and found nothing at or above `FAILING_SEVERITY`.

### *class* nw.ComposeReport(\*\*data)

Bases: `BaseModel`

Inspection of the project-level final composed video.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.DecisionEntry(\*\*data)

Bases: `BaseModel`

One entry of a project’s decision log, flattened for display.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'ignore'}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.Deliverable(path, content_type, filename, artifact_id='', project_id='', genre='', ref=None, title=None, duration_s=None, size_bytes=None, created_at=None, meta=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A finished thing a person can watch, hear, or download.

`path` is server-side and never leaves the host; it is what the transport
streams. Everything else exists so the host does not have to guess:

- `content_type` — what to serve it as. The genre knows; the host would
  otherwise re-derive it from a suffix.
- `filename` — what it should be called when it lands in someone’s
  Downloads folder. `music_video_test_02-cut-4.mp4` beats `b02fc05417ea`.
- `ref` — the speakable label (see [`format_ref()`](#nw.format_ref)).
- `artifact_id` — the stable, unambiguous id. `ref` is the convenience;
  this is the truth, and it is what a signed token is minted against.

The optional descriptive fields are what a listing surface renders, and what
lets a watch page say “10 seconds, 4.4 MB, made yesterday” without opening
the file.

#### *property* kind *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)*

`'video'`, `'audio'`, `'image'` or `'file'` — how to present it.

Derived from `content_type` so a genre never has to declare it twice.

```pycon
>>> Deliverable(Path('a.mp4'), 'video/mp4', 'a.mp4').kind
'video'
>>> Deliverable(Path('a.mp3'), 'audio/mpeg', 'a.mp3').kind
'audio'
>>> Deliverable(Path('a.pdf'), 'application/pdf', 'a.pdf').kind
'file'
```

#### *property* label *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)*

The best short name for a human — the ref if it has one, else the id.

```pycon
>>> Deliverable(Path('a.mp4'), 'video/mp4', 'a.mp4', ref='cut 4').label
'cut 4'
>>> Deliverable(Path('a.mp4'), 'video/mp4', 'a.mp4', artifact_id='b02f').label
'b02f'
```

#### meta *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)*

Genre-specific extras a listing or watch page may show. Free-form on
purpose — the host renders what it recognises and ignores the rest, so a
genre can enrich its own surface without a change here.

### *class* nw.EnvironmentRef(\*\*data)

Bases: `BaseModel`

Pointer to an environment folder under `environments/<name>/`.

Mirrors [`nw.bodies.EnvironmentRefBodyV1`](nw.bodies.html.md#nw.bodies.EnvironmentRefBodyV1) field-for-field, for the
same load-bearing reason as [`CharacterRef`](#nw.CharacterRef) — see that docstring.
`reference_image_urls` (the lookbook the FE curates for a *location*)
was erased by every `update_spec` until this mirror was completed.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'ignore'}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.FailedOutput(skeleton, status, reason='', error=None, blocked_by=())

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

An output annotation that was planned but never produced.

Carries the *skeleton* rather than an id because the skeleton is what the
caller planned and what a retry would re-submit — and because a UI needs its
body to say which panel is missing, not just that something is.

#### blocked_by *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[int](https://docs.python.org/3/builtins/functions.html#int), ...]*

Indices of the calls whose failure blocked this one.

#### error *: [BaseException](https://docs.python.org/3/builtins/exceptions.html#BaseException) | [None](https://docs.python.org/3/builtins/constants.html#None)*

The original exception, for a caller that classifies on falaw’s typed
hierarchy (`FalRateLimited` is worth retrying; `FalAccountLocked` is not).

#### reason *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)*

upstream panel 47
was filtered”\* rather than an unexplained hole.

* **Type:**
  Human-readable cause, from falaw. Renders as 

  ```
  *
  ```

  ”skipped

#### skeleton *: Annotation*

The annotation that would have been completed.

#### status *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)*

`"failed"` (its own call failed) or `"blocked"` (an upstream one did).

### *class* nw.FanOutItemResult(item, instance_id, status, result=None, error=None, reason='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One unit’s outcome, aligned 1:1 with the plan’s units.

### *class* nw.FanOutPlan(transform_name, units)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The planned fan-out — pure data, like every plan in this federation.

Cost arithmetic follows falaw#18’s honest form exactly (same names, same
semantics): [`known_cost_usd`](#nw.FanOutPlan.known_cost_usd) is the priced part, and a correct
gate reads it **together with** [`unknown_call_count`](#nw.FanOutPlan.unknown_call_count) — the true
cost is the known sum *plus an unknown amount* over that many calls,
and the gate refuses when the count is nonzero rather than pretending
the unknown part is free.

#### *property* has_unknown_costs *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

True if any unit has a billable call with no price.

#### *property* known_cost_usd *: [float](https://docs.python.org/3/builtins/functions.html#float)*

Sum of every unit plan’s priced, non-cache-hit calls.

#### *property* unknown_call_count *: [int](https://docs.python.org/3/builtins/functions.html#int)*

How many billable calls across all units carry no price.

### *class* nw.FanOutResult(transform_name, items)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A fan-out run: one [`FanOutItemResult`](#nw.FanOutItemResult) per planned unit, in order.

`len(result.items) == len(fan_out.units)` always — the same alignment
guarantee falaw’s `ExecutionReport` gives one level down.

#### *property* cost_usd_actual *: [float](https://docs.python.org/3/builtins/functions.html#float)*

Observed spend over the units that ran. A lower bound, like
[`TransformResult.cost_usd_actual`](#nw.TransformResult.cost_usd_actual) (whose caveat about billed-
but-failed calls applies per unit).

#### *property* has_unknown_costs *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

True when the run’s true spend is not fully known.

Two sources, both counted: a surviving unit whose own report says
so, and any **failed** unit — a unit that raised mid-execute may
have been billed for calls its (discarded) report would have
carried, so its spend is unknown by construction. Without the
second clause, a failed run could read “all costs known, $0.00
spent” — the exact under-report the federation’s unknown-cost rule
exists to prevent. `blocked` units never ran and are known-$0.

#### to_record()

The run record — where work items live (never the graph document).

JSON-serializable as returned, provided every item’s `attributes`
is (their contract; a violation raises here, naming the item).
Annotations and artifacts are referenced by id; the annotations
themselves were already written to the graph by each unit’s ordinary
`execute`, and duplicating their bodies here would make the record
a second, driftable copy.

`failed_count` / `blocked_count` count outputs **within** a unit
(zero when the unit itself failed — its result is `None`); the
unit-level outcome is `status`. A consumer counting failed *units*
counts statuses, not these fields.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### *class* nw.FanOutUnit(item, instance_id, plan, skeleton)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One planned unit: a work item plus its ordinary Transform plan.

### *class* nw.Finding(check, severity, message, where='', remedy=None, evidence=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One thing a check noticed.

A finding is never a bare boolean. Whoever reads it — a human deciding
whether to publish, or a model deciding what to fix — needs to know *where*
in the work it is and *what* would make it go away, and a check that cannot
say those two things has not finished its job.

#### check

the name of the check that produced it.

#### severity

`"info"`, `"warn"` or `"error"`; only `"error"`
makes a report not `ok`.

#### message

what is wrong, in one sentence a human can act on.

#### where

where in the work — a timestamp, a frame index, a shot id, a
path. Free-form because the checks are, but never empty for
anything above `"info"`.

#### remedy

what would fix it, when the check knows. `None` when it
honestly does not.

#### evidence

anything a reader would want to look at — an extracted
frame’s path, the numbers behind the verdict.

### *class* nw.FreshnessVerdict(annotation, is_stale, reason, upstream_id=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Why one reachable annotation was judged stale (or not).

Emitted by [`stale_verdicts()`](#nw.stale_verdicts). `reason` is one of the
`REASON_*` constants; `upstream_id` names the parent that decided it
when a single parent did, so “why is this stale?” has an answer that does
not require re-deriving the walk by hand.

### *class* nw.FrozenSegment(\*\*data)

Bases: `BaseModel`

A run of consecutive frames whose pixels don’t change.

A short freeze (≤ 0.25s) is usually a model artifact; a long one (≥ 1s)
is almost always a bug — Hailuo Pro returning a too-short clip + a tpad
fallback that froze the last frame, etc.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.Gap(\*\*data)

Bases: `BaseModel`

A gap on the timeline between two shots.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.Genre(slug, title, description='', body_schema_uris=(), transform_names=(), strategy_names=(), projection_entrypoint=None, folder_conventions=<factory>, status='available', templates=(), intake_kinds=(), cost_profile=None, defaults=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A reusable definition of a *production kind* over the nw substrate.

Pure data: it *references* substrate pieces by name rather than owning
them, so declaring a genre never touches the engine.

```pycon
>>> slideshow = Genre(
...     slug="slideshow",
...     title="Slideshow",
...     description="Stills over narration, assembled to a video.",
...     transform_names=("clips_to_animatic.ffmpeg",),
...     projection_entrypoint="clips_to_animatic.ffmpeg",
... )
>>> slideshow.title
'Slideshow'
>>> slideshow.status
'available'
```

`projection_entrypoint`, when given, must be one of the genre’s own
declared transforms or strategies:

```pycon
>>> Genre(slug="bad", title="Bad", projection_entrypoint="nope")
Traceback (most recent call last):
    ...
ValueError: Genre 'bad': projection_entrypoint 'nope' is not among its transform_names or strategy_names
```

#### cost_profile *: [str](https://docs.python.org/3/builtins/stdtypes.html#str) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

A short discriminator slug routing the cost gate to the right estimator
(e.g. `"tts"` = per-character audio, `"per_clip"` = per-render video).
The real numbers stay in the app; this is only the routing tag.

#### defaults *: [Mapping](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any)]*

The “start from scratch” params for this genre (same opaque shape as a
[`Template`](#nw.Template)’s `params`) — used when no template is chosen.

#### intake_kinds *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), ...]* *= ()*

Intake “what are you making?” answers that select this genre (the edge
[`recommend_genre()`](#nw.recommend_genre) walks). App data (e.g. reelee’s intake form) owns
the vocabulary; the genre just declares which answers it covers.

#### is_ready()

True iff every declared transform and strategy is registered.

A `planned` genre may legitimately be *not* ready; an `available`
one that isn’t ready is a wiring bug worth catching in a test.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

#### list_templates()

This genre’s Template slugs, in declared order.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

#### missing_strategies()

Declared `strategy_names` not (yet) present in `nw.renderers`.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

#### missing_transforms()

Declared `transform_names` not (yet) present in `nw.transforms`.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

#### template(slug)

Look up one of this genre’s [`Template`](#nw.Template)s by slug (KeyError if absent).

* **Return type:**
  [`Template`](#nw.Template)

#### templates *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[Template](#nw.Template), ...]* *= ()*

Named presets (“subgenres”) within this genre — see [`Template`](#nw.Template).

#### to_dict()

A JSON-able catalog entry — the shape apps serve to a frontend / MCP client.

Templates are emitted with their opaque `params` (not flattened), and
`intake_kinds`/`cost_profile`/`defaults` ride at the genre level, so a
consumer needs no app-specific knowledge to render the catalog.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### *class* nw.PlanQuote(, total_usd, status, as_of_total_usd, repriced, reason='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Today’s price for a persisted plan, with the stale figure alongside.

The stale figure is kept — as [`as_of_total_usd`](#nw.PlanQuote.as_of_total_usd), explicitly named
“as of then” — because an audit surface wants to show the movement. What
it must never do is *present* it as current; that is what
[`total_usd`](#nw.PlanQuote.total_usd) and [`status`](#nw.PlanQuote.status) are for.

#### as_of_total_usd *: [float](https://docs.python.org/3/builtins/functions.html#float) | [None](https://docs.python.org/3/builtins/constants.html#None)*

What the persisted plan said, `None` if it already said unknown.

A fact about the moment it was written. Render it labelled as such, or
not at all.

#### *property* basis_changed *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

True when a rate table moved underneath at least one call.

The audit answer the frozen number could never give: it separates “the
price changed because the *table* changed” from “the price changed
because the plan did”. Read it beside [`status`](#nw.PlanQuote.status) — a `changed`
with this `False` is a caller quoting different quantities, not a
repricing event.

#### *property* delta_usd *: [float](https://docs.python.org/3/builtins/functions.html#float) | [None](https://docs.python.org/3/builtins/constants.html#None)*

`total_usd - as_of_total_usd`, or `None` when either is unknown.

`None` rather than `0.0`: a plan that lost its price did not move
by zero.

#### *property* has_unknown_costs *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

True when the total cannot be known — the gate’s refusal condition.

The same judgement as `falaw.Plan.has_unknown_costs`, made at
re-quote time rather than at plan time.

#### reason *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)*

Why the *whole* quote is unknown, when that is the situation.

Set by [`unquotable()`](#nw.unquotable) (the thing handed in was not a plan) and by
[`quote_render_decision()`](#nw.quote_render_decision) (the payload contradicts itself). Empty for
an ordinary re-quote, where the per-call reasons live on
[`repriced`](#nw.PlanQuote.repriced) instead.

#### repriced *: RepricedPlan*

falaw’s per-call diff — `status`, `basis_changed`, `reason` per
call. Read it for the audit view; [`total_usd`](#nw.PlanQuote.total_usd) is the headline.

#### status *: [Literal](https://docs.python.org/3/library/typing.html#typing.Literal)['unchanged', 'changed', 'unknown']*

Which of the three cases this plan fell into — see `QuoteStatus`.

#### to_dict()

JSON-able headline for a surface (an API response, a job record).

Deliberately not the whole per-call diff: a spend surface needs the
number, whether it is knowable, and whether it moved. Reach into
[`repriced`](#nw.PlanQuote.repriced) for the rest.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

#### total_usd *: [float](https://docs.python.org/3/builtins/functions.html#float) | [None](https://docs.python.org/3/builtins/constants.html#None)*

Today’s billable total, or `None` when any billable call is
unpriceable today. `None` means unknown, never free (nw invariant #2).

### *class* nw.Project(root, , auto_migrate=True)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A folder-backed nw project.

Construct from a path (must exist + must contain `project.json`); use
[`Project.init()`](#nw.Project.init) to bootstrap a new project on disk.

#### add_character(name, , description='')

Add a character (idempotent: re-adds update the description).

Re-adding updates *only* the description: any stable attributes
already recorded on the character (costume, palette anchors,
`do_not_do` …) are carried over, so calling this again is not a
way to lose them.

* **Return type:**
  [`CharacterRef`](nw.schema.html.md#nw.schema.CharacterRef)

#### *classmethod* init(root, , title='', song=None, force=False)

Create a new project on disk and return the [`Project`](#nw.Project) facade.

* **Parameters:**
  * **root** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – Folder to create. Must not exist (or pass `force=True` to
    overwrite an empty folder).
  * **title** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Optional human-readable title; defaults to the folder name.
  * **song** (`Union`[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path), [`None`](https://docs.python.org/3/builtins/constants.html#None)]) – Optional path to a master audio file. When given, the file
    is *copied* into `<root>/song/` and registered in the spec.
  * **force** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – When True, accept an existing folder if it’s empty (no
    `project.json`); refuse if a project already exists there.
* **Return type:**
  [`Project`](nw.project.html.md#nw.project.Project)

#### list_character_images(name)

Return all images associated with a character, with provenance flags.

Walks `characters/<name>/refs/` and `characters/<name>/selected/`.
Marks the file the card’s `reference_image_path` points at as
`is_anchor=True`.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`CharacterImage`](nw.project.html.md#nw.project.CharacterImage)]

#### log_decision(kind, \*\*payload)

Record a typed decision in the project graph + the JSONL audit log.

Decisions are project-local provenance: which character anchor was
picked, which model overrode the default, why a shot was retried.
Both surfaces stay in sync:

- The lacing graph (`decision` tier, body schema
  `annot://schema/decision/v1`) is the SSOT — reelee will surface
  these in inspector / network views.
- `.nw/decisions.jsonl` continues as a tail-grep-able audit trail.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### read_spec()

Read the project spec, synthesizing from the graph for graph-native fields.

Project-level metadata (title, song, global_style, notes,
schema_version) lives in `project.json`. Sections, shots,
characters, and environments live in the lacing graph and are
synthesized into the returned [`ProjectSpec`](#nw.ProjectSpec) for back-compat
with code that still reads via `read_spec()`.

* **Return type:**
  [`ProjectSpec`](nw.schema.html.md#nw.schema.ProjectSpec)

#### read_summary()

Return a typed read view of the project — all the facts at once.

* **Return type:**
  [`ProjectSummary`](nw.schema.html.md#nw.schema.ProjectSummary)

#### resolved_genre()

The `{genre, template, params}` envelope this project was created as.

The read accessor for the envelope `nw.genres.initialize_genre()`
persists at creation (nw#32) — same shape as
`nw.genres.resolve_genre()` returns, so consumers reuse or diff
the *effective* creation params without re-deriving them. `None`
for a project with no recorded genre (created before nw#32, or not
through the genre machinery).

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

#### resumption_brief(, recent=10)

Return a “where we left off” snapshot for the start of a session.

Pure data, fully offline: a decision-log tail, what is reachable
downstream of the last change, recorded spend, and a deterministic
list of suggested next actions. reelee renders it as prose and
injects it as the opening context of a session.

Read [`ResumptionBrief`](nw.schema.html.md#nw.schema.ResumptionBrief) before trusting the numbers —
two of them are upper bounds, and the brief says so in
`caveats` rather than only in a
docstring.

* **Parameters:**
  **recent** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – How many decision-log entries to include, most recent last.
* **Return type:**
  [`ResumptionBrief`](nw.schema.html.md#nw.schema.ResumptionBrief)

#### set_character_anchor(name, image_path)

Pick an existing image as the character’s anchor (lipsync seed, etc.).

Returns the updated card. Raises if the image isn’t under the
character’s folder, since cross-character anchoring is almost always
a mistake.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

#### set_global_style(style)

Set the project-level visual style hint.

* **Return type:**
  [`ProjectSpec`](nw.schema.html.md#nw.schema.ProjectSpec)

#### set_song(source, , copy=True)

Register an audio file as the project’s master song.

Probes duration / sample-rate / bitrate via `mixing.audio.Audio` if
available, else leaves them at 0 (the spec accepts the SSOT-only
path with placeholder metadata).

* **Return type:**
  [`SongInfo`](nw.schema.html.md#nw.schema.SongInfo)

#### set_title(title)

Set the project title.

* **Return type:**
  [`ProjectSpec`](nw.schema.html.md#nw.schema.ProjectSpec)

#### total_spend_usd()

Sum the cost recorded on every decision in the project.

Prefers each decision’s *actual* per-artifact `cost_usd` and falls
back to its `total_estimated_cost_usd` when no artifact costs were
recorded.

**Deliberately not re-quoted.** This is money that was *billed*, and a
receipt is not a quote: re-pricing it through
[`nw.pricing.current_quote()`](nw.pricing.html.md#nw.pricing.current_quote) would rewrite history at today’s
rates. The consequence is that the estimate-based fallback is an
as-of-then figure — falaw’s tables have moved since (0.0.46 tenfold
upward on premium LLM calls), so a decision that recorded no artifact
costs contributes what it was quoted then, not what the same render
would cost now. That is the right answer for “what did this project
spend”; it is the wrong one for “what would this cost today”, and
[`nw.pricing.quote_render_decision()`](nw.pricing.html.md#nw.pricing.quote_render_decision) is what answers that (nw#74).

Walks **every store scope** (graph, storyboard, alignment), not just
the project graph: a decision written to the storyboard scope is money
that was spent, and counting only one scope would silently *under*-report
while `caveats` claims an upper bound.

**This is an upper bound on money usefully spent.** A render that was
billed and then failed is recorded exactly like one that succeeded,
because nothing in the execution layer records a per-branch outcome
yet. When failure isolation lands, this should sum over the *produced*
branches only — and this method is the one place that changes.

* **Return type:**
  [`float`](https://docs.python.org/3/builtins/functions.html#float)

#### update_spec(\*\*changes)

Apply field-level changes to the spec; return the new spec.

* **Return type:**
  [`ProjectSpec`](nw.schema.html.md#nw.schema.ProjectSpec)

#### write_spec(spec)

Write the spec.

For back-compat with existing code that builds a `ProjectSpec` and
calls `write_spec`, this routes graph-backed fields (sections,
shots, characters, environments) through the graph and persists the
rest as project.json metadata.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### *class* nw.ProjectGraph(project_root)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Typed read/write facade over the project’s lacing graph store.

Use `Project.graph()` to get one rather than constructing directly.
Each method opens-and-closes the underlying store so concurrent
reads/writes from different processes are safe (SqliteStore is
file-locked).

#### add_annotation(ann, , instance_id=None, call_index=None)

Write one annotation to the project graph, plus its verifying trace.

Registers `ann.tier` if it isn’t a known tier yet — `SqliteStore`
enforces a foreign key on `tier`, so writing under a fresh tier
(e.g. a Transform output kind) would otherwise fail. `add_tier` is
idempotent, so this is a no-op for the built-in project tiers.

This is the single choke point every *derived* annotation in nw and
reelee passes through, which is why the verifying trace
([`nw.bodies.verifying_trace`](nw.bodies.verifying_trace.html.md#module-nw.bodies.verifying_trace)) is recorded here rather than in
`derive_provenance`: that helper returns a `Provenance` and has
no store to write to, and threading one in would change a signature
with production callsites in three repos. Writing at persist time
also covers the paths that build a `Provenance` by hand.

Annotations with no `was_derived_from` parents get no trace — there
is nothing to verify, and they are nobody’s descendant.

It is also where a matching [`add_unproduced_output()`](#nw.ProjectGraph.add_unproduced_output) record is
retired (nw#44): a successful write is proof the thing that record
described has now been produced. `instance_id` / `call_index`
identify *this write’s* unit precisely (see
[`nw.bodies.unproduced_output`](nw.bodies.unproduced_output.html.md#module-nw.bodies.unproduced_output)’s module docstring for the key);
omit `instance_id` only when it is not known — the retirement then
falls back to `(transform_name, call_index, upstream)`, which still
cannot distinguish two DIFFERENT units sharing both an upstream set
and a `call_index` (the residual case the module docstring names),
so it may retire nothing, or (rarely) the wrong record.

\*\*Bypassing this method (a raw `store.add`) leaves a matching
unproduced-output record in place\*\* — it reads as a live blocker
after reload even though this write produced the thing it described.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### add_unproduced_output(skeleton, , transform_name, status, reason='', error=None, blocked_by=(), instance_id=None, call_index=None, was_attributed_to=None)

Persist why a planned output was never produced (nw#44).

Mirrors one entry of `TransformResult.failed` / `.blocked`
([`nw.transforms.FailedOutput`](#nw.FailedOutput)) — pass its `skeleton`,
`status`, `reason`, `error` and `blocked_by` straight through.
Written under `nw.bodies.UNPRODUCED_OUTPUT_BODY_SCHEMA_URI`,
never under `skeleton.body_schema_uri` (see the module’s docstring
on why that tier is reserved for what was actually produced).

`instance_id` (a fan-out unit’s
`work_item_instance_id()`) and
`call_index` (this output’s position within its `execute()`
call’s skeleton tuple) together form the identity
[`add_annotation()`](#nw.ProjectGraph.add_annotation) retires by — see
[`nw.bodies.unproduced_output`](nw.bodies.unproduced_output.html.md#module-nw.bodies.unproduced_output)’s module docstring for the full
key. **Dedupes on that identity**: a record already outstanding for
the same key is removed before this one is written, so a unit
failing twice leaves one current record, not two.

Parentless, like a verifying trace.

`reason` never stores raw exception text — see the module
docstring’s “reason never carries raw exception text”. When
`error` is given, the original `reason` is logged
(`logging.getLogger("nw.graph")`, `WARNING`) and the persisted
`reason` becomes a fixed sentence naming `error`’s type.

* **Return type:**
  [`UUID`](https://docs.python.org/3/library/uuid.html#uuid.UUID)

#### append_decision(body, , was_attributed_to='user:nw', was_derived_from=())

Append a decision; never replaces an existing one (the log is append-only).

* **Return type:**
  [`UUID`](https://docs.python.org/3/library/uuid.html#uuid.UUID)

#### genre_envelope()

The recorded genre envelope, or `None` for a genre-less project.

The read half of [`set_genre_envelope()`](#nw.ProjectGraph.set_genre_envelope); consumers should reach
it through [`nw.Project.resolved_genre()`](#nw.Project.resolved_genre), which returns the
plain-dict envelope shape `nw.genres.resolve_genre()` produces.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`GenreEnvelopeBodyV1`](nw.bodies.genre_envelope.html.md#nw.bodies.genre_envelope.GenreEnvelopeBodyV1)]

#### remove_annotation(annotation_id)

Remove one annotation from the project graph, with its verifying traces.

The delete counterpart of [`add_annotation()`](#nw.ProjectGraph.add_annotation) (nw#36): every trace
whose `for_annotation_id` names `annotation_id` goes with it, so a
deletion never leaves a sidecar behind — an orphaned trace is inert
for freshness but grows the store without bound, and if the id is
later re-used it can even answer a freshness query from digests
recorded for content that is no longer there.

Only the project graph store is touched — the store
[`add_annotation()`](#nw.ProjectGraph.add_annotation) writes to. Annotations living in the other
scopes (storyboard, alignment) are removed by their own facades;
[`collect_orphan_traces()`](#nw.collect_orphan_traces) is the project-wide backstop.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)
* **Returns:**
  Whether `annotation_id` itself was present (its traces are
  removed either way).

#### set_genre_envelope(body, , was_attributed_to='agent:nw.genres')

Record the resolved `{genre, template, params}` envelope; return its id.

Singleton per project (nw#32): the tier is the identity, so
re-initializing replaces the recorded envelope in place — the
annotation id is stable across replacements, like every entity
upsert. A no-op write (same envelope) writes nothing.

* **Return type:**
  [`UUID`](https://docs.python.org/3/library/uuid.html#uuid.UUID)

#### unproduced_outputs(, transform_name=None)

Every unproduced-output record still outstanding, oldest first.

A record disappears the moment [`add_annotation()`](#nw.ProjectGraph.add_annotation) writes a real
output for the same identity, or another [`add_unproduced_output()`](#nw.ProjectGraph.add_unproduced_output)
call for the same identity supersedes it (dedupe) — what this
returns is exactly “still missing”, survives a reload, and is not a
cache of any in-memory `TransformResult`.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`StoredUnproducedOutput`](nw.graph.html.md#nw.graph.StoredUnproducedOutput)]

#### upsert_character_ref(body, , was_attributed_to='user:nw')

Insert-or-update the character ref with this `name`; return its id.

The id is *stable* across edits — see `_upsert()`.

* **Return type:**
  [`UUID`](https://docs.python.org/3/library/uuid.html#uuid.UUID)

#### upsert_environment_ref(body, , was_attributed_to='user:nw')

Insert-or-update the environment ref with this `name`; return its id.

The id is *stable* across edits — see `_upsert()`.

* **Return type:**
  [`UUID`](https://docs.python.org/3/library/uuid.html#uuid.UUID)

#### upsert_section(body, interval, , was_attributed_to='user:nw')

Insert-or-update the section with this `section_id`; return its id.

The id is *stable* across edits — see `_upsert()`.

* **Return type:**
  [`UUID`](https://docs.python.org/3/library/uuid.html#uuid.UUID)

#### upsert_shot(body, interval, , was_attributed_to='user:nw')

Insert-or-update the shot with this `shot_id`; return its id.

The id is *stable* across edits — see `_upsert()`.

* **Return type:**
  [`UUID`](https://docs.python.org/3/library/uuid.html#uuid.UUID)

### *class* nw.ProjectSpec(\*\*data)

Bases: `BaseModel`

The top-level project SSOT, persisted as `project.json`.

Field names and order are chosen to round-trip identically with muvid’s
ProjectSpec for `schema_version=1`, so the_bells_v\* fixtures (and any
other muvid-shaped project) load and re-save without churn.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'ignore'}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.ProjectSummary(\*\*data)

Bases: `BaseModel`

Typed read view of a project — what `muvid status` printed, but typed.

Returned by [`Project.read_summary()`](#nw.Project.read_summary). Holds the small facts the user
most often wants: title, root, song path, counts of characters / shots /
sections / output, plus a coarse “stages_done” list naming the lifecycle
stages that have been reached.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'ignore'}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

#### *property* stages_done *: [list](https://docs.python.org/3/builtins/stdtypes.html#list)[[str](https://docs.python.org/3/builtins/stdtypes.html#str)]*

Coarse stage list — what’s been reached, in lifecycle order.

### *class* nw.Resolver(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

`resolve(email, project_id, artifact_id) -> Deliverable` — a genre’s half.

`artifact_id` may be a raw id OR a reference the genre accepts (see
[`parse_ref()`](#nw.parse_ref)); resolving both is the genre’s job, because only it knows
the ordering that gives `cut 4` its meaning.

Raises `KeyError` when nothing resolves (the host answers 404) and
`PermissionError` when it resolves but is not the caller’s (403). Never
let a server path escape in the message.

### *class* nw.ResumptionBrief(\*\*data)

Bases: `BaseModel`

A “where we left off” snapshot, returned by [`nw.Project.resumption_brief()`](#nw.Project.resumption_brief).

Pure data: no fal calls, no LLM, no network. reelee renders it as prose
and injects it as the first tool-result of a session.

\*\*The field names are chosen to be honest about what nw can currently
measure\*\*, because a confidently wrong number is worse than no number:

- `downstream_of_last_authored_change` is *not* “stale”. It is
  `nw.descendants_of` — pure provenance reachability, comparing no
  content and no timestamp — so this set includes everything already
  regenerated since the change. It is an **upper bound** on what needs
  attention, and it is named for what it measures.

  `nw.stale_after` is the narrower answer and it now cuts off early
  (nw#24), so switching this field to it would return a smaller and
  correct set. That is deliberately **not** done here: the field would
  then be named for the wrong measurement, and which of the two a
  resumption brief should show is nw#7’s call, not nw#24’s. Callers who
  want the exact set can call `nw.stale_after` with
  `last_authored_change_id`.
- The walk starts at the last **authored** change — the most recent
  annotation the user wrote (a shot, a section, a character or
  environment ref), never one a Transform derived. Walking from “the
  newest annotation” instead would be inverted: the newest node in a
  provenance graph is by construction a *leaf*, so its descendant set is
  empty in exactly the case the field exists for.
- `total_spend_usd` sums *every* recorded render decision across
  every store scope. Nothing records per-branch outcomes yet, so a render
  that failed after being billed is counted here exactly like one that
  succeeded. Also an upper bound.

`caveats` carries those qualifications as data — so a consumer
renders them next to the numbers instead of rediscovering them.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'ignore'}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.Secrets(mapping=None, , \*\*named)

Bases: [`Mapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

A read-only `{provider_name: key}` mapping that never prints or persists.

Construct from a mapping, keywords, or both; `None`/empty values are
dropped (absent means “not supplied”), a non-`str` key or value is a
`TypeError` — a secret is text, and an int or a bytes object here is a
caller bug worth failing on.

#### with_(\*\*named)

A copy with `named` layered on top (a boundary adding a provider).

* **Return type:**
  [`Secrets`](nw.secrets.html.md#nw.secrets.Secrets)

### *class* nw.SectionSpec(\*\*data)

Bases: `BaseModel`

A non-overlapping span of the project’s master timeline.

`label` is free-form (“intro”, “verse”, “chorus”, “scene-1”, “act-2”,
…) so different apps (music-video, explainer, podcast-clip) can use
their own taxonomy.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'ignore'}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.ShotPreparation(project_root, shot, shot_dir, audio_slice_path, audio_slice_url='', character_anchor_paths=<factory>, character_anchor_urls=<factory>, environment_anchor_path=None, environment_anchor_url='', lyric_lines=<factory>, storyboard_prompt='', global_style='')

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

fal-storage URL of the audio slice (set by [`prepare_shot()`](#nw.prepare_shot) when
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

### *class* nw.ShotReport(\*\*data)

Bases: `BaseModel`

Inspection of one rendered shot.

#### *property* has_long_freeze *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

Any freeze ≥ 1.0s is suspicious. Anything ≥ 0.5s is worth flagging.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.ShotSpec(\*\*data)

Bases: `BaseModel`

A timeline-locked visual unit.

`[start_s, end_s)` is half-open. `render_strategy` is an open string
rather than a closed Literal, so apps can register their own strategies
via [`nw.renderers.register_strategy()`](nw.renderers.html.md#nw.renderers.register_strategy) (Phase 1b.3) without modifying
the schema.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'ignore'}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.SongInfo(\*\*data)

Bases: `BaseModel`

Metadata for the master audio file.

Compatible with muvid’s SongInfo by field name and type.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'ignore'}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.StoredUnproducedOutput(annotation_id, body)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

### *class* nw.Strategy(\*args, \*\*kwargs)

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

### *class* nw.Template(slug, title, description='', params=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A named preset (“subgenre”) *within* a genre — a filled-in default config.

AV-general: the substrate owns the Template’s identity (`slug`/`title`/
`description`) and carries an opaque `params` payload it does **not**
interpret. The app that owns the genre puts meaning in `params` (reelee:
`{"output_intent": ..., "flavor": ...}`; braidio: `{"format_id": ...}`)
and validates/resolves it. Frozen + hashable (`params` is excluded from
identity and normalized to an immutable mapping), so a Template can live in a
`Genre.templates` tuple without breaking the genre’s `__hash__`.

```pycon
>>> t = Template(slug="cinematic_clip", title="Cinematic clip",
...              params={"flavor": "fal.cinematic"})
>>> t.params["flavor"]
'fal.cinematic'
>>> t.to_dict()["params"]
{'flavor': 'fal.cinematic'}
```

#### to_dict()

A JSON-able catalog entry: `{slug, title, description, params}`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### *class* nw.Transform(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

A swappable, costed function from A-annotations to B-annotations.

Implementations usually subclass [`BaseTransform`](#nw.BaseTransform) rather than
satisfying this Protocol directly, but the Protocol is the contract the
registry and orchestrator depend on.

#### execute(project, plan, skeleton, , use_cache=True, force=False, on_failure='halt', unit_instance_id=None, secrets=None)

Run `plan`, complete `skeleton`, write to the graph, return result.

`force=True` bypasses the cache **read** (the “regenerate this”
affordance) and keeps the cache **write**, so a forced re-run stays
reusable instead of billing the next consumer again (nw#72).
`use_cache=False` means “do not touch the cache at all”; pairing it
with `force=True` raises [`CacheModeConflict`](#nw.CacheModeConflict).

`on_failure` selects the failure policy — see `OnFailure`.
`"halt"` is the default so no existing caller changes behaviour.

#### WARNING
`on_failure` is **newer than some implementations**. A Transform
that overrides [`execute()`](#nw.Transform.execute) and predates nw#25 does not accept the
keyword, and this Protocol is `runtime_checkable`, which compares
*method names* and not signatures — so `isinstance` still passes
and the `TypeError` arrives at call time. reelee has ~18 such
overrides (thorwhalen/reelee#299 tracks the migration).

Until they are migrated, a caller iterating over arbitrary registered
Transforms should pass `on_failure` only to ones it knows accept
it, or catch `TypeError`. Everything inheriting
[`BaseTransform`](#nw.BaseTransform)’s `execute` — the common case — already
does.

`unit_instance_id` (nw#44) is the same accepts-it-or-not shape,
newer still: `fan_out_execute()` passes a
fan-out unit’s own
`work_item_instance_id()` here, when
accepted, as the precise identity an unproduced-output record is
retired by. Not a Transform’s concern beyond forwarding it to
[`nw.graph.ProjectGraph.add_unproduced_output()`](nw.graph.html.md#nw.graph.ProjectGraph.add_unproduced_output) /
[`add_annotation()`](nw.graph.html.md#nw.graph.ProjectGraph.add_annotation) — [`BaseTransform`](#nw.BaseTransform)
already does.

`secrets` is the third keyword of that shape, and the one that
carries a **credential**: the caller’s per-call, bring-your-own API
key(s), as a read-only `{provider_name: key}` mapping
([`nw.Secrets`](#nw.Secrets); every entry point coerces a plain mapping to
it). It is the one input that is deliberately *not an input* — it
never enters the Plan, the skeleton, provenance, the cache identity,
a run record, the job index or a log line — and a Transform that
spends a caller’s credential reads it here and nowhere else.
`None` (the default, and what every call did before the seam
existed) means “resolve from the process environment”.
`fan_out_execute()` and
[`nw.jobs.enqueue()`](nw.jobs.html.md#nw.jobs.enqueue) pass it accepts-it-or-not, so an override
that has no key to spend never sees it; [`BaseTransform`](#nw.BaseTransform) binds
a [`FAL_SECRET`](nw.secrets.html.md#nw.secrets.FAL_SECRET) as the fal credential for the
duration of the call, and `fan_out_execute` binds it around every
unit whether or not the keyword is accepted. An override that
declares the keyword and is called *directly* receives whatever the
caller passed — run it through [`nw.secrets.as_secrets()`](nw.secrets.html.md#nw.secrets.as_secrets) before
logging or formatting it. See [`nw.secrets`](nw.secrets.html.md#module-nw.secrets).

* **Return type:**
  [`TransformResult`](#nw.TransformResult)

#### generate_when *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)*

When this Transform’s fan-out cardinality is knowable (nw#26):
`"static"` (the work-item list is derivable before the run — a
pre-flight estimate is a real number) or `"dynamic"` (cardinality is
known only after an upstream call returns — the only honest pre-flight
estimate is *unknown*, which forces approval). Undeclared defaults to
`"dynamic"`: fail expensive-looking. See
`nw.transforms.fanout.GenerateWhen`.

#### impl_version *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)*

Behaviour version of this implementation (nw#27).

“Same interface, changed behaviour” — a prompt-template edit, a
post-processing change — bumps this \*\*without renaming the registry
key\*\* (the name denotes the capability; a different capability gets a
different name). It is a lock, not a receipt: it enters provenance
(`transform:<name>@<impl_version>`) and, when it is not
`DFLT_IMPL_VERSION`, the falaw cache identity of every call
executed through `BaseTransform.execute()` — so a behaviour change
cannot keep serving results minted by the old behaviour. A Transform
that overrides `execute` must apply
[`stamp_transform_identity()`](#nw.stamp_transform_identity) itself; the lock only locks what
passes through it.

#### input_kinds *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), ...]*

Body-schema URIs this Transform reads. The first is the *primary*
kind; the rest are context kinds.

#### is_batch *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

How [`plan()`](#nw.Transform.plan) consumes `inputs.primary`.

`False` (one-to-one): [`plan()`](#nw.Transform.plan) operates on a *single* primary
annotation (`inputs.primary[0]`) — e.g. `beat_to_panel`, one beat in,
one panel out. A caller wanting to apply it across many annotations calls
[`plan()`](#nw.Transform.plan) once per annotation and composes the Plans.

`True` (batch): [`plan()`](#nw.Transform.plan) consumes *all* of `inputs.primary` at
once — e.g. `extract_characters` (every beat → an LLM call) or
`clips_to_animatic` (every clip → one animatic). A caller passes the
whole set in a single [`plan()`](#nw.Transform.plan) call.

This is the property an orchestrator needs to fan a Transform across a
project’s annotations correctly — it can’t be inferred from
`input_kinds`.

#### name *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)*

Globally-unique identifier in [`transforms`](nw.transforms.html.md#nw.transforms).

#### output_kind *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)*

The body-schema URI this Transform produces.

#### params_model *: [type](https://docs.python.org/3/builtins/functions.html#type)*

Pydantic model class for this Transform’s per-call params;
`type(None)` means no params. On the Protocol — not just
[`BaseTransform`](#nw.BaseTransform) — so anything reading a Transform through the
contract (the capability catalogue, an MCP tool builder, the CLI
dispatcher) can rely on it (nw#27).

#### plan(project, inputs, , params=None)

Build a `falaw.Plan` + skeleton output annotations.

Pure data. No billable calls. The skeleton annotations have provenance
filled in; their bodies’ artifact references are placeholders that
[`execute()`](#nw.Transform.execute) replaces.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[`Plan`, [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[`Annotation`, [`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis)]]

### *class* nw.TransformInputs(primary, context=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The annotations a Transform consumes.

`primary` is the subject of the operation — a single-element tuple for
one-to-one Transforms, many for batch Transforms (e.g. `clips_to_animatic`
consumes every clip). `context` is side material keyed by kind name, so
a Transform that declares `input_kinds=(beat, character-ref)` receives
the Beat in `primary` and the CharacterRefs in `context["character-ref"]`.

### *class* nw.TransformResult(annotations, artifacts=(), cost_usd_actual=0.0, cache_hit_savings_usd=0.0, has_unknown_costs=False, failed=(), blocked=())

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The outputs of a Transform’s [`execute()`](#nw.Transform.execute).

#### annotations *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[Annotation, ...]*

The completed output annotation(s), written to the project graph.

#### artifacts *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[Artifact, ...]*

The `lacing.Artifact`s produced (images, videos, audio, json …).
Annotations reference these by `artifact_id` in their bodies.

#### blocked *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[FailedOutput](#nw.FailedOutput), ...]*

Outputs never attempted because an upstream call failed.

#### cache_hit_savings_usd *: [float](https://docs.python.org/3/builtins/functions.html#float)*

USD not spent because a call was served from cache.

Also observed rather than predicted — this changed source at the same time
as `cost_usd_actual`, from `Plan.cache_hit_savings_usd` (what planning
guessed would hit) to what actually hit.

#### cost_usd_actual *: [float](https://docs.python.org/3/builtins/functions.html#float)*

USD billed during execution, over the calls that \*\*succeeded and were not
cache hits\*\* — falaw’s observed `ExecutionReport.estimated_spend_usd`.
Since falaw#26 the per-`Artifact` `cost_usd` is *also* stamped from the
observed outcome, so the two now agree; the report stays the source here
because it is the run-level truth (and carries `has_unknown_costs`),
not because the artifacts lie anymore.

**A lower bound, despite the name.** falaw runs its converter *inside* the
unit of work, after the billed call, so a call fal charged for can still end
as `status="failed"` — and a failed call is excluded here, because falaw
cannot know whether the vendor billed it and inventing a number would be
worse. Under `"halt"` the run aborted anyway; under `"isolate"` it
continues, so a caller accumulating this across a fan-out with failures will
under-count. Read `failed` alongside it.

#### failed *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[FailedOutput](#nw.FailedOutput), ...]*

Outputs whose own call failed. Empty unless `on_failure="isolate"`.

#### has_unknown_costs *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

Whether any executed call had no price. Carried so `$0.00` stays
distinguishable from “we do not know”, which is the distinction every cost
gate in the federation is required to read.

#### *property* is_complete *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

Whether every planned output was produced.

### *class* nw.UnproducedOutputBodyV1(\*\*data)

Bases: `BaseModel`

Body of an unproduced-output record.

`status` mirrors `nw.transforms.fanout.UnitStatus`’s two
unproduced cases: `"failed"` (the call itself failed) or `"blocked"`
(an upstream call in the same plan failed first). `upstream` is stored
for the `call_index` fallback identity (see the module docstring); it
is not itself a sufficient key.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *exception* nw.ValidationError(report)

Bases: [`AssertionError`](https://docs.python.org/3/builtins/exceptions.html#AssertionError)

Raised by [`ValidationReport.raise_if_failed()`](#nw.ValidationReport.raise_if_failed).

### *class* nw.ValidationReport(target, results=(), elapsed_s=0.0)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Everything a [`validate()`](#nw.validate) run produced.

#### target

what was validated.

#### results

one per check that was selected, in the order they were run.

#### elapsed_s

wall-clock for the whole run.

#### *property* ok *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

No failing findings **and** nothing that failed to run.

A check that errored is not a pass. Callers gating a publish on this
get the conservative answer without having to remember to ask for it.

#### raise_if_failed()

Return self, or raise [`ValidationError`](#nw.ValidationError) — for a hard gate.

* **Return type:**
  [`ValidationReport`](nw.validation.html.md#nw.validation.ValidationReport)

#### summary()

A few lines a human can read without unpacking the object.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### *class* nw.WorkItem(\*\*data)

Bases: `BaseModel`

One unit of a fan-out — the PDG-shaped work item (nw#26).

`scope_interval` puts *time in the demand, not the graph* (Nuke’s
model): a pipeline that stores frame ranges in nodes must edit the graph
to change a range; one that stores them in the request does not. It is
an interval rather than a point because lacing’s `TimeInterval` admits
`start == end` as a valid point annotation — the point-demand case is
already representable, no second demand type needed.

#### *property* instance_id *: [UUID](https://docs.python.org/3/library/uuid.html#uuid.UUID)*

This item’s instance id is only defined *for a transform* — use
[`work_item_instance_id()`](#nw.work_item_instance_id). This property exists to raise a
helpful error instead of letting `item.instance_id` look like it
could mean something transform-free.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### nw.all_stale(project_root)

Every annotation that is currently stale, regardless of cause.

[`stale_verdicts_all()`](#nw.stale_verdicts_all) with the fresh verdicts dropped — the
snapshot counterpart of [`stale_after()`](#nw.stale_after), and the primitive a
freshness indicator or a “regenerate everything stale” verb should sit
on instead of re-deriving its own definition of the word.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[`Annotation`]

### nw.annotations_at_tier(project_root, tier)

Return every annotation at the given tier across all of the project’s stores.

Useful for reelee views that lens on a single annotation kind:
`annotations_at_tier(root, "shot")` returns every shot annotation
regardless of which store it lives in (project graph vs. storyboard
vs. alignment).

Asks each store for the tier rather than deserializing every annotation
and filtering. `by_tier` is a real indexed query on all four lacing
backends and was called by nothing in nw; this walked the whole project
to answer a question about one tier. Measured on 2000 annotations with
200 at the tier: **33.5 ms → 3.9 ms**, and the gap widens with project
size because one is O(all rows) and the other O(matching).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[`Annotation`]

### nw.apply_to_projects(roots, fn, , parallel=False)

Apply `fn` to each project at `roots` and collect the results.

* **Parameters:**
  * **roots** ([`Iterable`](https://docs.python.org/3/library/typing.html#typing.Iterable)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)]) – Iterable of project roots. Each must point to an existing
    nw project.
  * **fn** ([`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`Project`](nw.project.html.md#nw.project.Project)], [`TypeVar`](https://docs.python.org/3/library/typing.html#typing.TypeVar)(`T`)]) – Callable taking a [`Project`](#nw.Project) and returning anything. Use this
    for per-project operations: parsing a script, estimating cost,
    rendering, gathering reports.
  * **parallel** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – When True, run `fn` in a thread pool. Useful when `fn`
    is I/O- or API-bound (e.g. a render). When False (default), runs
    sequentially in submission order — the safest semantics.
* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`TypeVar`](https://docs.python.org/3/library/typing.html#typing.TypeVar)(`T`)]
* **Returns:**
  A list of `fn(project)` results in the same order as `roots`.

### Examples

```pycon
>>> # Estimate cost of all four sibling experiments without rendering:
>>> # totals = apply_to_projects(roots, lambda p: estimate_render_cost(p))
>>> # Apply the same script to all of them after a refactor:
>>> # apply_to_projects(roots, lambda p: parse_script(p))
```

### nw.as_secrets(secrets)

Coerce a caller-supplied mapping to [`Secrets`](#nw.Secrets); empty → `None`.

The nw entry points — `nw.BaseTransform.execute()`,
[`nw.fan_out_execute()`](#nw.fan_out_execute), [`nw.jobs.enqueue()`](nw.jobs.html.md#nw.jobs.enqueue) — run every incoming
`secrets` through this, so below *them* a Transform only ever sees the
redacting type. A Transform that **overrides** `execute` and is called
directly gets whatever the caller passed: an override that logs or
formats its `secrets` should `as_secrets` first (or the caller should
hand it a [`Secrets`](#nw.Secrets)), because a plain `dict` prints its values.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Secrets`](nw.secrets.html.md#nw.secrets.Secrets)]

```pycon
>>> as_secrets(None) is None
True
>>> as_secrets({"fal": None}) is None
True
>>> as_secrets({"fal": "k"})
Secrets(<1 redacted: fal>)
```

### nw.backfill_traces(project_root, , execute=False)

Bless a pre-trace project so the verifying-trace rule can read it (nw#58).

On a project whose annotations predate nw#24’s trace-writing, the
verifying-trace rule is a behavior change, not a wrapper: every derived
annotation reads stale (`no-trace`), and nothing heals them — regen
skips non-Transform-produced annotations, and body updates write no
trace. This writes, for each derived annotation with no trace, a trace
against its parents’ CURRENT content digests: blessing at-rest state as
fresh, which is exactly the old timestamp rule’s verdict for at-rest
data — semantics-preserving at the moment of migration.

**Report-only by default.** The first thing run against a real user’s
projects should be a read; pass `execute=True` to write. Idempotent
either way: an annotation that already has a usable trace is counted in
`already_traced` and never rewritten, so a partial run is simply
re-run rather than reasoned about. One caveat keeps “a read” honest at
the FILE level: stores are opened with `migrate=True`, so a store
stamped at an older lacing schema is upgraded ON OPEN even under the
default — run this only where the build that serves these stores is
already the new one (the D-vg-mcp-10 deploy ordering; a pre-migrated
file makes an old serving build refuse it).

**Not blessed, by design — the old rule’s own stale verdicts.** A parent
edited AFTER the annotation was derived is exactly the
pending-regeneration state the old timestamp rule reported stale;
blessing it would silently clear a real signal. Such annotations land in
`skipped` and stay no-trace-stale — same verdict, and a later regen
writes the true trace through the chokepoint. (Exact preservation in the
other direction is impossible — the trace rule recurses where the old
rule was one-hop — but that residual over-reports, the direction
[`nw.freshness`](nw.freshness.html.md#module-nw.freshness) documents as the safe one.)

What is deliberately NOT blessed, each with a `skipped` entry naming
the annotation and the reason:

- a parent that no longer exists — that annotation is genuinely
  `upstream-missing`, and a fabricated trace would hide a real hole;
- a parent list carrying artifact refs (64-hex asset ids) — the
  annotation-tier trace cannot cover them (nw#55), and a trace over a
  subset of the parents reads as stale anyway (“upstream set is not
  exactly `was_derived_from`”), so writing one would be decoration;
- a parent whose body cannot be digested — broken data at the producer,
  same rule as `build_verifying_trace()`.

Parentless annotations are never stale by contract, so they are counted
(`parentless`) and need nothing.

Returns one project’s report — callers migrating a tree of projects loop
and get per-project summaries for free:
`{"project", "stores_found", "examined", "backfilled", "already_traced",
"traced_unusable", "parentless",
"skipped": [{"annotation_id", "reason"}, ...], "executed"}`.
`backfilled` is the count of traces written when `execute=True`, and
of traces that WOULD be written otherwise; `executed` says which
reading applies. Read `stores_found` before trusting zeros: a typo’d
or empty root reports all-zero COUNTS, and `stores_found == 0` is what
distinguishes “nothing to migrate” from “not a project here”.
`traced_unusable` counts annotations whose existing trace the
freshness rule cannot use (foreign digest scheme, mismatched upstream
set) — permanently stale, deliberately not overwritten here; expect it
to be zero on genuine pre-trace projects. A broken project (corrupt
store, unreadable `project.json`) RAISES rather than reporting —
catch per root in a tree loop so one damaged project is recorded, not
silently averaged away.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### nw.clone_project(src_root, dst_root, , preserve=('song', 'lyrics', 'characters'), reset=('script', 'shots', 'output', '.nw'), title=None, force=False)

Clone an nw project to a new root.

* **Parameters:**
  * **src_root** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – Path to an existing nw project (must contain `project.json`).
  * **dst_root** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – Destination path. Must not exist (or pass `force=True` to
    overwrite).
  * **preserve** ([`Iterable`](https://docs.python.org/3/library/typing.html#typing.Iterable)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Subtrees of `src_root` to copy verbatim into `dst_root`.
    Default: `("song", "lyrics", "characters")`.
  * **reset** ([`Iterable`](https://docs.python.org/3/library/typing.html#typing.Iterable)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Subtrees of `dst_root` to (re)create as empty after copying.
    Default: `("script", "shots", "output", ".nw")`.
  * **title** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – New title for the cloned project. Defaults to `dst_root`’s
    folder name.
  * **force** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – When True, overwrite an existing `dst_root` (refuses by default
    to avoid clobbering work).
* **Return type:**
  [`ProjectSummary`](nw.schema.html.md#nw.schema.ProjectSummary)
* **Returns:**
  [`ProjectSummary`](#nw.ProjectSummary) of the cloned project.

### nw.collect_orphan_traces(project_root)

Drop verifying traces whose target annotation no longer exists.

The backstop for deletion paths that do not (or cannot) go through
`remove_annotations_with_traces()` — a direct `store.remove`, an
external tool, history from before deletions collected traces (nw#36).
Walks every store under the project; a trace is an orphan when its
`for_annotation_id` resolves in **none** of them. Idempotent, and safe
to run as routine maintenance: an orphaned trace is never consulted by
[`nw.freshness`](nw.freshness.html.md#module-nw.freshness), so removing it changes no freshness answer.

A trace whose body cannot be read (not a dict, unparseable target id) is
left in place: it may be an orphan, but deleting what we cannot identify
is worse than carrying it.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`UUID`](https://docs.python.org/3/library/uuid.html#uuid.UUID)]
* **Returns:**
  The ids of the trace annotations removed, in store order.

### nw.compose_report(project, , freeze_sample_fps=4.0, duration_tolerance_s=0.1)

Per-shot reports + final-compose inspection in one call.

* **Return type:**
  [`ComposeReport`](nw.inspect.html.md#nw.inspect.ComposeReport)

### nw.cost_records(plan)

The JSON-able per-call cost rows nw persists in a decision payload.

A serialized call cannot be re-quoted from `application` and
`arguments` alone — the quantity hints that priced it are estimator-only
and never reach the wire arguments. Carrying `cost_basis` alongside the
frozen figure is what makes the row re-quotable later by
[`quote_from_cost_records()`](#nw.quote_from_cost_records).

`cost_basis` is omitted when unset, exactly as falaw omits it, so a row
written by a caller that records no basis is byte-identical to what nw
wrote before nw#74.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]]

```pycon
>>> from falaw import CallPlan, Plan
>>> call = CallPlan(tool="t", application="a", arguments={},
...                 output_kind="video", estimated_cost_usd=1.0)
>>> sorted(cost_records(Plan(calls=(call,)))[0])
['application', 'cache_status', 'estimated_cost_usd', 'tool']
```

### nw.create_genre_project(genre, caller, project_id, , title=None, template=None)

Create + seed a new project for a PLUGGED-IN `genre` in `caller`’s space.

The *create*-counterpart to [`resolve_genre()`](#nw.resolve_genre) (params) + [`initialize_genre()`](#nw.initialize_genre)
(seed): resolve → the genre’s factory (create in the caller’s space) → initialize.
**All-or-nothing** — if seeding fails the just-created project is rolled back (its
folder removed) and the error re-raised. Returns the factory’s JSON-able info (minus
the live `project`) plus the resolved `{genre, template, params}` envelope, so the
caller can immediately address the new project (e.g. by `project_id`). The same
envelope is **persisted on the project** by the initialize step (nw#32) — under the
all-or-nothing guarantee, since a failure there rolls the whole create back — so the
association survives this call returning; read it back via
[`nw.Project.resolved_genre()`](#nw.Project.resolved_genre).

Raises [`KeyError`](https://docs.python.org/3/builtins/exceptions.html#KeyError) on an unknown genre/template, or a genre with no registered
factory (a host’s own genre is created by the host, not via this path).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### nw.current_quote(plan, \*, pricers={'llm_rates': Pricer(quote=<function \_quote_from_llm_rates>, table='falaw/data/llm_rates.json', version=<functools._lru_cache_wrapper object>), 'model_catalogue': Pricer(quote = <function \_quote_from_catalogue>, table='falaw/data/models.json', version=<functools._lru_cache_wrapper object>)})

Re-quote `plan` at today’s rates and report the result honestly.

Pure data — `falaw.reprice_plan()` reads the committed rate tables and
does arithmetic. No network, no billing API, no cache peek, so this is
safe to call anywhere a `plan()` is (nw invariant #1).

* **Parameters:**
  * **plan** (`Plan`) – The plan to re-quote — typically one just rebuilt from a stored
    payload with [`plan_from_cost_records()`](#nw.plan_from_cost_records).
  * **pricers** ([`Mapping`](https://docs.python.org/3/library/typing.html#typing.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), `Pricer`]) – Pricing rules by `falaw.CostBasis.pricer`. The seam for
    a caller with reconciled numbers of their own; see
    `falaw.reprice.Pricer`.
* **Return type:**
  [`PlanQuote`](nw.pricing.html.md#nw.pricing.PlanQuote)

```pycon
>>> from falaw import Plan
>>> current_quote(Plan(calls=())).status
'unchanged'
```

### nw.derived_from(project_root, annotation_id)

Return the annotations this one was directly derived from.

Walks `provenance.was_derived_from` *one hop only* across all of the
project’s stores.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[`Annotation`]

### nw.descendants_of(project_root, ancestor_id)

Return every annotation whose provenance chain leads back to `ancestor_id`.

Walks `provenance.was_derived_from` *transitively* across all of the
project’s lacing stores. This is the operation reelee’s freshness
analysis is built on (system overview §7): when a node changes, every
annotation in the closure of this set is “downstream of the change.”

Deterministic order: (generation time, id) — the same public ordering
contract as [`nw.freshness.stale_verdicts()`](nw.freshness.html.md#nw.freshness.stale_verdicts). The closure used to be
returned in set-iteration (hash-derived) order, which leaked into every
consumer’s output (nw#39).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[`Annotation`]

### nw.describe_genre(slug)

One genre’s catalog entry (raises [`KeyError`](https://docs.python.org/3/builtins/exceptions.html#KeyError) if the slug is unknown).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### nw.execute_render(prep, plan, , on_event=None, use_cache=True, project=None)

Execute a Plan, materialize the result as `shot_dir/output.mp4`.

Refuses to execute a plan-only Plan (one whose arguments still contain
`<plan-only:...>` placeholders) — those exist so the planner can show
cost without any uploads, and need to be replaced with real URLs (call
[`prepare_shot()`](#nw.prepare_shot) with `upload=True`) before execute.

* **Parameters:**
  * **prep** ([`ShotPreparation`](nw.workflow.html.md#nw.workflow.ShotPreparation)) – The [`ShotPreparation`](#nw.ShotPreparation) the Plan was built for.
  * **plan** (`Plan`) – A `falaw.Plan` (typically from [`plan_render_shot()`](#nw.plan_render_shot)).
  * **on_event** – Optional event subscriber forwarded to the falaw call layer.
  * **use_cache** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – When True (default), routes via `cached_call_fal` so
    cache hits skip the network.
  * **project** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Project`](nw.project.html.md#nw.project.Project)]) – Optional [`Project`](#nw.Project). When given, a render-decision
    annotation is appended to the project graph after execution
    with `was_derived_from = (shot_annotation_id,)`, so reelee’s
    freshness queries (`descendants_of` / `stale_after`) walk
    from the shot to its render output.
* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)
* **Returns:**
  Path to `shot_dir/output.mp4` (trimmed/padded to `prep.duration_s`).

### nw.execute_render_panel_images(project, storyboard, plan, panel_ids, , on_event=None, use_cache=True, on_failure='halt')

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

### nw.fan_out_execute(transform, project, fan_out, , use_cache=True, force=False, on_failure='isolate', secrets=None)

Execute a planned fan-out, one ordinary `transform.execute` per unit.

`on_failure` governs **both levels symmetrically**:

- within a unit, it is passed to the Transform’s `execute` (when the
  implementation accepts it — a pre-nw#25 override runs with its own
  halt-like behaviour inside the unit; cross-unit isolation still
  applies);
- across units, `"isolate"` (the default — it is the point of a
  fan-out) runs every unit and reports per-unit outcomes, while
  `"halt"` stops *submitting* units after the first raising unit and
  marks the rest `blocked`.

A unit whose `execute` **returns** is never a halt trigger, even when
its result is partial — the Transform already decided those failures
were survivable; only a raising unit halts.

Two protocol-violation shapes degrade rather than crash, deliberately:
an `execute` that rejects `use_cache`/`force` (or returns a
non-`TransformResult`) shows up as per-unit `failed` rows carrying
the `TypeError`/`AttributeError` — N identical rows for one
programming error reads worse than one loud raise, but the alternative
discards the run record for units that already spent. And a `**kwargs`
override that accepts-but-ignores `on_failure` runs its internal
default within the unit — undetectable by signature inspection in
principle; cross-unit policy is still honoured.

`use_cache` / `force` are forwarded per unit and mean what they mean
on [`Transform.execute()`](#nw.Transform.execute): `force` skips the cache **read** and keeps
the **write**, so re-forcing a 200-unit fan-out does not orphan 200 paid
results (nw#72). `use_cache=False, force=True` raises
`CacheModeConflict` **before the first unit runs**:
it is a contradiction decidable from the arguments alone, so it does not
get the degradation above — filing one programming error as N identical
failed rows is only the lesser evil for the shapes that cannot be checked
up front.

`secrets` — the caller’s per-call credentials ([`nw.Secrets`](#nw.Secrets);
any mapping is coerced) — reaches each unit two ways, so no registered
Transform can silently bill the server’s key. It is **passed** to
`execute` when the implementation declares the keyword (the same
accepts-it-or-not seam as `on_failure` and `unit_instance_id`), and it
is **bound** around every unit regardless — a `"fal"` secret is the fal
credential for the call ([`nw.secrets.using_secrets()`](nw.secrets.html.md#nw.secrets.using_secrets)) even for an
override that predates the seam. It reaches nothing else: not the units,
not the run record ([`FanOutResult.to_record()`](#nw.FanOutResult.to_record); a failing unit’s
`reason` is redacted), not a log line.

Units run **sequentially**. Concurrency *within* a unit is falaw’s
(`execute_plan_isolated` bounds it); concurrency *across* units is the
deferred-scheduler work nw#26 explicitly scopes out, and nothing here
forecloses it — units are planned independently and the result is
order-aligned, not order-dependent.

* **Return type:**
  FanOutResult

### nw.fan_out_plan(transform, project, items, , inputs_for, params=None)

Plan one Transform across `items` — each unit an ordinary `plan()` call.

`inputs_for` maps a work item to the [`TransformInputs`](#nw.TransformInputs)
its unit consumes; the item’s `attributes` carry any per-unit data it
needs to build them. No billable calls; pure data out.

Duplicate `mapping_key`s are refused: two units sharing a key share
an instance id, which destroys exactly the per-instance identity the key
exists to provide (retry, cost attribution, regenerate-just-this-one all
become ambiguous).

`stamp_transform_identity` is applied to each unit plan \*\*here, at plan
time\*\* — its own docstring asks orchestrators that hash or persist plans
before execution to do so, and a fan-out’s run record is such a
persistence. Idempotent, so `BaseTransform.execute()` re-stamping at
execute time changes nothing.

* **Return type:**
  FanOutPlan

### nw.format_ref(n)

The one spelling we print. Input is permissive; output never varies.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> format_ref(1), format_ref(42)
('cut 1', 'cut 42')
```

### nw.genre_catalog()

Every registered genre as a JSON-able catalog entry (sorted by slug).

This is the generic, app-agnostic catalog an HTTP route / MCP tool serves; see
[`Genre.to_dict()`](#nw.Genre.to_dict) for the entry shape.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### nw.get_genre(slug)

Look up a genre by slug; raises [`KeyError`](https://docs.python.org/3/builtins/exceptions.html#KeyError) with the known slugs.

* **Return type:**
  [`Genre`](#nw.Genre)

### nw.get_strategy(name)

Look up a strategy by name; raises if unknown.

* **Return type:**
  [`Strategy`](nw.renderers.html.md#nw.renderers.Strategy)

### nw.get_transform(name)

Look up a Transform instance by name; raises with the known names.

* **Return type:**
  [`Transform`](#nw.Transform)

### nw.has_genre_project_factory(slug)

True iff a plugged-in project factory is registered for `slug`.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### nw.initialize_genre(genre, project, , template=None, params=None)

Seed a freshly-created `project` for `genre` (+ optional `template`).

The side-effecting apply-counterpart to [`resolve_genre()`](#nw.resolve_genre). Dispatches to
the genre’s registered initializer ([`register_genre_initializer()`](#nw.register_genre_initializer)) when
one exists; **when none is registered this is a no-op** — the correct default
for a genre that seeds nothing on create (e.g. one whose preset is applied at
render time).

`params` is the resolved creation params (from [`resolve_genre()`](#nw.resolve_genre)); when
`None` it is resolved from the genre’s `template`/`defaults` here, so
`initialize_genre(genre, project)` seeds a project in the genre’s defaults in
one call. Raises [`KeyError`](https://docs.python.org/3/builtins/exceptions.html#KeyError) on an unknown genre or — **uniformly** — an
unknown `template` slug (matching [`resolve_genre()`](#nw.resolve_genre)).

**The envelope is persisted** (nw#32): after the initializer succeeds (or
no-ops), the resolved `{genre, template, params}` is recorded on the
project’s graph — so “what genre is this project?” stays answerable after
this call returns, on the host-creates path exactly as on
[`create_genre_project()`](#nw.create_genre_project)’s. Written *after* the seed on purpose: a
recorded envelope certifies a completed initialization, never a failed
one. A `project` stand-in without a `graph` (a test double, a
factory that returns no live project) skips the recording; read it back
via [`nw.Project.resolved_genre()`](#nw.Project.resolved_genre).

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

```pycon
>>> _ = register_genre(Genre(slug="_noinit_demo", title="Demo"))
>>> initialize_genre("_noinit_demo", object())  # no initializer -> no seed
>>> del genres["_noinit_demo"]
```

### nw.is_migrated(project_root)

True iff this project has been migrated to the lacing graph.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### nw.iter_all_annotations(project_root)

Walk every annotation in every store under a project (any backend).

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[`Annotation`]

### nw.list_genres()

Return all registered genre slugs (sorted).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### nw.list_strategies()

Return all registered strategy names (sorted).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### nw.list_transforms()

Return all registered Transform names (sorted).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### nw.menu(, cost=None)

Every registered check, name-ordered — what a user chooses from.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Check`](nw.validation.html.md#nw.validation.Check), [`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis)]

### nw.migrate_to_graph(project_root, , backup=True, was_attributed_to='agent:nw.migrate')

Migrate `project_root`’s project.json into the lacing graph.

Idempotent: returns `{"already_migrated": 1, ...}` with zero writes if
the sentinel exists. Otherwise reads `project.json`, writes equivalent
annotations into `project.annot.sqlite`, drops the migrated arrays from
`project.json`, and writes the sentinel.

* **Parameters:**
  * **project_root** ([`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – Path to a project root.
  * **backup** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – When True (default), copy the original `project.json` to
    `.nw/project.json.pre-graph.bak` before trimming.
  * **was_attributed_to** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Provenance for the migrator. Defaults to
    `"agent:nw.migrate"`; pass `"user:<handle>"` from a CLI.
* **Returns:**
  ```
  ``
  ```

  {“sections”: N, “shots”: N, “characters”: N, “environments”: N,
  : ”decisions”: N}\`\`.
* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`int`](https://docs.python.org/3/builtins/functions.html#int)]

### nw.open_project_stores(project_root)

Yield an iterator of open stores, one per scope, honouring the backend.

The backend-aware replacement for `for p in all_project_stores(...):
SqliteStore(p)`. Under SQLite it visits each existing per-scope file;
under Postgres it visits each scope’s tenant in the shared DB. Use it for
both reads (walk `.all()`) and writes (`.remove` / `.add`).

Each store is closed before the next opens, so consume each store’s
annotations before advancing.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[`IntervalAnnotationStore`]]

### nw.open_storyboard(project)

Load the project’s storyboard. Returns an empty one if not present.

* **Return type:**
  `Storyboard`

### nw.parse_ref(text)

The ordinal in a spoken reference, or `None` if it isn’t one.

`None` is the signal to fall through to treating the input as a raw
artifact id — which is why this never raises: “not an ordinal” is an
ordinary, expected answer, not an error.

* **Return type:**
  [`int`](https://docs.python.org/3/builtins/functions.html#int) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

```pycon
>>> parse_ref("cut 4"), parse_ref("CUT4"), parse_ref(" cut - 4 ")
(4, 4, 4)
>>> parse_ref("#11"), parse_ref("11")
(11, 11)
>>> parse_ref("b02fc05417ea") is None, parse_ref("") is None
(True, True)
```

Zero and negatives are not references — deliverables are numbered from 1, so
accepting `cut 0` would resolve to a neighbour under a naive index:

```pycon
>>> parse_ref("cut 0") is None
True
```

### nw.plan_checks(selection)

Order the selection into waves that may each run concurrently.

Every check in a wave has all its requirements satisfied by earlier waves,
so the waves are the schedule: run each in turn, in parallel within it.
A check that is not `parallel_safe` gets a wave to itself.

* **Raises:**
  [**ValueError**](https://docs.python.org/3/builtins/exceptions.html#ValueError) – on a dependency cycle, or a requirement that is not
      registered — both at plan time, before anything has been spent.
* **Return type:**
  [*tuple*](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[*tuple*](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[*Check*](nw.validation.html.md#nw.validation.Check), …], …]

### Examples

```pycon
>>> plan_checks(())
()
```

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Check`](nw.validation.html.md#nw.validation.Check), [`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis)], [`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis)]

### nw.plan_from_cost_records(records)

Rebuild a re-quotable `falaw.Plan` from [`cost_records()`](#nw.cost_records) rows.

The result is a **pricing** plan, not an executable one: `arguments` is
empty and `output_kind` is a placeholder, because a stored cost row does
not carry the wire payload and re-pricing does not read it. Never hand one
of these to `falaw.execute_plan()` — build a fresh plan for that.

Rows missing `cost_basis` come back basis-free, which is exactly what
makes them re-price as `"no_basis"` (unknown) rather than as their
frozen number.

* **Return type:**
  `Plan`

### nw.plan_render_panel_images(storyboard, , quality='balanced', image_size='landscape_16_9', model_id=None, only_missing=True)

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
  order as the Plan’s calls (so [`execute_render_panel_images()`](#nw.execute_render_panel_images)
  knows which panel each artifact belongs to).

### nw.plan_render_shot(prep, , quality='balanced', model_overrides=None)

Build a `falaw.Plan` for rendering a prepared shot.

Dispatches on `prep.shot.render_strategy` via [`nw.renderers`](nw.renderers.html.md#module-nw.renderers).

* **Parameters:**
  * **prep** ([`ShotPreparation`](nw.workflow.html.md#nw.workflow.ShotPreparation)) – A [`ShotPreparation`](#nw.ShotPreparation) from [`prepare_shot()`](#nw.prepare_shot).
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

### nw.prepare_shot(project, shot_id, , upload=True)

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
  * **project** ([`Project`](nw.project.html.md#nw.project.Project)) – An [`nw.Project`](#nw.Project) instance.
  * **shot_id** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – The shot’s id, as in `project.read_spec().shots[*].id`.
  * **upload** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – When True (default), upload local files to fal-storage and
    populate the `*_url` fields. When False, only the local paths
    are populated.
* **Return type:**
  [`ShotPreparation`](nw.workflow.html.md#nw.workflow.ShotPreparation)
* **Returns:**
  A [`ShotPreparation`](#nw.ShotPreparation) with local paths (and URLs if `upload`)
  ready to plan.

### nw.project_asset_id(project)

The asset_id used for storyboard panel references.

Uses the SHA-256 of the project’s song bytes when available, so the
asset_id matches whatever a downstream consumer would compute via
`lacing.hash_file()`. Falls back to a stable derived id when the
song isn’t available yet.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### nw.quote_from_cost_records(records, \*, pricers={'llm_rates': Pricer(quote=<function \_quote_from_llm_rates>, table='falaw/data/llm_rates.json', version=<functools._lru_cache_wrapper object>), 'model_catalogue': Pricer(quote = <function \_quote_from_catalogue>, table='falaw/data/models.json', version=<functools._lru_cache_wrapper object>)})

Today’s price for the calls stored in a decision payload.

The read-back half of [`cost_records()`](#nw.cost_records). `None` or a non-sequence
(a payload that recorded no calls at all) yields an empty plan’s quote —
`total_usd == 0.0`, `status == "unchanged"` — because “no calls” is a
known zero, not an unknown.

* **Return type:**
  [`PlanQuote`](nw.pricing.html.md#nw.pricing.PlanQuote)

### nw.quote_render_decision(payload, \*, pricers={'llm_rates': Pricer(quote=<function \_quote_from_llm_rates>, table='falaw/data/llm_rates.json', version=<functools._lru_cache_wrapper object>), 'model_catalogue': Pricer(quote = <function \_quote_from_catalogue>, table='falaw/data/models.json', version=<functools._lru_cache_wrapper object>)})

Today’s price for a `render_shot` decision payload.

The counterpart to what `nw.workflow._record_render_decision()` wrote.
Read this — never `payload["total_estimated_cost_usd"]` — whenever a
stored render cost is about to be shown or gated on as a *current* figure.

A payload written before nw#74 carries no per-call basis, so it re-quotes
as `"unknown"` with `total_usd` `None`. That is the point: nobody
can say what it costs today, and saying so is better than repeating a
number that has since moved.

The stored `total_estimated_cost_usd` is the payload’s own headline, so
it — not the calls’ sum — is reported as [`PlanQuote.as_of_total_usd`](#nw.PlanQuote.as_of_total_usd).
When the two **disagree**, the whole payload is *unknown*: a total of $3
over a payload whose calls sum to $0 is a broken record, and answering
“$0, unchanged” would report a stored $3 as a known zero. nw’s own writer
never produces such a payload; a hand-edited or truncated one can, and
unknown is the only honest reading of it.

* **Return type:**
  [`PlanQuote`](nw.pricing.html.md#nw.pricing.PlanQuote)

```pycon
>>> broken = quote_render_decision(
...     {"calls": [], "total_estimated_cost_usd": 3.0})
>>> broken.status, broken.total_usd, broken.as_of_total_usd
('unknown', None, 3.0)
>>> stale = quote_render_decision(
...     {"calls": [{"tool": "t", "application": "a",
...                 "estimated_cost_usd": 3.0}],
...      "total_estimated_cost_usd": 3.0})
>>> stale.status, stale.total_usd, stale.as_of_total_usd
('unknown', None, 3.0)
```

### nw.recommend_genre(kind)

The slug of the genre whose `intake_kinds` contains `kind` (first in slug
order), or `None` when `kind` is falsy / unmatched.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

```pycon
>>> g = register_genre(Genre(slug="_rec_demo", title="Rec", intake_kinds=("essay",)))
>>> recommend_genre("essay")
'_rec_demo'
>>> recommend_genre("nope") is None and recommend_genre(None) is None
True
>>> del genres["_rec_demo"]
```

### nw.redact(text, secrets)

`text` with every secret value replaced by `<redacted:name>`.

For the places nw persists free text it did not author — an exception
message, a failure reason — while holding the values that must not land
there. Cheap, exact-substring, and a no-op with no secrets.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> redact("boom: key sk-1 rejected", {"fal": "sk-1"})
'boom: key <redacted:fal> rejected'
>>> redact("nothing here", None)
'nothing here'
```

### nw.redact_exception(error, secrets)

The exception to re-raise so that nothing it *renders* carries a secret.

Scrubs `args` and `__notes__` in place and, when `str(error)` is
still not clean — an exception whose message is built from a non-string
arg (`RuntimeError({"detail": key})`, `OSError(2, msg, path)`) or a
custom `__str__` — rebuilds it as `type(error)(scrubbed_text)`, falling
back to `RedactedError` when the type will not construct that way
or still renders the secret. The cause/context chain is scrubbed the same
way. Returns the object to raise: the original when it was already clean.

Applied where nw lets an exception escape toward a store it does not own
(the job worker: au persists the rendered text) or files it into a record
it does (a fan-out unit’s `reason`).

* **Return type:**
  [`BaseException`](https://docs.python.org/3/builtins/exceptions.html#BaseException)

### nw.register_check(check=None, \*\*kwargs)

Add a check to the menu, as a call or as a decorator.

As a call:

```default
register_check(Check(name="video.duration", summary="...", run=...))
```

As a decorator on the run function, with the rest as keywords:

```default
@register_check(name="video.duration", summary="...", cost="cheap")
def _duration(target, ctx): ...
```

### nw.register_genre(genre)

Register a [`Genre`](#nw.Genre) under its `slug`; returns it for inline use.

* **Return type:**
  [`Genre`](#nw.Genre)

```pycon
>>> g = register_genre(Genre(slug="doctest_demo", title="Demo"))
>>> get_genre("doctest_demo").title
'Demo'
>>> "doctest_demo" in list_genres()
True
>>> del genres["doctest_demo"]  # keep the shared registry clean
```

### nw.register_genre_initializer(slug, initializer)

Register an initializer for a genre slug; returns it for inline use.

Called by the genre’s owning app. `initializer(genre, template, project,
params) -> None` seeds a freshly-created project for the chosen genre/template
(see `GenreInitializer` for the side-effect contract);
[`initialize_genre()`](#nw.initialize_genre) dispatches to it. Independent of genre \*registration
order\* (keyed by the slug string). A genre that seeds nothing on create needs
no initializer at all.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`Genre`](#nw.Genre), [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)], [`Project`](nw.project.html.md#nw.project.Project), [`Mapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]], [`None`](https://docs.python.org/3/builtins/constants.html#None)]

```pycon
>>> _ = register_genre(Genre(slug="_init_demo", title="Demo",
...                          defaults={"look": "plain"}))
>>> seen = {}
>>> def _seed(genre, template, project, params):
...     seen["applied"] = (genre.slug, template, params)
>>> _ = register_genre_initializer("_init_demo", _seed)
>>> initialize_genre("_init_demo", object())  # params default to the genre's
>>> seen["applied"]
('_init_demo', None, {'look': 'plain'})
>>> del genres["_init_demo"]; del genre_initializers["_init_demo"]
```

### nw.register_genre_project_factory(slug, factory)

Register a project factory for a genre slug; returns it for inline use.

Called by the genre’s **owning app** so a host that aggregates the genre can create
its projects via [`create_genre_project()`](#nw.create_genre_project) without knowing its storage. See
`GenreProjectFactory` for the signature + the caller-space contract.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis), [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

```pycon
>>> _ = register_genre(Genre(slug="_pf_demo", title="Demo",
...                          defaults={"format_id": "solo"}))
>>> made = {}
>>> def _f(caller, project_id, *, title, template, params):
...     made.update(caller=caller, project_id=project_id, params=params)
...     return {"project": None, "project_id": project_id}
>>> _ = register_genre_project_factory("_pf_demo", _f)
>>> create_genre_project("_pf_demo", "u@x.com", "p1")["project_id"]
'p1'
>>> (made["caller"], made["params"])
('u@x.com', {'format_id': 'solo'})
>>> del genres["_pf_demo"]; del genre_project_factories["_pf_demo"]
```

### nw.register_genre_resolver(slug, resolver)

Register a resolver for a genre slug; returns it for inline use.

Called by the genre’s owning app. `resolver(genre, template) -> params` maps a
chosen template (or `None` for “start from scratch”) to that app’s bare params
payload; [`resolve_genre()`](#nw.resolve_genre) adds the `{genre, template, params}` envelope.
Independent of genre *registration order* (keyed by the slug string).

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`Genre`](#nw.Genre), [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]], [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

```pycon
>>> _ = register_genre(Genre(slug="_resolver_demo", title="Demo",
...                          defaults={"look": "plain"}))
>>> _ = register_genre_resolver("_resolver_demo",
...     lambda genre, template: {"look": genre.defaults["look"], "via": "resolver"})
>>> resolve_genre("_resolver_demo")
{'genre': '_resolver_demo', 'template': None, 'params': {'look': 'plain', 'via': 'resolver'}}
>>> del genres["_resolver_demo"]; del genre_resolvers["_resolver_demo"]
```

### nw.register_strategy(name, impl)

Register a strategy. Returns `impl` so it can be used inline.

* **Return type:**
  [`Strategy`](nw.renderers.html.md#nw.renderers.Strategy)

### nw.register_transform(name, impl=None)

Register a Transform under `name`. Two forms:

Direct — pass an instance:

```default
register_transform("clips_to_animatic.ffmpeg", ClipsToAnimatic())
```

Decorator — decorate a class; it is instantiated and the *instance* is
registered (so [`get_transform()`](#nw.get_transform) always returns something callable),
and the class is returned unchanged:

```default
@register_transform("beat_to_panel.llm.default")
class BeatToPanelLLM(BaseTransform):
    ...
```

Registration validates the contract the registry’s consumers depend on:
an empty `output_kind` is refused loudly, in the same spirit as the
registry’s `on_conflict="error"` — an agent’s unit of work must have a
declared output type, or “the job runs successfully but produces
nothing retrievable” becomes invisible to every layer that reports
success (nw#27).

* **Return type:**
  `Union`[[`Transform`](#nw.Transform), [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`type`](https://docs.python.org/3/builtins/functions.html#type)], [`type`](https://docs.python.org/3/builtins/functions.html#type)]]

### nw.resolve_defaults(genre, template=None)

Resolve a genre (+ optional template) to the params for a new project.

Returns `{"genre": slug, "template": template_or_None, "params": {...}}` — the
chosen [`Template`](#nw.Template)’s `params` when `template` is given, else the genre’s
`defaults`. Raises [`KeyError`](https://docs.python.org/3/builtins/exceptions.html#KeyError) on an unknown genre or template. The caller
(app) interprets `params` (reelee reads `output_intent`/`flavor`; braidio a
`format_id`).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### nw.resolve_genre(genre, template=None)

Resolve a genre (+ optional template) to the standard creation envelope.

Always returns `{"genre": slug, "template": template_or_None, "params": {...}}` —
ONE stable contract for every host, regardless of whether the genre has a resolver.
`params` comes from the genre’s registered resolver
([`register_genre_resolver()`](#nw.register_genre_resolver)) when one exists, else from the generic
[`resolve_defaults()`](#nw.resolve_defaults) (the template’s params, or the genre’s `defaults`).

Raises [`KeyError`](https://docs.python.org/3/builtins/exceptions.html#KeyError) on an unknown genre or — **uniformly, resolver or not** —
an unknown `template` slug (the substrate owns template identity; a resolver only
interprets params, it doesn’t get to invent template slugs).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

```pycon
>>> _ = register_genre(Genre(slug="_rg_demo", title="Demo",
...                          defaults={"flavor": "cinematic"}))
>>> resolve_genre("_rg_demo")  # no resolver registered -> generic params
{'genre': '_rg_demo', 'template': None, 'params': {'flavor': 'cinematic'}}
>>> del genres["_rg_demo"]
```

### nw.save_storyboard(project, storyboard, , panel_intervals, was_attributed_to='user:nw', was_generated_by='agent:nw.storyboard')

Persist a Storyboard into the project’s SqliteStore.

Wipes the existing storyboard panels (under the default tier) so the
save is idempotent — re-running with edited panels replaces them rather
than accumulating duplicates.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### nw.shot_report(project, shot_id, , freeze_sample_fps=4.0, duration_tolerance_s=0.1)

Inspect `shots/<shot_id>/output.mp4` and return a typed report.

* **Parameters:**
  * **project** ([`Project`](nw.project.html.md#nw.project.Project)) – The [`nw.Project`](#nw.Project).
  * **shot_id** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – The shot id.
  * **freeze_sample_fps** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – How many frames per second to extract for the
    freeze detector (default 4 fps; a freeze must hold across at
    least two consecutive samples to count).
  * **duration_tolerance_s** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Acceptable difference between actual and
    target duration before flagging.
* **Return type:**
  [`ShotReport`](nw.inspect.html.md#nw.inspect.ShotReport)
* **Returns:**
  A [`ShotReport`](#nw.ShotReport).

### nw.stale_after(project_root, changed_id)

Return every annotation that `changed_id` actually invalidated.

The freshness operation. `changed_id`’s descendants are walked and each
is checked against the upstream value digests it recorded when it was
written ([`nw.bodies.verifying_trace`](nw.bodies.verifying_trace.html.md#module-nw.bodies.verifying_trace)). A descendant whose recorded
inputs still match the current ones is **not** returned — that is the
early cutoff, and it is why this is not `descendants_of` under another
name. The full rule, and the four things it deliberately does not catch,
are in this module’s docstring.

The returned list does NOT include `changed_id` itself (it is the source
of the change, not a stale derivative).

`descendants_of` is unchanged and still answers the reachability
question — “what is downstream of this?” is legitimate and the two verbs
are no longer synonyms. Use [`stale_verdicts()`](#nw.stale_verdicts) when you need the
*reason* a given annotation is in (or out of) this set.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[`Annotation`]

### nw.stale_verdicts(project_root, changed_id)

Classify every annotation downstream of `changed_id`.

The explained form of [`stale_after()`](#nw.stale_after): one verdict per reachable
annotation, stale or not, in a deterministic order (generation time, then
id). `changed_id` itself is never included — it is the source of the
change, not a derivative of it.

Use this when the *number* is being questioned. `stale_after` is the
same walk with the fresh verdicts dropped;
[`stale_verdicts_all()`](#nw.stale_verdicts_all) is the same classification with no
`changed_id` — the whole-project snapshot.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`FreshnessVerdict`](nw.freshness.html.md#nw.freshness.FreshnessVerdict)]

### nw.stale_verdicts_all(project_root)

Classify every derived annotation in the project — the snapshot form.

The question a freshness *indicator* asks: “what is stale in this
project right now?”, with no `changed_id` to anchor on. Same
verifying-trace classification as [`stale_verdicts()`](#nw.stale_verdicts), over a wider
frontier: every annotation with at least one provenance parent.

Two boundaries that are the point of this living here rather than each
consumer approximating it (nw#39):

- **Parentless annotations are never stale** and stay out of the walk —
  an imported screenplay must not read as stale forever. (nw-written
  verifying traces are parentless, so they stay out too.)
- **Upstream-stale recursion runs over the whole derived set.** The
  scoped walk only recurses into parents inside `reachable` (outside
  it a parent is by construction unaffected by the change); with no
  change there is no such boundary. The cycle guard covers termination.

**Legacy projects read all-stale, by design.** A derived annotation
written before verifying traces existed classifies `no-trace` →
stale, and unlike the scoped walk (which only surfaces it downstream
of an actual change) the snapshot reports it *always*, until it is
rewritten through the trace-writing path. A consumer replacing its own
weaker snapshot with this one is making a behavior change on pre-trace
projects, not installing a pure wrapper.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`FreshnessVerdict`](nw.freshness.html.md#nw.freshness.FreshnessVerdict)]

### nw.stamp_transform_identity(plan, transform)

Fold `transform.impl_version` into every call’s cache identity.

The reader that makes `impl_version` a lock instead of a receipt
(nw#27): a bumped version lands in each call’s falaw `key_extra`, so
a cached result minted by the old behaviour cannot be reused. At
`DFLT_IMPL_VERSION` nothing is stamped — every key ever issued
stays byte-identical, and the first real bump is the first salt.

Each stamped call’s `cache_status` is reset to `"unknown"`: the
plan-time peek keyed without the salt, so its prediction (typically
“hit” — the old-behaviour result is cached, invalidating it is the
point) would make cost gates quote $0.00 for a full re-bill.

`BaseTransform.execute()` applies this automatically. \*\*A Transform
that overrides\*\* `execute()` \*\*must apply it
itself\*\* — the lock only locks calls that pass through it (a
registry-wide conformance test is the honest guard). An orchestrator
that hashes or caches plans *before* execution (e.g. a job idempotency
key over `falaw.plan_hash`) should apply it at plan time so those
keys see the version too. Idempotent — stamping twice writes the same
value.

* **Return type:**
  `Plan`

### nw.storyboard_db_path(project)

Return the path to the project’s storyboard SQLite store.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### nw.storyboard_from_shots(project, , title=None, style=None)

Build a one-panel-per-shot draft Storyboard from a project’s shots.

Each panel’s caption defaults to the shot’s description, framing and
camera carry over, and the panel’s `shot_id` points back at the shot.
No images are attached yet — use [`plan_render_panel_images()`](#nw.plan_render_panel_images) to
generate them.

Returns `(storyboard, panel_intervals)` so the caller can feed both
into [`save_storyboard()`](#nw.save_storyboard).

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[`Storyboard`, [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), `TimeInterval`]]

### nw.suggest(request, , include_paid=False)

Checks whose `example_requests` look like what the user just asked for.

Deliberately crude — a word-overlap score, not a model call — because this
runs on every request and its job is to narrow forty items to a handful
that a human or a model then confirms. `"paid"` checks are never
suggested: money is asked for by name.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Check`](nw.validation.html.md#nw.validation.Check), [`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis)]

### Examples

```pycon
>>> suggest("")
()
```

### nw.summarize_all(roots)

Convenience: return a [`ProjectSummary`](#nw.ProjectSummary) for each project.

Equivalent to `apply_to_projects(roots, lambda p: p.read_summary())`.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`ProjectSummary`](nw.schema.html.md#nw.schema.ProjectSummary)]

### nw.transform_catalog()

Every registered Transform as a JSON-able capability entry (sorted by name).

The typed-capability surface an HTTP route / MCP tool builder / agent
serves or selects from, mirroring [`nw.genre_catalog()`](#nw.genre_catalog) (nw#28): a
consumer needs no registry-internal knowledge to render or compose.
Entry shape:

```default
{name, input_kinds, output_kind, is_batch, generate_when,
 impl_version, params_schema}
```

`name` is the registry key (the addressable name). `params_schema`
is the params model’s JSON Schema — `{}` for a Transform with no
params — and is what an MCP tool definition is built from. The whole
list is JSON-serializable as returned.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### nw.unquotable(reason)

A quote for something that could not be re-quoted at all.

For the caller who was handed a *plan-shaped* thing that turned out not to
be a plan — an unparseable payload, an object of the wrong type. The honest
answer is `None` (unknown), not the frozen figure that came with it, and
not `0.0`.

[`PlanQuote.repriced`](#nw.PlanQuote.repriced) is an empty `falaw.RepricedPlan`: there
were no calls to diff. Read [`PlanQuote.reason`](#nw.PlanQuote.reason) for what went wrong.

* **Return type:**
  [`PlanQuote`](nw.pricing.html.md#nw.pricing.PlanQuote)

```pycon
>>> q = unquotable("params['plan'] is not a falaw Plan")
>>> q.status, q.total_usd, q.has_unknown_costs
('unknown', None, True)
```

### nw.using_secrets(secrets)

Bind the secrets nw itself knows how to use, for the duration of a block.

Today that is `FAL_SECRET`: when present it becomes the fal
credential (`falaw.using_fal_credentials()`) so every `call_fal`
inside the block authenticates with the caller’s key instead of the
server’s `FAL_KEY`. Anything else in `secrets` is left for the
Transform that declared it. With no fal secret this is a `nullcontext`,
so the `with` shape stays uniform.

* **Return type:**
  [`AbstractContextManager`](https://docs.python.org/3/library/contextlib.html#contextlib.AbstractContextManager)[[`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### nw.validate(target, , checks=(), max_workers=4, on_error='report')

Run `checks` against `target` and report.

* **Parameters:**
  * **target** ([`Any`](https://docs.python.org/3/library/typing.html#typing.Any)) – whatever the checks understand — a path to a rendered file, a
    `Project`, a `(video, annotations)` pair. This module does not
    care; it is the checks that agree with their caller.
  * **checks** ([`Iterable`](https://docs.python.org/3/library/typing.html#typing.Iterable)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Check`](nw.validation.html.md#nw.validation.Check)]) – names or [`Check`](#nw.Check) objects. Requirements are pulled in
    automatically. Empty means empty: validation is placed, never
    assumed.
  * **max_workers** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – concurrency within a wave.
  * **on_error** ([`Literal`](https://docs.python.org/3/library/typing.html#typing.Literal)[`'report'`, `'raise'`]) – `"report"` records a raising check as an errored
    [`CheckResult`](#nw.CheckResult) and carries on, so one broken plugin cannot
    hide the findings of the other nine. `"raise"` is for developing
    a check.
* **Return type:**
  [`ValidationReport`](nw.validation.html.md#nw.validation.ValidationReport)
* **Returns:**
  A [`ValidationReport`](#nw.ValidationReport). Note that `report.ok` is `False` when
  a check *errored*, not only when one failed: a suite that could not run
  has not said the work is good.

### Examples

```pycon
>>> validate("x.mp4").ok
True
```

### nw.work_item_instance_id(transform_name, mapping_key)

The instance id of one fan-out unit: UUIDv5 of `(transform_name, mapping_key)`.

A **pure function**, deliberately: pure is async-safe by construction
(no ambient counter for a suspended coroutine to corrupt — the ComfyUI
`GraphBuilder` race) and stable under insertion (adding an item never
changes any other item’s id). The same (transform, key) pair yields the
same id on every machine, every run, forever — which is what makes
per-instance retry, cost attribution, and “regenerate just this one”
addressable across runs.

* **Return type:**
  [`UUID`](https://docs.python.org/3/library/uuid.html#uuid.UUID)

```pycon
>>> a = work_item_instance_id("panel_to_image.fal", "scene_1/panel_2")
>>> a == work_item_instance_id("panel_to_image.fal", "scene_1/panel_2")
True
>>> a != work_item_instance_id("panel_to_voiceover", "scene_1/panel_2")
True
```

### Modules

| [`freshness`](nw.freshness.html.md#module-nw.freshness)                     | Freshness with **early cutoff** — what is *actually* out of date.                                                               |
|----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------|
| [`inspect`](nw.inspect.html.md#module-nw.inspect)                         | QA helpers — typed reports about rendered shots.                                                                                |
| [`checks`](nw.checks.html.md#module-nw.checks)                           | The checks nw itself ships — a small, honest default menu.                                                                      |
| [`bodies`](nw.bodies.html.md#module-nw.bodies)                           | Body schemas for nw's project-graph annotations.                                                                                |
| [`delivery`](nw.delivery.html.md#module-nw.delivery)                       | What a genre hands back when a human wants to *hold* what it made.                                                              |
| [`experiment`](nw.experiment.html.md#module-nw.experiment)                   | Experiment helpers — clone projects, apply operations across siblings.                                                          |
| [`genres`](nw.genres.html.md#nw.genres)                                  | A typed dict-backed plugin registry.                                                                                            |
| [`graph`](nw.graph.html.md#module-nw.graph)                             | The project annotation graph — read/write helpers + reelee-style traversals.                                                    |
| [`graph_backend`](nw.graph_backend.html.md#module-nw.graph_backend)             | Config-driven backend selection for nw's annotation graph stores.                                                               |
| [`jobs`](nw.jobs.html.md#module-nw.jobs)                               | nw.jobs — a project-scoped async **job** facade over `au`.                                                                      |
| [`migrate`](nw.migrate.html.md#module-nw.migrate)                         | Idempotent migration: project.json (sections/shots/refs) → lacing graph.                                                        |
| [`pricing`](nw.pricing.html.md#module-nw.pricing)                         | Re-quoting a persisted plan at today's rates (nw#74).                                                                           |
| [`project`](nw.project.html.md#module-nw.project)                         | Project facade: a folder on disk → typed reads, typed writes, typed summary.                                                    |
| [`renderers`](nw.renderers.html.md#module-nw.renderers)                     | Render strategies — pluggable, plan-producing, **shot-typed**.                                                                  |
| [`schema`](nw.schema.html.md#module-nw.schema)                           | Schema for an nw project — narrative-workflow SSOT data shapes.                                                                 |
| [`script_segmentation`](nw.script_segmentation.html.md#module-nw.script_segmentation) | `nw.script_segmentation` — narrow LLM-backed helper that converts a free-form script into a list of storyboard-panel proposals. |
| [`secrets`](nw.secrets.html.md#module-nw.secrets)                         | Execution secrets — credentials that reach `execute` and nothing else.                                                          |
| [`storyboard`](nw.storyboard.html.md#module-nw.storyboard)                   | Storyboard ↔ Project bridge.                                                                                                    |
| [`transforms`](nw.transforms.html.md#nw.transforms)                          | A typed dict-backed plugin registry.                                                                                            |
| [`validation`](nw.validation.html.md#module-nw.validation)                   | Pluggable validation of finished work — the seam, not the checks.                                                               |
| [`workflow`](nw.workflow.html.md#module-nw.workflow)                       | Workflow: prepare → plan → execute, for a **video shot**.                                                                       |
