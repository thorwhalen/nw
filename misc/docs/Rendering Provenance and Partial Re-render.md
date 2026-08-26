# Rendering provenance & partial re-render

*Why nw records rendering **choices** — not just content — as linked
artifacts, and what that buys us.*

## The principle

A rendered output (a video, a podcast episode, a slideshow) is **not authored
directly**. It is a **projection** of a graph of linked artifacts. nw's job is
to make that graph — and the act of projecting it — first-class, inspectable,
and incremental.

The graph must hold two kinds of thing:

1. **Content** — *what* is in the piece: sections, shots, lyric lines,
   narrative beats, characters, the source media.
2. **Choices** — *how* it is rendered: the render strategy, model + parameters,
   voice / delivery, timing, mix. These are decisions, and decisions are data.

Most pipelines keep (1) in files and (2) in ephemeral function arguments that
vanish the moment the render finishes. nw keeps **both in the graph**, joined by
provenance edges, so every output can be traced back to the exact inputs and
parameters that produced it.

## What recording choices-as-artifacts buys us

1. **Traceability.** Every second of output traces back to its source content
   nodes *and* the parameters and tool/version that produced it
   (`provenance.was_generated_by` = the activity/Transform;
   `provenance.was_derived_from` = the input annotations). You can always answer
   "why does this look/sound like this?"

2. **Tunability.** Parameters are first-class, versioned data — not lost args.
   Renders become comparable: diff two configs, A/B two takes, see which knob
   changed which output.

3. **Partial re-render — the big one.** Because every output artifact records
   *what it was derived from*, a change to one input invalidates **only its
   descendants**. `stale_after(changed_id)` returns exactly the set to
   recompute. Re-rendering becomes a **memoized build DAG** (Make/Bazel for
   media): edit one beat's text or swap one shot's strategy, and you recompute
   only that node and re-stitch — never the whole piece (shipped at the
   annotation tier — see "Status check" below for the four exclusions).

4. **Localized editing.** The graph is the *index* for "find the thing to
   change." The unit you want to fix (a shot, a narration turn, a clip) is a
   node; edit it, re-render its descendants, splice back.

5. **Reproducibility.** A render is a (near-)pure function of *graph state +
   config snapshot*. Same inputs → same output, modulo model nondeterminism,
   which we pin behind content-addressed caches.

## How nw implements it today

Read this section as **two** layers, because the document used to conflate them
and that conflation is what nw#9 was filed against. One is the general engine;
the other is a shot-only path that predates it.

**The substrate.** **lacing** provides content-addressed `Artifact`s (immutable,
hashed media), a `Provenance` DAG (`was_derived_from`), typed annotation bodies,
and rational-time intervals.

### The engine: `nw.transforms` — render-kind-agnostic

A `Transform` is the "A-annotation → B-annotation" arrow, split the same two
ways: `plan()` is **pure data** (a `falaw.Plan` plus *skeleton* output
annotations that already carry provenance, so a dry run shows what will be
produced, from what, and at what cost — no billable calls), and `execute()`
runs the Plan, completes the skeletons with real artifact references, and
writes them **through `ProjectGraph.add_annotation`**, which is what records the
verifying trace.

Nothing in that contract mentions video. `input_kinds` / `output_kind` are body
schema URIs the *app* owns; the registry (`xdol.Registry`, keyed by
`<from_kind>_to_<to_kind>[.<flavor>[.<variant>]]`) is open-closed, so an app adds
its arrows without touching nw. This is the layer reelee's production
`narrative_video` rides, and — since nw#9 was filed — the layer **braidio's audio
weave rides too** (see "Generalizing beyond shots" below). The generalization
this document once described as future work is therefore *demonstrated*, not
merely designed.

### The legacy path: `nw.workflow` + `nw.renderers` — shot-only, retained

`prepare_shot` → `plan_render_shot` → `execute_render` splits a **video shot**
render the same three ways, and `nw.renderers` holds the five `Strategy`
implementations it dispatches to (`lipsync`, `image_to_video`, `text_to_video`,
`still`, `composite_lipsync`). It bakes in `ShotSpec`, `render_strategy`, and an
`output.mp4`: this path genuinely does assume video, and that is the honest
statement of it.

It is **deliberately retained** as the shot render unit, and it is **not** the
thing to generalize into a new render kind. Measured at HEAD (2026-08-27),
`prepare_shot`, `plan_render_shot`, `execute_render`, `ShotSpec` and
`get_strategy` have **zero** call sites outside nw — a grep across reelee, muvid
and braidio finds none. (muvid was previously named as its consumer; it is not.
muvid has its own `muvid.schema.ShotSpec` and its own ffmpeg strategies, and
says so in `muvid/genre.py`: "not `nw.renderers` strategies".)

