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

## How nw implements it today (video shots)

- **lacing** is the substrate: content-addressed `Artifact`s (immutable, hashed
  media), a `Provenance` DAG (`was_derived_from`), typed annotation bodies,
  rational-time intervals.
- **nw.workflow** splits a render into `prepare_shot` → `plan_render_shot`
  (pure data — inspect cost, no billing) → `execute_render` (the only billable
  phase). `execute` records a `render-result/v1` annotation whose
  `was_derived_from` includes the shot's id, and whose heavy output (the mp4) is
  a referenced `Artifact`, not inline.
- **nw.graph** exposes the reachability queries — `derived_from` (one hop),
  `descendants_of` (transitive closure) — and records the verifying trace on
  every derived write. **nw.freshness** exposes `stale_after` / `stale_verdicts`:
  the same walk, with each node checked against the upstream digests it
  recorded. `stale_after(changed_id)` *is* the partial-re-render frontier —
  everything downstream of a change **that the change actually invalidated**.
- The **plan/execute** separation means a skeleton `render-result` (no artifact
  yet, with a cost estimate) exists before any money is spent; `execute` fills
  in the artifact. Cache keys are derived from *stable* inputs (uploaded URLs,
  content hashes) so a cache hit is honest (see "Status check" below — not true
  today).

This is already a rendering-provenance + partial-re-render engine. It is just
currently shaped around **video shots** (`ShotSpec`, `render-strategy` → mp4).

## Status check (2026-08): what is implemented, and what this document over-claims

Two claims above are aspirational rather than descriptive. They are corrected
here rather than deleted, because the design they describe is still the target.

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
so a cache hit is honest" — the opposite is true today.** `falaw`'s per-call key
is `sha256({app, args})` (`falaw/cache.py:56`) over the *resolved* arguments,
and resolution rewrites `"<from N>"` to `artifacts[N].url` (`falaw/plan.py:432`
→ `_lookup_artifact_url`, `falaw/plan.py:482`) — a fresh URL per generation. A
byte-identical upstream regeneration therefore produces a downstream cache
**miss**. Separately, the plan-time cache peek keys on *unresolved* arguments
while execution keys on *resolved* ones, so `cache_status`,
`cache_hit_savings_usd` and the cost gate are all computed against a key that is
never used. Both are tracked in `falaw`: content-addressed artifacts
(thorwhalen/falaw#14), the plan-time peek (thorwhalen/falaw#15), fail-loud key
composition (thorwhalen/falaw#17).

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
3. **`lacing`: widen `Provenance.was_derived_from`** (thorwhalen/lacing#14). It
   is typed `list[UUID]` (`lacing/model.py:86`), so it cannot hold a 64-char hex
   `asset_id`, and artifact-to-artifact lineage is therefore structurally
   unrepresentable and always empty. Required for the *artifact* tier, and the
   only step needing a real on-disk migration.

**Steps 1–2 are independent of step 3.** The lineage graph is complete at the
annotation tier today (`nw/transforms/_provenance.py:18` `derive_provenance`
populates `was_derived_from` on every Transform output) and empty at the
artifact tier — which is precisely the tier where the expensive things live.

### Two verbs, not one

`descendants_of` is unchanged: "what is downstream of this?" is a legitimate
reachability query and part of the public API. `stale_after` is now a different
answer to a different question — "what is downstream of this *and actually out
of date*?" — so the two are no longer synonyms, which is what the README
already claimed. `nw.stale_verdicts` returns the same walk with a `reason` per
annotation, because a freshness number nobody can interrogate is a number
nobody trusts.

## Generalizing beyond shots (audio weaving, and any render)

The same pattern applies to **any** projection, including an audio podcast that
weaves narration (TTS) with extracted audio segments (music, audiobook, news,
SFX). The render *choices* there are things like: voice / delivery preset,
multi-voice turn grouping, speed jitter, clip extraction padding, ducking depth,
crossfades, loudness target (a `WeaveConfig`). To get trace + tune + partial
re-render for audio, model those choices as nodes with provenance, exactly as
nw does for shots:

| Node (body schema) | Records | `was_derived_from` |
|---|---|---|
| `render-config/v1` | a frozen config snapshot (all params) | — (a root input) |
| `voice-assignment/v1` | which voice a turn got (+ pool, seed) | the narrative beat(s) |
| `narration-render/v1` | the audio `Artifact` for one turn | beat text · voice-assignment · render-config |
| `clip-extraction/v1` | the cut segment `Artifact` (padded interval) | audio-clip node · source asset · render-config |
| `episode-render/v1` | the assembled output `Artifact` | ordered member renders · render-config |

Partial re-render then falls out of the existing machinery: compute each output
node's cache key from its inputs' hashes + the config; reuse the `Artifact` if
the key is unchanged, otherwise regenerate; re-assemble only when membership or
keys change. A change to one beat or one parameter scopes the recompute via
`stale_after`.

**Design stance:** keep the render-provenance engine (`prepare/plan/execute`,
`render-result`, freshness queries) **render-kind-agnostic** in nw, and let each
app (a music video via reelee, a lyrics podcast, an audiobook explainer) supply
its own render-node body schemas + Transforms. See the tracking issue for the
audio/weave generalization.

## Related

- lacing: `Artifact`, `Provenance`, content addressing — the storage/graph layer.
- reelee: a consumer that already uses `descendants_of` / `stale_after` for
  freshness.
- The Hamilton lyrics-podcast: the first **audio** consumer; its
  `docs/design/render-provenance.md` describes the weave-specific node model.
- `misc/docs/Execution Semantics and Fan-out.md` — the companion doc: how work is
  scheduled, fanned out, isolated on failure, and made discoverable to an agent.
  This document owns *why* choices are recorded as linked artifacts; that one
  owns *how* the work runs.
