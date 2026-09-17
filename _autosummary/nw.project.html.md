# nw.project

Project facade: a folder on disk → typed reads, typed writes, typed summary.

A nw project lives at a folder. `project.json` is the SSOT; every other
file is a derived artifact (lyrics, alignment store, character cards, shot
output videos, etc.).

The facade is deliberately small. It exposes:

- read/write/update of the `ProjectSpec`,
- folder helpers (`character_dir()`, `environment_dir()`,
  `shot_dir()`),
- the setter operations the muvid_project agent had to express via
  `python -c` glue (`set_title`, `set_global_style`,
  `set_character_anchor`, `list_character_images`),
- a typed `read_summary()` that returns the facts `muvid status`
  printed,
- a `log_decision()` append-only line writer.

It does NOT do rendering — that’s [`nw.workflow`](nw.workflow.html.md#module-nw.workflow) (Phase 1b.2).

### Classes

| [`CharacterImage`](#nw.project.CharacterImage)(path, \*[, from_ref, ...])   | One image associated with a character.   |
|----------------------------------------------------------------------------------------------|------------------------------------------|
| [`Project`](#nw.project.Project)(root, \*[, auto_migrate])           | A folder-backed nw project.              |

### *class* nw.project.CharacterImage(path, , from_ref=False, from_selected=False, is_anchor=False)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One image associated with a character.

Returned by [`Project.list_character_images()`](#nw.project.Project.list_character_images). Distinguishes:

- `from_ref`: file lives under `characters/<name>/refs/` — a candidate
  from generation or upload.
- `from_selected`: under `characters/<name>/selected/` — curator-picked.
- `is_anchor`: this is the file the character card currently points at as
  the “use this image” anchor (lipsync seed, etc.).

### *class* nw.project.Project(root, , auto_migrate=True)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A folder-backed nw project.

Construct from a path (must exist + must contain `project.json`); use
[`Project.init()`](#nw.project.Project.init) to bootstrap a new project on disk.

#### add_character(name, , description='')

Add a character (idempotent: re-adds update the description).

Re-adding updates *only* the description: any stable attributes
already recorded on the character (costume, palette anchors,
`do_not_do` …) are carried over, so calling this again is not a
way to lose them.

* **Return type:**
  [`CharacterRef`](nw.schema.html.md#nw.schema.CharacterRef)

#### *classmethod* init(root, , title='', song=None, force=False)

Create a new project on disk and return the [`Project`](#nw.project.Project) facade.

* **Parameters:**
  * **root** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – Folder to create. Must not exist (or pass `force=True` to
    overwrite an empty folder).
  * **title** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Optional human-readable title; defaults to the folder name.
  * **song** (`Union`[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path), [`None`](https://docs.python.org/3/builtins/constants.html#None)]) – Optional path to a master audio file. When given, the file
    is *copied* into `<root>/song/` and registered in the spec.
  * **force** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – When True, accept an existing folder if it’s empty (no
    `project.json`); refuse if a project already exists there.
* **Return type:**
  [`Project`](#nw.project.Project)

#### list_character_images(name)

Return all images associated with a character, with provenance flags.

Walks `characters/<name>/refs/` and `characters/<name>/selected/`.
Marks the file the card’s `reference_image_path` points at as
`is_anchor=True`.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`CharacterImage`](#nw.project.CharacterImage)]

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
synthesized into the returned `ProjectSpec` for back-compat
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
