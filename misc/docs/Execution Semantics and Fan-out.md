# Execution semantics and fan-out

*Where nw sits on the incrementality grid, what fan-out primitive it adopts, and
the invariants the `Transform` contract must enforce.*

Companion to `Rendering Provenance and Partial Re-render.md`, which owns the
*why* (choices as linked artifacts). This document owns the *how*: scheduling,
fan-out, failure isolation, and capability discovery.

Every position below is grounded in the video_gen research programme (private
repo, `data/groups/video_gen/docs/research/`): brief A (concepts and
terminology), brief B (evaluation, incrementality, caching), brief G (synthesis
and gap analysis), brief K (ComfyUI execution semantics at source), brief O (the
façade spec). Where a claim below carries a number, it comes from those briefs.

---

## 1. Where nw sits, and where it is going

*Build Systems à la Carte* decomposes any incremental engine into a
**scheduler** × a **rebuilder**. The grid:

| Rebuilder ↓ / Scheduler → | Topological | Restarting | Suspending |
|---|---|---|---|
| **Dirty bit** | Make | Excel | — |
| **Verifying traces** | Ninja | — | Shake |
| **Constructive traces** | CloudBuild | Bazel | *(unoccupied — "Cloud Shake")* |
| **Deep constructive traces** | Buck | — | Nix |

**nw today is the Make cell**: reachability-based invalidation with no output
comparison (`nw/graph.py:451`), so no early cutoff. **ComfyUI is also a
dirty-bit rebuilder** — its cache key is a function of the entire upstream
subgraph's structure, class types, parameters and `IS_CHANGED` values, *and of
no output value whatsoever*. We re-implemented their defect at our own graph
layer independently.

**The target is the unoccupied cell**: suspending × constructive traces (the
paper names it "Cloud Shake" and calls it the most interesting unfilled spot).
That combination is exactly what a generative-media executor needs — the graph
shape depends on the screenplay (needs dynamic dependencies), each node is
expensive (needs minimality and early cutoff), and results should be shareable
across machines (needs constructive traces). The intermediate, and the next
step, is **verifying traces**: compare *output* content digests and stop
propagating where nothing changed.

The one rule that gets us there, stated once:

> **Key the cache on inputs; address the artifact by content; record both in the
> trace.** An input-addressed key answers "have I run this exact invocation
> before?". A content digest of the output answers "did the answer actually
> change?". You need both, and they are different hashes. A system with only the
> first cannot cut off.

## 2. The fan-out primitive: PDG work items with Dagster's mandatory key

nw has no fan-out primitive. `Transform.is_batch`
(`nw/transforms/__init__.py:112`) is the only cardinality signal and it says
only "`plan()` consumes all of `inputs.primary` at once" — not how many outputs
there will be, and not whether the count is knowable before the run.

Four production mechanisms were surveyed. **Ranked on identity discipline:
Dagster > Houdini PDG > Airflow > ComfyUI.**

| Axis | ComfyUI | PDG | Airflow | Dagster | nw needs |
|---|---|---|---|---|---|
| Shape known before execution? | No, and no way to declare it | **Yes** (`Generate When: static\|dynamic`) | Partly | Partly | **Yes** — the cost gate is dishonest without it |
| Instance identity | Ordinal *by default* | Attribute-derived | Ordinal, honestly so | **Semantic, mandatory** | Semantic, mandatory |
| Identity durability | In-process only | Persisted | DB-backed | Asset storage | Persisted in the graph |
| Per-instance retry | **No** — whole prompt or nothing | Yes | Yes | Yes | Yes, per shot |
| Failure isolation | **None** | Per-item | Per-instance | Per-partition | Per-shot |
| Return a collection to the parent | **No** (their own open PR) | Yes | Yes | Yes | Yes |

**ComfyUI node expansion is rejected as our fan-out primitive**, on three
counts, in severity order:

1. **Caching silently dies on the realistic fan-out.** Any non-canonicalisable
   value handed to an expanded instance becomes `Unhashable`, and every node
   receiving it is permanently uncacheable — with no diagnostic.
2. **No failure isolation and no per-instance retry.** One filtered shot out of
   200 halts the run.
3. **No shape declaration, so no honest cost gate.** A pre-flight estimate over
   an expanding node is not merely imprecise, it is *unformulable*.

### What nw adopts instead