**"Legacy" here means "not an entry point", not "unreferenced" — and the two
layers are stacked, not parallel.** Inside nw this path is load-bearing for the
engine itself: `nw/transforms/_adapters/render_strategy.py` wraps every
registered `Strategy` as a `shot_to_render_result.fal.<name>` Transform at
import time, and that Transform calls `prepare_shot` in both its `plan`
(`upload=params.upload`) and its `execute` (`upload=False`); `plan_render_shot`
and `execute_render` in turn call `nw.renderers.get_strategy`; and
`nw.genres.Genre.missing_strategies()` validates a genre's `strategy_names`
against `nw.renderers`. So the engine's *shot* arrow is built **on top of** the
path this section calls legacy. Deleting `workflow.py` as dead code, or changing
a signature in it, breaks the Transform path — which is the path this document
recommends. A new render *kind* registers Transforms; a new way to render a
*shot* is still at home here, and gets its Transform adaptation for free.

### Freshness

- **nw.graph** exposes the reachability queries — `derived_from` (one hop),
  `descendants_of` (transitive closure) — and records the verifying trace on
  every derived write. **nw.freshness** exposes `stale_after` / `stale_verdicts`:
  the same walk, with each node checked against the upstream digests it
  recorded. `stale_after(changed_id)` *is* the partial-re-render frontier —
  everything downstream of a change **that the change actually invalidated**.
- The **plan/execute** separation means a skeleton output annotation (no
  artifact yet, with a cost estimate) exists before any money is spent;
  `execute` fills in the artifact. Cache keys are derived from *stable* inputs
  (uploaded URLs, content hashes) so a cache hit is honest (see "Status check"
  below — true of execution since falaw 0.0.24, and of the plan-time preview
  since thorwhalen/falaw#15 closed on 2026-08-11: a *chained* call is no longer
  peeked with a key nothing writes, it reports `cache_status="unknown"`).

## Status check (2026-08): what is implemented, and what this document over-claims

Two claims above were aspirational rather than descriptive when this document
was written. Both have since been fixed in code; the history is kept rather
than deleted, because knowing *what the bug was* is what stops it being
reintroduced.

**"`stale_after(changed_id)` returns exactly the set to recompute" — ~~not
yet~~ shipped at the annotation tier (nw#24, 2026-08-06), with the four
exclusions listed at the end of this section.**

*What was wrong.* `nw.stale_after` was a one-line alias for `descendants_of`:
its whole body was `return descendants_of(project_root, changed_id)`. It was
pure reachability over `provenance.was_derived_from` and it compared nothing.
`Provenance.generated_at_time` was written on every annotation and read by no
freshness function in the package. So the set returned was *everything
downstream of the change* — whether or not it was actually out of date, and
whether or not regenerating it would produce identical content.

In *Build Systems à la Carte* terms that is a **dirty-bit rebuilder**: the
*Make* cell of the scheduler × rebuilder grid, which the paper marks "early
cutoff: **No**". Not Bazel. The target — now implemented — is a
**verifying-trace** rebuilder (Ninja, Shake, rustc/Salsa): record, per output,
the value digests of the inputs it was built from; stop propagating wherever
those digests still agree. Salsa's name for that step, *backdating*, is the
clearest term in the literature.

*How it works now.* Every derived write records a **verifying trace** —
`nw/bodies/verifying_trace.py`, an nw-owned sidecar annotation, *not* a field
on `lacing.Provenance` (which is frozen/`extra="forbid"` behind a
`SCHEMA_VERSION` with no envelope migration ladder; see reelee#253 decision
D6). `nw/freshness.py` walks the reachable set and compares each recorded
upstream digest against `lacing.annotation_value_digest` of that upstream
today.

Two properties are load-bearing and easy to get backwards:

- **The comparison is on the edge, not the node.** Classifying a node as fresh
  and pruning the walk there is wrong: fresh *inputs* say nothing about whether
  the node's own *value* still equals what its children recorded. Rewriting a
  node in place makes it fresh and its children stale in the same instant.
- **Unverifiable means stale.** No trace, a deleted input, a trace that no
  longer covers the current parents, a different digest scheme — all resolve to
  stale. Over-reporting costs a recompute; under-reporting serves a stale
  artifact. This is also why the change needed **no data migration**:
  pre-existing annotations have no trace and behave exactly as before.

*What it still does not catch*, stated so the claim above is not read too
widely: a changed **Transform** (the trace records upstream values, not the
producing code); a **hand-edited** output (fresh relative to its inputs, which
is the intended reading); an upstream mutated inside the **plan → execute
window**; and the **artifact tier**, which needs step 3 below.

**"Cache keys are derived from *stable* inputs (uploaded URLs, content hashes)
so a cache hit is honest" — ~~the opposite is true today~~ shipped, in two
passes: *execution* since falaw 0.0.24 (thorwhalen/falaw#14, closed
2026-08-06), and the *plan-time* preview since thorwhalen/falaw#15 (closed
2026-08-11).**

