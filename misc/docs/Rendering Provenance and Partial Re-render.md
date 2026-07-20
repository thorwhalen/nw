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
   only that node and re-stitch — never the whole piece.

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
- **nw.graph** exposes the freshness queries — `derived_from` (one hop),
  `descendants_of` / `stale_after` (transitive closure). `stale_after(changed_id)`
  *is* the partial-re-render frontier: everything downstream of a change.
- The **plan/execute** separation means a skeleton `render-result` (no artifact
  yet, with a cost estimate) exists before any money is spent; `execute` fills
  in the artifact. Cache keys are derived from *stable* inputs (uploaded URLs,
  content hashes) so a cache hit is honest.

This is already a rendering-provenance + partial-re-render engine. It is just
currently shaped around **video shots** (`ShotSpec`, `render-strategy` → mp4).

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
