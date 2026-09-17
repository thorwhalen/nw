# nw.graph

The project annotation graph — read/write helpers + reelee-style traversals.

A nw project’s SSOT for sections, shots, character/environment refs, and
decisions is a per-project lacing `SqliteStore` at
`project.annot.sqlite`. This module wraps that store with typed helpers
so the rest of nw doesn’t need to know about tier names, MediaRef
construction, or annotation envelopes.

Usage from inside the package (illustrative — `project_root`, `shot_body`
and `TimeInterval` are the caller’s fixtures, not defined here, so the
executable lines are skipped rather than exercised):

```pycon
>>> from nw.graph import ProjectGraph
>>> g = ProjectGraph(project_root)
>>> g.upsert_shot_body(shot_body, interval=TimeInterval.from_seconds(0, 8))
>>> for shot in g.shots():
...     ...
```

For reelee’s freshness analysis (planned in §7 of the system overview),
[`derived_from()`](#nw.graph.derived_from) and [`descendants_of()`](#nw.graph.descendants_of) walk the
`provenance.was_derived_from` edges across **all** stores in a project
(project graph + storyboard + alignment). Those are *reachability* queries.
The freshness query that compares content — `nw.stale_after` — lives in
[`nw.freshness`](nw.freshness.html.md#module-nw.freshness); this module is where its input, the verifying trace, is
written (see [`ProjectGraph.add_annotation()`](#nw.graph.ProjectGraph.add_annotation)).

### Functions

| [`all_project_stores`](#nw.graph.all_project_stores)(project_root)                  | Return the **existing** lacing-store file paths under a project.              |
|----------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| [`annotations_at_tier`](#nw.graph.annotations_at_tier)(project_root, tier)           | Return every annotation at the given tier across all of the project's stores. |
| [`backfill_traces`](#nw.graph.backfill_traces)(project_root, \*[, execute])      | Bless a pre-trace project so the verifying-trace rule can read it (nw#58).    |
| [`collect_orphan_traces`](#nw.graph.collect_orphan_traces)(project_root)               | Drop verifying traces whose target annotation no longer exists.               |
| [`derived_from`](#nw.graph.derived_from)(project_root, annotation_id)         | Return the annotations this one was directly derived from.                    |
| [`descendants_of`](#nw.graph.descendants_of)(project_root, ancestor_id)         | Return every annotation whose provenance chain leads back to `ancestor_id`.   |
| [`iter_all_annotations`](#nw.graph.iter_all_annotations)(project_root)                | Walk every annotation in every store under a project (any backend).           |
| [`open_project_stores`](#nw.graph.open_project_stores)(project_root)                 | Yield an iterator of open stores, one per scope, honouring the backend.       |
| [`remove_annotations_with_traces`](#nw.graph.remove_annotations_with_traces)(store, ...[, ...]) | Remove annotations from `store`, plus every trace in it naming them.          |

### Classes

| [`ProjectGraph`](#nw.graph.ProjectGraph)(project_root)                   | Typed read/write facade over the project's lacing graph store.   |
|-----------------------------------------------------------------------------------------------|------------------------------------------------------------------|
| [`StoredCharacterRef`](#nw.graph.StoredCharacterRef)(annotation_id, body)      |                                                                  |
| [`StoredDecision`](#nw.graph.StoredDecision)(annotation_id, body)          |                                                                  |
| [`StoredEnvironmentRef`](#nw.graph.StoredEnvironmentRef)(annotation_id, body)    |                                                                  |
| [`StoredSection`](#nw.graph.StoredSection)(annotation_id, interval, body) |                                                                  |
| [`StoredShot`](#nw.graph.StoredShot)(annotation_id, interval, body)    |                                                                  |
| [`StoredUnproducedOutput`](#nw.graph.StoredUnproducedOutput)(annotation_id, body)  |                                                                  |

### *class* nw.graph.ProjectGraph(project_root)

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

It is also where a matching [`add_unproduced_output()`](#nw.graph.ProjectGraph.add_unproduced_output) record is
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
([`nw.transforms.FailedOutput`](nw.html.md#nw.FailedOutput)) — pass its `skeleton`,
`status`, `reason`, `error` and `blocked_by` straight through.
Written under `nw.bodies.UNPRODUCED_OUTPUT_BODY_SCHEMA_URI`,
never under `skeleton.body_schema_uri` (see the module’s docstring
on why that tier is reserved for what was actually produced).

`instance_id` (a fan-out unit’s
`work_item_instance_id()`) and
`call_index` (this output’s position within its `execute()`
call’s skeleton tuple) together form the identity
[`add_annotation()`](#nw.graph.ProjectGraph.add_annotation) retires by — see
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

The read half of [`set_genre_envelope()`](#nw.graph.ProjectGraph.set_genre_envelope); consumers should reach
it through [`nw.Project.resolved_genre()`](nw.html.md#nw.Project.resolved_genre), which returns the
plain-dict envelope shape `nw.genres.resolve_genre()` produces.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`GenreEnvelopeBodyV1`](nw.bodies.genre_envelope.html.md#nw.bodies.genre_envelope.GenreEnvelopeBodyV1)]

#### remove_annotation(annotation_id)

Remove one annotation from the project graph, with its verifying traces.

The delete counterpart of [`add_annotation()`](#nw.graph.ProjectGraph.add_annotation) (nw#36): every trace
whose `for_annotation_id` names `annotation_id` goes with it, so a
deletion never leaves a sidecar behind — an orphaned trace is inert
for freshness but grows the store without bound, and if the id is
later re-used it can even answer a freshness query from digests
recorded for content that is no longer there.

Only the project graph store is touched — the store
[`add_annotation()`](#nw.graph.ProjectGraph.add_annotation) writes to. Annotations living in the other
scopes (storyboard, alignment) are removed by their own facades;
[`collect_orphan_traces()`](#nw.graph.collect_orphan_traces) is the project-wide backstop.

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

A record disappears the moment [`add_annotation()`](#nw.graph.ProjectGraph.add_annotation) writes a real
output for the same identity, or another [`add_unproduced_output()`](#nw.graph.ProjectGraph.add_unproduced_output)
call for the same identity supersedes it (dedupe) — what this
returns is exactly “still missing”, survives a reload, and is not a
cache of any in-memory `TransformResult`.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`StoredUnproducedOutput`](#nw.graph.StoredUnproducedOutput)]

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

### *class* nw.graph.StoredCharacterRef(annotation_id, body)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

### *class* nw.graph.StoredDecision(annotation_id, body)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

### *class* nw.graph.StoredEnvironmentRef(annotation_id, body)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

### *class* nw.graph.StoredSection(annotation_id, interval, body)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

### *class* nw.graph.StoredShot(annotation_id, interval, body)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

### *class* nw.graph.StoredUnproducedOutput(annotation_id, body)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

### nw.graph.all_project_stores(project_root)

Return the **existing** lacing-store file paths under a project.

SQLite-mode only — these are filesystem paths. Code that walks or mutates
project stores should route through [`open_project_stores()`](#nw.graph.open_project_stores) (which
honours the backend seam) rather than opening these paths directly, so it
keeps working when the backend is Postgres.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)]

### nw.graph.annotations_at_tier(project_root, tier)

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

### nw.graph.backfill_traces(project_root, , execute=False)

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

### nw.graph.collect_orphan_traces(project_root)

Drop verifying traces whose target annotation no longer exists.

The backstop for deletion paths that do not (or cannot) go through
[`remove_annotations_with_traces()`](#nw.graph.remove_annotations_with_traces) — a direct `store.remove`, an
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

### nw.graph.derived_from(project_root, annotation_id)

Return the annotations this one was directly derived from.

Walks `provenance.was_derived_from` *one hop only* across all of the
project’s stores.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[`Annotation`]

### nw.graph.descendants_of(project_root, ancestor_id)

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

### nw.graph.iter_all_annotations(project_root)

Walk every annotation in every store under a project (any backend).

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[`Annotation`]

### nw.graph.open_project_stores(project_root)

Yield an iterator of open stores, one per scope, honouring the backend.

The backend-aware replacement for `for p in all_project_stores(...):
SqliteStore(p)`. Under SQLite it visits each existing per-scope file;
under Postgres it visits each scope’s tenant in the shared DB. Use it for
both reads (walk `.all()`) and writes (`.remove` / `.add`).

Each store is closed before the next opens, so consume each store’s
annotations before advancing.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[`IntervalAnnotationStore`]]

### nw.graph.remove_annotations_with_traces(store, annotation_ids, , annotations=None)

Remove annotations from `store`, plus every trace in it naming them.

The store-level primitive behind every nw deletion path
([`ProjectGraph.remove_annotation()`](#nw.graph.ProjectGraph.remove_annotation), `write_spec`’s entity
reconciliation, the storyboard wipe). A verifying trace is a sidecar of
the annotation it describes; removing one without the other leaks an
inert row per deletion, forever (nw#36).

* **Parameters:**
  * **store** (`IntervalAnnotationStore`) – An **open** store — the caller owns its lifecycle.
  * **annotation_ids** ([`Iterable`](https://docs.python.org/3/library/typing.html#typing.Iterable)[[`UUID`](https://docs.python.org/3/library/uuid.html#uuid.UUID)]) – Ids to remove. Missing ids are ignored.
  * **annotations** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[`Annotation`]]) – The store’s annotations, if the caller already
    materialized `list(store.all())` — avoids a second scan.
* **Return type:**
  [`set`](https://docs.python.org/3/builtins/stdtypes.html#set)[[`UUID`](https://docs.python.org/3/library/uuid.html#uuid.UUID)]
* **Returns:**
  The subset of `annotation_ids` that was actually present. Trace
  removals are not reported: they are bookkeeping, not content.