*What was wrong.* `falaw`'s per-call key is `sha256({app, args})`
(`falaw/cache.py::_key`) over the **resolved** arguments, and resolution
rewrote `"<from N>"` to `artifacts[N].url` via a single `_lookup_artifact_url`
— a fresh URL per generation. A byte-identical upstream regeneration therefore
produced a downstream cache **miss**, and re-billed a $0.35–$1.50 clip for work
that had not changed.

*How it works now.* `_lookup_artifact_url` is gone. A `"<from N>"` placeholder
is resolved **twice**, against two different needs (`falaw/plan.py`):

- `_wire_ref` → the upstream's **URL**, which is what the fal payload must
  carry;
- `_key_ref` → `sha256:<upstream asset_id>`, which is what the **cache key**
  carries whenever the upstream's bytes were materialized (`bytes_size > 0`).
  The `sha256:` prefix is deliberate: a reader of a cache manifest can tell at
  a glance that an argument was keyed on *what* the upstream produced rather
  than *where* it was served from.

With no materialized bytes, `_key_ref` falls back to the URL — sound but
unreusable (a guaranteed miss), which beats inventing an id that could produce
a *wrong* hit. Materializing those bytes is also what makes `Artifact.asset_id`
the SHA-256 of the media, honouring lacing's contract, instead of the SHA-256
of a URL.