- **`WorkItem(mapping_key, parent_key, attributes, scope_interval)`** — PDG's
  unit of fan-out.
- **`mapping_key` is deterministic AND semantic.** `"scene_12/shot_04"`; never
  an ordinal, never a `uuid4`. Deterministic because non-deterministic ids
  silently disable the cache; semantic because ordinals shift when the
  screenplay is edited — insert a scene at position 2 and every downstream key
  from that point misses. Neither research brief states the conjunction; both
  halves are required.
- **`generate_when: "static" | "dynamic"`, declared per Transform.** "One image
  per panel" is static (cardinality known from the panel list); "segment this
  screenplay into beats" is dynamic (known only after the LLM returns). The
  static pre-pass yields a *real* cost estimate; the dynamic frontier yields an
  honest "unknown until segmentation completes". Our existing cost rule then
  does the right thing: `falaw.estimate_call_cost` (`falaw/cost.py:59`) returns
  `None` for an unpriceable call — never `0.0` — which propagates to
  `CallPlan.estimated_cost_usd` and lights up `Plan.has_unknown_costs`, forcing
  approval rather than under-quoting. Undeclared defaults to `dynamic`: fail
  expensive-looking.
- **Instance ids are a pure function of `(transform_name, mapping_key)`** —
  UUIDv5 over a namespace. Never allocated from ambient state. ComfyUI's
  `GraphBuilder` uses a class-level mutable default prefix that is not
  async-safe (their own in-source TODO admits it); a pure function is async-safe
  by construction and stable under insertion.
- **Work items live in the run record, not in the graph document.**
  Materialising instances into the document mutates it on execution and breaks
  its digest.
- **The generated unit is an ordinary Transform invocation** producing ordinary
  `lacing.Annotation`s and an ordinary `falaw.CallPlan` — never a special
  "expanded" record type. This is the one thing ComfyUI gets right: *the
  expansion document is the execution document*, which is why expansion composes
  with everything else in their executor.

### Time belongs in the demand, not in the graph

`scope_interval` rides on the `WorkItem` — on the *demand* that materialises the
item, not baked into the Transform. Nuke's model. The payoff is concrete: a
pipeline that puts frame ranges *in* nodes must edit the graph to change a
range; one that puts them in the request does not. (Open, per brief G: Nuke's
demand is a *frame* — a point — while a work item's scope is an *interval*, so
the demand type should be designed to be extensible.)

## 3. Failure isolation is structural, not a nicety

ComfyUI breaks the run on the first node failure. For a graph of GPU ops taking
seconds that is defensible. For a 200-shot fan-out where shot 47 trips a content
filter it is not — the other 199 are independent and 46 are already paid for.

nw inherited the same shape until nw#25. **This is now built** — falaw#20 landed
`execute_plan_isolated` + `ExecutionReport` (falaw ≥ 0.0.25), and
`BaseTransform.execute` runs both policies through it:

- A run-level `on_failure` policy: `"halt" | "isolate"`. `"halt"` is the default
  and is byte-for-byte the old behaviour — falaw defines `execute_plan` as
  exactly this call plus `artifacts_or_raise()`, so the *original* typed
  exception still propagates unwrapped.
- **Three outcomes, not two.** A branch is *produced*, *blocked*, or *failed*.
  "Blocked" is ComfyUI's `ExecutionBlocker` idea with its worst property
  removed — a silently-blocked branch there is indistinguishable from one that
  never ran. `TransformResult.blocked` carries a `reason` and `blocked_by`, so
  the UI renders "skipped: no dialogue in this panel" rather than an
  unexplained hole.
- `TransformResult` carries `annotations` / `failed` / `blocked` (+
  `is_complete`), and **the cost report attributes spend to the produced set
  only** — `ExecutionReport.estimated_spend_usd`, which excludes cache hits and
  failed calls. (Since thorwhalen/falaw#26 landed, per-`Artifact` `cost_usd`
  is also stamped from the observed outcome, so the sums agree; the report
  stays the source because it is run-level and carries `has_unknown_costs`.)
- Successes are written to the graph **before** failures are reported. They are
  paid for; discarding them because a sibling failed is the waste falaw#20
  removed one layer down.

One thing the original design note did not anticipate: the iteration must zip
skeletons against `report.outcomes`, never against the artifact list. Outcomes
is full-length in plan order by construction; the artifact list is short exactly
when something failed, so zipping it pairs shot 48's artifact onto shot 47's
skeleton the moment one call drops out — silently.

