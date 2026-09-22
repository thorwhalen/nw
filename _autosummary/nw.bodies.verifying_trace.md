# nw.bodies.verifying_trace

Body schema for verifying traces — what makes early cutoff possible.

URI: `annot://schema/verifying-trace/v1`

A **verifying trace** records, for one derived annotation, the \*content
digest\* each of its provenance parents had at the moment it was written:

```default
output annotation  X   was_derived_from = [A, B]
verifying trace    T   for_annotation_id = X
                       upstream = [(A, sha256…), (B, sha256…)]
```

`provenance.was_derived_from` alone says only *which* annotations X came
from. That makes freshness a reachability question — “A changed, so
everything reachable from A is suspect” — which is the *Make* cell of the
`Build Systems à la Carte` taxonomy and cannot cut off early. The digests
turn it into a **verifying-trace rebuilder** (Ninja / Shake / Salsa): when
A’s current value digest still equals the one X recorded, X is provably
unaffected and the walk stops there. See [`nw.freshness`](nw.freshness.md#module-nw.freshness) for the query
side.

## Why a sidecar annotation rather than a field on `lacing.Provenance`

`lacing.Provenance` is `frozen` / `extra="forbid"` and the
envelope has no migration ladder — `lacing.schema.register_migration`
migrates **bodies**, keyed by `body_schema_uri`, and the persisted store
refuses to open at a different `SCHEMA_VERSION`. Adding a field there is a
real on-disk migration against live project data.

lacing stores `body` as free-form JSON validated by `body_schema_uri`,
and `register_body_schema` is public API nw already calls six times, so a
new *body* type costs nothing. [`nw.bodies.decision`](nw.bodies.decision.md#module-nw.bodies.decision) is the precedent —
a timeless, project-local, typed provenance record stored under a sentinel
zero-duration reference. This is the same shape with a typed payload.
(thorwhalen/reelee#253 decision D6.)

## Two properties this schema deliberately has

1. **A trace is not a descendant of what it describes.** Its
   `was_derived_from` is empty and the link runs through the body’s
   `for_annotation_id` instead. Wiring it as a provenance edge would put
   every trace into its target’s `descendants_of` set — i.e. bookkeeping
   would show up in the user-facing freshness answer.
2. **A missing trace means “stale”, never “fresh”.** [`build_verifying_trace()`](#nw.bodies.verifying_trace.build_verifying_trace)
   returns `None` rather than a partial record when a parent cannot be
   resolved, and [`nw.freshness`](nw.freshness.md#module-nw.freshness) treats an annotation with no usable
   trace exactly as today’s reachability walk does. That is what makes this
   change need no migration: every pre-existing annotation keeps its current
   behaviour.

### Functions

| [`build_verifying_trace`](#nw.bodies.verifying_trace.build_verifying_trace)(\*, for_annotation_id, ...)   | Build the trace annotation for one derived annotation, or `None`.   |
|------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------|

### Classes

| [`UpstreamDigestV1`](#nw.bodies.verifying_trace.UpstreamDigestV1)(\*\*data)     | One `(upstream annotation, its value digest)` pair.   |
|---------------------------------------------------------------------------------|-------------------------------------------------------|
| [`VerifyingTraceBodyV1`](#nw.bodies.verifying_trace.VerifyingTraceBodyV1)(\*\*data) | Body of a verifying-trace annotation.                 |

### *class* nw.bodies.verifying_trace.UpstreamDigestV1(\*\*data)

Bases: `BaseModel`

One `(upstream annotation, its value digest)` pair.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.bodies.verifying_trace.VerifyingTraceBodyV1(\*\*data)

Bases: `BaseModel`

Body of a verifying-trace annotation.

`digest_scheme` is recorded rather than assumed: lacing documents that
changing `VALUE_FIELDS` or the canonicalisation is a breaking
cache-invalidation event and bumps the scheme string. A trace written
under an older scheme is not comparable, so [`nw.freshness`](nw.freshness.md#module-nw.freshness) treats
the mismatch as *unverifiable* (therefore stale) instead of comparing
digests that mean different things.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'extra': 'forbid', 'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### nw.bodies.verifying_trace.build_verifying_trace(, for_annotation_id, parent_ids, upstream, asset_id)

Build the trace annotation for one derived annotation, or `None`.

* **Parameters:**
  * **for_annotation_id** ([`UUID`](https://docs.python.org/3/library/uuid.html#uuid.UUID)) – Id of the annotation being described.
  * **parent_ids** ([`Iterable`](https://docs.python.org/3/library/typing.html#typing.Iterable)[[`UUID`](https://docs.python.org/3/library/uuid.html#uuid.UUID)]) – Its `provenance.was_derived_from`. Duplicates are
    collapsed, order preserved.
  * **upstream** ([`Sequence`](https://docs.python.org/3/library/typing.html#typing.Sequence)[`Annotation`]) – The resolved parent annotations. \*\*Must cover every id in

    ```
    ``
    ```

    parent_ids\`\`\*\* — a trace that omits a parent would let that
    parent change unnoticed.
  * **asset_id** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – The project’s asset id, for the sentinel reference.
* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[`Annotation`]
* **Returns:**
  The trace annotation, or `None` when there is nothing to verify
  (no parents) or the trace would be incomplete (a parent could not be
  resolved, or its value could not be digested). `None` is the safe
  answer in both cases: [`nw.freshness`](nw.freshness.md#module-nw.freshness) reads *no trace* as
  *unverifiable*, so the annotation keeps today’s conservative
  reachability behaviour instead of being silently declared fresh.
