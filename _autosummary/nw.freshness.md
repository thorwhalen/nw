# nw.freshness

Freshness with **early cutoff** — what is *actually* out of date.

`descendants_of` answers a reachability question: “what is downstream of
this?”. [`stale_after()`](#nw.freshness.stale_after) answers a freshness question: “what did this
change actually invalidate?”. Those are different questions, and until this
module existed nw answered the second with the first — a one-line alias, so
editing one beat in a 200-shot project reported every descendant stale
whether or not anything about it had changed.

In *Build Systems à la Carte* terms that is the **Make** cell: a dirty-bit
rebuilder, “early cutoff: no”. This module upgrades it to a \*\*verifying
trace\*\* rebuilder (Ninja, Shake, rustc/Salsa) using Salsa’s *backdating*
idea: compare the value you have against the value the consumer recorded,
and stop when they agree. One 32-byte digest comparison replaces loading a
40 MB video, which is what makes cutoff free rather than pointless.

## The rule, stated exactly

The walk has two frontiers over the same rule: [`stale_verdicts()`](#nw.freshness.stale_verdicts) /
[`stale_after()`](#nw.freshness.stale_after) classify everything reachable from a `changed_id`;
[`stale_verdicts_all()`](#nw.freshness.stale_verdicts_all) / [`all_stale()`](#nw.freshness.all_stale) classify every annotation
with at least one provenance parent — the whole-project snapshot a
freshness indicator wants (parentless annotations are never stale, or an
imported screenplay would read stale forever). An annotation `X` on the
frontier is **stale** when any of these holds, and **fresh** only when none
does:

- `X` itself carries an **unknown** `generated_at_time` — lacing’s tick-0
  `UNKNOWN_GENERATED_AT` sentinel (rows written through the REST path
  before lacing#35 still carry it; lacing#44). A row that cannot be placed
  in time is unverifiable, and it is never read as “the oldest thing in the
  project”. Regenerating `X` writes a fresh stamp, so this clears itself,
- no verifying trace was recorded for `X` ([`nw.bodies.verifying_trace`](nw.bodies.verifying_trace.md#module-nw.bodies.verifying_trace)),
- the trace was written under a different digest scheme,
- the trace’s upstream set is not exactly `X.provenance.was_derived_from`,
- a recorded upstream annotation no longer exists,
- a recorded upstream is **itself stale** — its value is about to change,
- a recorded upstream’s *current* value digest differs from the recorded one.

**A tick-0 \*parent\* is not a verdict.** Only `X`’s own stamp is checked.
Freshness has been digest-verified since nw#39: whether `X`’s inputs
changed is answered by comparing the parents’ *current* value digests to
the ones `X` recorded, and a parent’s unknown timestamp says nothing about
that. Staling `X` for a tick-0 parent would also never converge — a
legacy authored root is never regenerated, so no recompute could clear it,
only the timestamp backfill (lacing#46) — and a `regen_all_stale` loop
built on this walk would spend forever on every project with a legacy REST
root. The one place a timestamp *does* decide something is the trace
backfill’s bless walk ([`nw.graph.backfill_traces()`](nw.graph.md#nw.graph.backfill_traces)), which now refuses
any row it cannot place against its parents.

Two consequences worth stating, because both are easy to get backwards:

**The comparison lives on the edge, not on the node.** It is tempting to
classify `X` as fresh and then prune the walk there. That is wrong: `X`
having up-to-date *inputs* says nothing about whether `X`’s own *value*
still equals what its children recorded. Rewriting `X` in place makes
`X` fresh and its children stale at the same instant. So every reachable
node is classified against **its own** recorded digests; the walk prunes
nothing.

**Unverifiable means stale.** Every branch above defaults to stale.
Over-reporting wastes a recompute — which the content-addressed `falaw`
cache makes close to free. Under-reporting serves a stale artifact as if it
were current, so every ambiguous case resolves the other way. For the
*scoped* walk this also means no data migration: an annotation written
before traces existed reads as `no-trace` and behaves exactly as it did
under pure reachability. The *snapshot* walk has no such equivalence — on a
pre-trace project it reports every derived annotation stale until each is
rewritten through the trace-writing path; see [`stale_verdicts_all()`](#nw.freshness.stale_verdicts_all).

## What this does **not** catch

Stated so nobody reads more into the number than is there:

- **A changed Transform.** The trace records upstream *values*, not the
  producing code. Bumping a Transform’s implementation or prompt does not
  move any digest. `stale_after` answers “what did this *annotation* change
  invalidate”, not “what did this *code* change invalidate”.
- **A hand-edited output.** Editing `X`’s body directly leaves its trace
  matching its parents, so `X` reads fresh. That is the intended reading —
  a deliberate override is not stale relative to its inputs — but it does
  mean “fresh” is not “would regenerate identically”.
- **The plan → execute window.** The trace is written when the output is
  *persisted*, so an upstream mutated between planning and writing is
  recorded at its newer value. That needs a concurrent edit during a render.
- **The artifact tier.** Deliberately out of scope — but for a different
  reason than this line used to give. Artifact → artifact lineage has been
  *representable* since thorwhalen/lacing#14 landed (2026-08-16):
  `lacing.Provenance.was_derived_from` is `list[ProvenanceRef]` where
  `ProvenanceRef = UUID | AssetId`. What keeps it out of scope HERE is that
  nothing in nw *writes* artifact refs yet: the typed writers
  (`append_decision`, the `_put`/`_upsert` helpers) take
  `tuple[UUID, ...]`, and an annotation arriving at
  `ProjectGraph.add_annotation()` with an asset-id parent is persisted
  > but gets NO verifying trace — the parent never resolves as an annotation,
  > so the trace is declined and the row reads no-trace-stale
  > (thorwhalen/nw#55). This module is the **annotation** tier only until
  > that changes.

### Module Attributes

| [`STALE_REASONS`](#nw.freshness.STALE_REASONS)   | Every reason that resolves to *stale*.   |
|------------------------------------------------------------------|------------------------------------------|

### Functions

| [`all_stale`](#nw.freshness.all_stale)(project_root)                  | Every annotation that is currently stale, regardless of cause.        |
|-------------------------------------------------------------------------------------------|-----------------------------------------------------------------------|
| [`stale_after`](#nw.freshness.stale_after)(project_root, changed_id)    | Return every annotation that `changed_id` actually invalidated.       |
| [`stale_verdicts`](#nw.freshness.stale_verdicts)(project_root, changed_id) | Classify every annotation downstream of `changed_id`.                 |
| [`stale_verdicts_all`](#nw.freshness.stale_verdicts_all)(project_root)         | Classify every derived annotation in the project — the snapshot form. |

### Classes

| [`FreshnessVerdict`](#nw.freshness.FreshnessVerdict)(annotation, is_stale, reason)   | Why one reachable annotation was judged stale (or not).   |
|---------------------------------------------------------------------------------------------------|-----------------------------------------------------------|

### *class* nw.freshness.FreshnessVerdict(annotation, is_stale, reason, upstream_id=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Why one reachable annotation was judged stale (or not).

Emitted by [`stale_verdicts()`](#nw.freshness.stale_verdicts). `reason` is one of the
`REASON_*` constants; `upstream_id` names the parent that decided it
when a single parent did, so “why is this stale?” has an answer that does
not require re-deriving the walk by hand.

### nw.freshness.STALE_REASONS *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), ...]* *= ('generated-at-unknown', 'no-trace', 'digest-scheme-changed', 'trace-parents-differ', 'trace-unreadable', 'upstream-missing', 'upstream-stale', 'upstream-changed', 'provenance-cycle')*

Every reason that resolves to *stale*. `REASON_FRESH` is the only
verdict that does not, which is the invariant that keeps “unverifiable means
stale” true by construction rather than by review.

### nw.freshness.all_stale(project_root)

Every annotation that is currently stale, regardless of cause.

[`stale_verdicts_all()`](#nw.freshness.stale_verdicts_all) with the fresh verdicts dropped — the
snapshot counterpart of [`stale_after()`](#nw.freshness.stale_after), and the primitive a
freshness indicator or a “regenerate everything stale” verb should sit
on instead of re-deriving its own definition of the word.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[`Annotation`]

### nw.freshness.stale_after(project_root, changed_id)

Return every annotation that `changed_id` actually invalidated.

The freshness operation. `changed_id`’s descendants are walked and each
is checked against the upstream value digests it recorded when it was
written ([`nw.bodies.verifying_trace`](nw.bodies.verifying_trace.md#module-nw.bodies.verifying_trace)). A descendant whose recorded
inputs still match the current ones is **not** returned — that is the
early cutoff, and it is why this is not `descendants_of` under another
name. The full rule, and the four things it deliberately does not catch,
are in this module’s docstring.

The returned list does NOT include `changed_id` itself (it is the source
of the change, not a stale derivative).

`descendants_of` is unchanged and still answers the reachability
question — “what is downstream of this?” is legitimate and the two verbs
are no longer synonyms. Use [`stale_verdicts()`](#nw.freshness.stale_verdicts) when you need the
*reason* a given annotation is in (or out of) this set.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[`Annotation`]

### nw.freshness.stale_verdicts(project_root, changed_id)

Classify every annotation downstream of `changed_id`.

The explained form of [`stale_after()`](#nw.freshness.stale_after): one verdict per reachable
annotation, stale or not, in a deterministic order (generation time, then
id). `changed_id` itself is never included — it is the source of the
change, not a derivative of it.

Use this when the *number* is being questioned. `stale_after` is the
same walk with the fresh verdicts dropped;
[`stale_verdicts_all()`](#nw.freshness.stale_verdicts_all) is the same classification with no
`changed_id` — the whole-project snapshot.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`FreshnessVerdict`](#nw.freshness.FreshnessVerdict)]

### nw.freshness.stale_verdicts_all(project_root)

Classify every derived annotation in the project — the snapshot form.

The question a freshness *indicator* asks: “what is stale in this
project right now?”, with no `changed_id` to anchor on. Same
verifying-trace classification as [`stale_verdicts()`](#nw.freshness.stale_verdicts), over a wider
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
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`FreshnessVerdict`](#nw.freshness.FreshnessVerdict)]