*The plan-time half, closed 2026-08-11 (falaw#15 / falaw#17 — both CLOSED; do
not act on this paragraph as if they were open).* The plan-time cache peek used
to key on *unresolved* arguments while execution keys on *resolved* ones, so
`cache_status`, `cache_hit_savings_usd` and the cost gate were computed against
a key nothing ever writes — a guaranteed false `"miss"` on every **chained**
call, which under prepaid billing (a quote that may be *deducted*) is a billing
bug. `falaw.make_call_plan` now **declines to peek** a call whose arguments
still hold a `"<from N>"` placeholder, and reports `cache_status="unknown"`
instead (`falaw/plan.py`, falaw 0.0.40) — the honest answer, and exactly the
case `CacheStatus` documents `"unknown"` for. **Unknown is not zero:** a
consumer's cost gate must read it as *not yet knowable* and force approval,
never as free. Key composition no longer goes through
`json.dumps(..., default=str)` either: `ensure_canonical` raises
`FalNonCanonicalArgument` while planning is still free, rather than silently
colliding on the way to the network (thorwhalen/falaw#17).

**What this costs.** For a 200-shot fan-out, a re-run without early cutoff is
the difference between free and several hundred dollars. That is why the
freshness work is sequenced *before* any fan-out primitive.

### The dependency chain, shortest path first

1. ~~**`lacing`: an annotation *value* digest**~~ **shipped** —
   `lacing.annotation_value_digest`, lacing 0.0.25 (thorwhalen/lacing#16). A
   pure function over an annotation's value, excluding `id` and `provenance` —
   both of which change on every regeneration even when the content does not.
   No schema change, no migration.
2. ~~**`nw`: `stale_after` stops being an alias**~~ **shipped** —
   `nw.freshness` (nw#24). Compares recorded upstream value digests against
   current ones. The *annotation* tier, needing only step 1.
3. ~~**`lacing`: widen `Provenance.was_derived_from`**~~ **shipped in lacing,
   not yet used in nw** — thorwhalen/lacing#14 (defect D5), landed 2026-08-16
   with a real store migration. `was_derived_from` is now
   `list[ProvenanceRef]` where `ProvenanceRef = UUID | AssetId`, the two being
   format-disjoint; `lacing.partition_provenance_refs` is the one discriminator
   every lineage walker should use rather than re-deriving the union rule. This
   was the only step needing a real on-disk migration, and nw opens project
   stores with `migrate=True` so pre-D5 v1 files upgrade rather than refuse
   (nw#53).

**The artifact tier is now *representable* but still *empty*.** The lineage
graph is complete at the annotation tier (`nw/transforms/_provenance.py`
`derive_provenance` populates `was_derived_from` on every Transform output) —
but it populates **annotation ids only**. Nothing in nw yet emits an
artifact→artifact `AssetId` edge, so the tier where the expensive things live
still has no lineage. The blocker moved from lacing to nw; that remaining work
is tracked separately and is not part of nw#9.

### Two verbs, not one

`descendants_of` is unchanged: "what is downstream of this?" is a legitimate
reachability query and part of the public API. `stale_after` is now a different
answer to a different question — "what is downstream of this *and actually out
of date*?" — so the two are no longer synonyms, which is what the README
already claimed. `nw.stale_verdicts` returns the same walk with a `reason` per
annotation, because a freshness number nobody can interrogate is a number
nobody trusts.

## Generalizing beyond shots (audio weaving, and any render) — done

The same pattern applies to **any** projection, including an audio podcast that
weaves narration (TTS) with extracted audio segments (music, audiobook, news,
SFX). The render *choices* there are things like: voice / delivery preset,
multi-voice turn grouping, speed jitter, segment extraction padding, ducking
depth, crossfades, loudness target (a `WeaveConfig`).

**This is no longer a sketch.** `braidio` — the commentary-talk audio package —
is the first audio consumer, and it got there **without a single change to nw**:
it declares its own body schemas (`braidio/bodies/_render_nodes.py`) and
registers its own Transforms (`braidio/transforms/`) into `nw.transforms`,
exactly as reelee's `panel_to_clip.fal.default` does. That is the seam working
as designed, and it is the evidence for the "keep the engine render-kind-agnostic"
stance below.

The node model braidio actually ships (names are **braidio's**, which is what
this table is for — an earlier draft named a `render-config/v1` and a
`clip-extraction/v1` that were never built):

| Node (body schema) | Records | Produced by | `was_derived_from` |
|---|---|---|---|
| `weave-config/v1` | a frozen `WeaveConfig` snapshot (all params) | ingest | — (a root input) |
| `source-media/v1` | an imported source asset + its rights posture | ingest | — (a root input) |
| `voice-assignment/v1` | which voice a turn got (+ pool, seed) | `beat_to_voice_assignment.default` | narrative beat · weave-config |
| `narration-render/v1` | the audio `Artifact` for one turn (+ `cache_key`) | `narration_render.tts` | beat · voice-assignment · weave-config |
| `segment-extraction/v1` | the cut+padded segment `Artifact` (+ `cache_key`) | `segment_extraction.ffmpeg` | audio-clip · source-media · weave-config |
| `episode-render/v1` | the assembled output `Artifact` (+ ordered members) | `weave_to_episode.default` (batch) | ordered member renders · weave-config |

Partial re-render falls out of the existing machinery: each output node's cache
key is computed from its inputs' hashes + the config; the `Artifact` is reused
if the key is unchanged, otherwise regenerated; re-assembly happens only when
membership or keys change. A change to one beat or one parameter scopes the
recompute via `stale_after`, because every one of those writes goes through
`ProjectGraph.add_annotation`.

**One thing that is *not* shared, and should be.** falaw's content-addressed
cache covers fal calls. braidio's two expensive non-fal Transforms (ElevenLabs
TTS, ffmpeg extraction) are outside it, so each carries an explicit `cache_key`
field on its body and does its own compare-and-skip — via a local
`cached_output()` helper and a hash function imported from
**`mixing._cache`, a private module of another package**. That is item (b) of
nw#9 ("a small shared `cache_key` helper for non-fal Transforms") still open,
and it is now a concrete cross-package smell rather than a nice-to-have. Tracked
separately; not resolved by this document.

**Design stance (unchanged, now with evidence):** keep the render-provenance
engine **render-kind-agnostic** in `nw.transforms`, and let each app (a music
video via reelee, a commentary-talk episode via braidio, an audiobook explainer)
supply its own render-node body schemas + Transforms. Explicitly **not**
recommended: generalizing `render-result/v1` into a universal render body — a
per-app render body is the better shape, and braidio's `episode-render/v1` is
the worked example. `nw.workflow` / `nw.renderers` stay as the shot render unit:
they are not the extension point for a new render *kind*, but they remain the
right place for a new *shot* strategy — which the adapter then publishes as a
Transform automatically.

## Related

- lacing: `Artifact`, `Provenance`, content addressing — the storage/graph layer.
- reelee: a consumer that already uses `descendants_of` / `stale_after` for
  freshness.
- braidio: the first **shipped** audio consumer, riding `nw.transforms` with its
  own body schemas (`braidio/bodies/_render_nodes.py`) and Transforms
  (`braidio/transforms/`). The node model above is braidio's.
- The Hamilton lyrics-podcast (thorwhalen/Hamilton epic #18, still open): the
  audio consumer this document was originally written for, and the only place
  the *weave-specific* node model is described at length — its
  `docs/design/render-provenance.md`. braidio is where that weaving engine
  graduates into a package; the pointer is kept because braidio's schema list
  above is not a substitute for that description.
- `misc/docs/Execution Semantics and Fan-out.md` — the companion doc: how work is
  scheduled, fanned out, isolated on failure, and made discoverable to an agent.
  This document owns *why* choices are recorded as linked artifacts; that one
  owns *how* the work runs.
