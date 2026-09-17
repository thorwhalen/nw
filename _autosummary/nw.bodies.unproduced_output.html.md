# nw.bodies.unproduced_output

Body schema for unproduced-output records — nw#44.

URI: `annot://schema/unproduced-output/v1`

`TransformResult.failed` / `.blocked` (nw#25) carry a reason for a
planned output that was never produced, but only within the response that
produced them — reload the project and the hole is unexplained again. This
schema is the persisted record of that reason, written through
[`nw.graph.ProjectGraph.add_unproduced_output()`](nw.graph.html.md#nw.graph.ProjectGraph.add_unproduced_output) at the same choke point
`execute()` writes successes through
(`RenderStrategyTransform.execute` self-stamps the same way, since it
overrides `execute` and bypasses the base implementation).

## Why a sidecar tier rather than the output kind’s own schema

Reading the live registry, most output kinds already validate as an empty
skeleton — so nothing on the *write* side forces a new tier. The *read* side
decides it: several consumers (e.g. a retry planner) compute “already
produced” from rows carrying the output kind’s `body_schema_uri`. A
tombstone written under that URI would mark the failed unit \*\*already
produced\*\*, and a retry would never be planned again — turning a transient
failure into a permanent one. So this is its own tier, never borrowing the
output kind’s URI, exactly like [`nw.bodies.verifying_trace`](nw.bodies.verifying_trace.html.md#module-nw.bodies.verifying_trace) never
borrows its target’s.

## Identity and lifecycle

A record’s retirement/dedup key is **not** just `(transform_name,
upstream)` — two units of the same fan-out (different `mapping_key`, e.g.
two panels of the same beat) can share an identical upstream set, and
keying on upstream alone let one unit’s success retire an *unrelated* unit’s
still-outstanding record. The real key is:

- `(transform_name, instance_id, call_index)` when `instance_id` is
  known — the fan-out unit’s own
  `nw.transforms.fanout.work_item_instance_id()` (a pure function of
  > `(transform_name, mapping_key)`), threaded from

  `fan_out_execute()` through an `execute()`
  : implementation that accepts the `unit_instance_id` keyword (the same
    accepts-it-or-not seam `_accepts_keyword()`
    already uses for `on_failure`). `call_index` still has to agree even
    here: a unit’s own plan can carry more than one call, and matching on
    `instance_id` alone would collapse two of that unit’s own outputs into
    one key;
- `(transform_name, call_index, upstream)` otherwise — `call_index` is
  this output’s position within its `execute()` call’s `skeleton` tuple,
  which disambiguates multiple outputs of one batch call that share
  identical upstream parents even with no fan-out involved. This fallback
  is still not a full identity: two DIFFERENT units run outside a fan-out
  (no `instance_id` threaded at all) with identical upstream parents and
  the same `call_index` still alias, and one succeeding retires the
  other’s record too — the residual version of the original bug, scoped to
  callers that never pass `unit_instance_id`.

[`add_unproduced_output()`](nw.graph.html.md#nw.graph.ProjectGraph.add_unproduced_output) **dedupes on this key**:
a second record for the same identity replaces the first rather than
accumulating — a unit failing twice must not leave a first-run reason
readable as a live blocker after the unit has since failed differently (or
the record is stale but still there). [`add_annotation()`](nw.graph.html.md#nw.graph.ProjectGraph.add_annotation)
retires (removes) any record matching a real output’s identity the moment
that output is written — a successful retry clears its own record.

\*\*A write that bypasses `add_annotation` — a raw `store.add` — leaves
its matching record in place.\*\* It reads back as a live blocker after
reload even though the output was, in fact, produced. Route every derived
write through [`add_annotation()`](nw.graph.html.md#nw.graph.ProjectGraph.add_annotation), as the module
docstring on [`nw.graph`](nw.graph.html.md#module-nw.graph) already requires for verifying traces.

## Two properties this schema deliberately shares with the verifying trace

1. **Parentless.** `was_derived_from` is empty; the link to what it
   describes runs through the body’s own identity fields instead. Wiring it
   as a provenance edge would put every record into its subject’s
   `descendants_of` set, and — worse — [`nw.freshness`](nw.freshness.html.md#module-nw.freshness) would read
   matching digests as fresh, reporting a hole as verified-fresh.
2. **Excluded from freshness by construction.** Because it is parentless it
   never appears in any `descendants_of` walk, so `stale_verdicts_all` /
   `/api/freshness` are untouched and no lacing migration is owed. It is
   also excluded from [`nw.Project()`](nw.html.md#nw.Project)’s resumption-brief “last authored
   change” the same way (`nw.project._BOOKKEEPING_TIERS`) — it is written
   *after* the run it describes, not authored by the user.

## `reason` never carries raw exception text

The graph is exportable project data, and an exception’s `str()` can carry
a signed URL, a local path, or other operational detail that does not belong
in it. When the `FailedOutput` this record is built from carries an
`error`, [`add_unproduced_output()`](nw.graph.html.md#nw.graph.ProjectGraph.add_unproduced_output) stores a
fixed sentence plus `error_type` here and logs the original reason instead
(`logging.getLogger("nw.graph")`, at `WARNING`). A `reason` with no
`error` (e.g. a `"blocked"` output’s falaw-supplied human string, the
whole point of nw#25 —  *“skipped: no dialogue in this panel”*) is stored
as-is.

### Classes

| [`UnproducedOutputBodyV1`](#nw.bodies.unproduced_output.UnproducedOutputBodyV1)(\*\*data)   | Body of an unproduced-output record.   |
|-------------------------------------------------------------------------------------|----------------------------------------|

### *class* nw.bodies.unproduced_output.UnproducedOutputBodyV1(\*\*data)

Bases: `BaseModel`

Body of an unproduced-output record.

`status` mirrors `nw.transforms.fanout.UnitStatus`’s two
unproduced cases: `"failed"` (the call itself failed) or `"blocked"`
(an upstream call in the same plan failed first). `upstream` is stored
for the `call_index` fallback identity (see the module docstring); it
is not itself a sufficient key.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].