Not yet built: a Transform that composes N calls into one output
(`RenderStrategyTransform`) can only isolate at *its own* boundary — it reports
the whole shot as failed rather than half-materializing it. That is the honest
granularity, not a gap, but a caller fanning out over shots needs to know which
level it is getting.

Without this, a set-level verdict ("is this set of shots coherent?") has nothing
to act on — which is why the decisions doc calls per-branch failure isolation
"the one missing piece".

## 4. Contract invariants the registry must enforce

**An agent's unit of work must have a declared output type.** ComfyUI's own
agent skill enforces "this workflow produces a retrievable artifact" as *prose
instructions to the model*, warning that otherwise "the job runs successfully
but produces nothing retrievable, wasting compute". That failure is invisible to
schema validation, to type checking, and to the executor alike. `output_kind` is
already the right answer — it is a declared field on the `Transform` Protocol
(`nw/transforms/__init__.py:109`). The gap is that nothing enforces it:
`BaseTransform.output_kind` defaults to `""` (`:178`) and `register_transform`
(`:270`) does not check.

**Version goes in a field, not in the name.**
`panel_to_image.comfy.flux_lora` is a correct registry name — it denotes a
genuinely different capability (different cost, output character, pins) from
`panel_to_image.fal.flux_kontext`. But a prompt-template edit inside one flavor
is a *behaviour* change with a *stable* interface. That is what `impl_version`
is for, and it must enter the **cache key**. ComfyUI's `properties.ver` is the
anti-lesson: recorded, and read by nobody — *a receipt, not a lock*.

**Undeclared impurity is the expensive default.** Determinism, billability and
static/dynamic are declared on the Transform, never inferred. Undeclared ⇒
stochastic, billable, dynamic.

## 5. The capability catalogue

`nw.transform_catalog()` — the `/object_info` equivalent over our own registry.
Roughly twenty lines, mirroring `nw.genre_catalog()` (`nw/genres.py:326`), and
the prerequisite for every agent-facing surface.

The ruling it enables: **raw registry introspection is never shown to the model
in production.** For open graph authoring, measured format validity is ~0.9
against a pass rate of ~0.28; retrieval augmentation nearly doubles pass
(0.28 → 0.52); a multi-agent scaffold *halved* validity to 0.47, below the bare
model; and composition from pre-verified modules reaches 100% pass / 83% resolve
against 56% / 32.5% for open authoring. The agent selects typed, pre-verified
**capabilities** and fills named slots validated against a Pydantic
`params_model`. The catalogue is what makes that composition possible.

## 6. Deliberately deferred

- **A general re-entrant scheduler.** ComfyUI's topological-dissolve scheduler
  with staging and re-entry, and its single `PENDING` code serving lazy inputs,
  expansion and async alike, are the best ideas in that codebase (~200 lines,
  and they compose soundly). But a fan-out of independent `CallPlan`s needs only
  bounded concurrency plus isolation. Build the scheduler when a Transform
  actually needs lazy inputs — and do not foreclose it. (Note that the
  concurrency itself does not exist yet either: `falaw.execute` is strictly
  sequential.)
- **Lazy inputs (`required_inputs`).** Strictly stronger in nw than in ComfyUI,
  because nw has `plan()`: ComfyUI must *run* upstream nodes to learn what a lazy
  node needs, while nw could consult `required_inputs` during planning, so the
  cost gate sees the pruned frontier before a single billable call. Worth
  building; not yet.
- **Per-output invalidation.** Whole-node invalidation is the honest default.
  Defer until a Transform exists that needs finer grain.

## 7. Anti-patterns — do not absorb these by accident

- **Positional parameter arrays** whose meaning depends on the node definition.
  nw uses named parameters, always.
- **Stringly-typed ports.** Pydantic v2 is the schema SSOT.
- **In-memory, non-content-addressed caching.**
- **A key composer that degrades instead of raising.** ComfyUI's `Unhashable`
  path silently disables caching; `falaw._key`'s `json.dumps(..., default=str)`
  (`falaw/cache.py:56-62`) is the same branch, except stringify collisions are
  silent **hits**, which is worse (thorwhalen/falaw#17).
- **Ordinal instance ids.** The easiest path in ComfyUI's `GraphBuilder` is the
  cache-destroying one, with no warning.
