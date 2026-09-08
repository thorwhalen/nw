"""Body schema for unproduced-output records — nw#44.

URI: ``annot://schema/unproduced-output/v1``

``TransformResult.failed`` / ``.blocked`` (nw#25) carry a reason for a
planned output that was never produced, but only within the response that
produced them — reload the project and the hole is unexplained again. This
schema is the persisted record of that reason, written through
:meth:`nw.graph.ProjectGraph.add_unproduced_output` at the same choke point
:meth:`~nw.transforms.BaseTransform.execute` writes successes through
(``RenderStrategyTransform.execute`` self-stamps the same way, since it
overrides ``execute`` and bypasses the base implementation).

Why a sidecar tier rather than the output kind's own schema
-------------------------------------------------------------
Reading the live registry, most output kinds already validate as an empty
skeleton — so nothing on the *write* side forces a new tier. The *read* side
decides it: several consumers (e.g. a retry planner) compute "already
produced" from rows carrying the output kind's ``body_schema_uri``. A
tombstone written under that URI would mark the failed unit **already
produced**, and a retry would never be planned again — turning a transient
failure into a permanent one. So this is its own tier, never borrowing the
output kind's URI, exactly like :mod:`nw.bodies.verifying_trace` never
borrows its target's.

Identity and lifecycle
-----------------------
A record's retirement/dedup key is **not** just ``(transform_name,
upstream)`` — two units of the same fan-out (different ``mapping_key``, e.g.
two panels of the same beat) can share an identical upstream set, and
keying on upstream alone let one unit's success retire an *unrelated* unit's
still-outstanding record. The real key is:

- ``(transform_name, instance_id, call_index)`` when ``instance_id`` is
  known — the fan-out unit's own
  :func:`nw.transforms.fanout.work_item_instance_id` (a pure function of
  ``(transform_name, mapping_key)``), threaded from
  :func:`~nw.transforms.fanout.fan_out_execute` through an ``execute()``
  implementation that accepts the ``unit_instance_id`` keyword (the same
  accepts-it-or-not seam :func:`~nw.transforms.fanout._accepts_keyword`
  already uses for ``on_failure``). ``call_index`` still has to agree even
  here: a unit's own plan can carry more than one call, and matching on
  ``instance_id`` alone would collapse two of that unit's own outputs into
  one key;
- ``(transform_name, call_index, upstream)`` otherwise — ``call_index`` is
  this output's position within its ``execute()`` call's ``skeleton`` tuple,
  which disambiguates multiple outputs of one batch call that share
  identical upstream parents even with no fan-out involved. This fallback
  is still not a full identity: two DIFFERENT units run outside a fan-out
  (no ``instance_id`` threaded at all) with identical upstream parents and
  the same ``call_index`` still alias, and one succeeding retires the
  other's record too — the residual version of the original bug, scoped to
  callers that never pass ``unit_instance_id``.

:meth:`~nw.graph.ProjectGraph.add_unproduced_output` **dedupes on this key**:
a second record for the same identity replaces the first rather than
accumulating — a unit failing twice must not leave a first-run reason
readable as a live blocker after the unit has since failed differently (or
the record is stale but still there). :meth:`~nw.graph.ProjectGraph.add_annotation`
retires (removes) any record matching a real output's identity the moment
that output is written — a successful retry clears its own record.

**A write that bypasses ``add_annotation`` — a raw ``store.add`` — leaves
its matching record in place.** It reads back as a live blocker after
reload even though the output was, in fact, produced. Route every derived
write through :meth:`~nw.graph.ProjectGraph.add_annotation`, as the module
docstring on :mod:`nw.graph` already requires for verifying traces.

Two properties this schema deliberately shares with the verifying trace
--------------------------------------------------------------------------
1. **Parentless.** ``was_derived_from`` is empty; the link to what it
   describes runs through the body's own identity fields instead. Wiring it
   as a provenance edge would put every record into its subject's
   ``descendants_of`` set, and — worse — :mod:`nw.freshness` would read
   matching digests as fresh, reporting a hole as verified-fresh.
2. **Excluded from freshness by construction.** Because it is parentless it
   never appears in any ``descendants_of`` walk, so ``stale_verdicts_all`` /
   ``/api/freshness`` are untouched and no lacing migration is owed. It is
   also excluded from :func:`nw.Project`'s resumption-brief "last authored
   change" the same way (``nw.project._BOOKKEEPING_TIERS``) — it is written
   *after* the run it describes, not authored by the user.

``reason`` never carries raw exception text
---------------------------------------------
The graph is exportable project data, and an exception's ``str()`` can carry
a signed URL, a local path, or other operational detail that does not belong
in it. When the ``FailedOutput`` this record is built from carries an
``error``, :meth:`~nw.graph.ProjectGraph.add_unproduced_output` stores a
fixed sentence plus ``error_type`` here and logs the original reason instead
(``logging.getLogger("nw.graph")``, at ``WARNING``). A ``reason`` with no
``error`` (e.g. a ``"blocked"`` output's falaw-supplied human string, the
whole point of nw#25 — *"skipped: no dialogue in this panel"*) is stored
as-is.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from lacing.schema import register_body_schema


UNPRODUCED_OUTPUT_BODY_SCHEMA_URI = "annot://schema/unproduced-output/v1"
UNPRODUCED_OUTPUT_TIER = "unproduced-output"


class UnproducedOutputBodyV1(BaseModel):
    """Body of an unproduced-output record.

    ``status`` mirrors :class:`nw.transforms.fanout.UnitStatus`'s two
    unproduced cases: ``"failed"`` (the call itself failed) or ``"blocked"``
    (an upstream call in the same plan failed first). ``upstream`` is stored
    for the ``call_index`` fallback identity (see the module docstring); it
    is not itself a sufficient key.
    """

    model_config = {"frozen": True, "extra": "forbid"}

    transform_name: str = Field(
        ..., description="The Transform whose planned output this describes."
    )
    instance_id: Optional[str] = Field(
        default=None,
        description=(
            "UUIDv5 str of (transform_name, mapping_key) — the fan-out "
            "unit's identity (nw.transforms.fanout.work_item_instance_id), "
            "when known. The primary half of the retirement key."
        ),
    )
    call_index: Optional[int] = Field(
        default=None,
        description=(
            "This output's index within its execute() call's skeleton "
            "tuple — the retirement key's fallback discriminator when "
            "`instance_id` is None (e.g. a non-fan-out batch call)."
        ),
    )
    upstream: tuple[str, ...] = Field(
        default=(),
        description=(
            "UUIDs (as strings) of the output's provenance parents — "
            "informational, and part of the retirement key only when "
            "`instance_id` is None."
        ),
    )
    output_kind: str = Field(
        default="",
        description=(
            "The output's `body_schema_uri`, informational only — never used "
            "as this record's own `body_schema_uri` (see module docstring)."
        ),
    )
    status: str = Field(
        ...,
        description="'failed' (its own call failed) or 'blocked' (an upstream one did).",
    )
    reason: str = Field(
        default="",
        description=(
            "Human-readable cause. Never raw exception text — see the "
            "module docstring's 'reason never carries raw exception text'."
        ),
    )
    error_type: Optional[str] = Field(
        default=None,
        description="The original exception's type name, when there was one.",
    )
    blocked_by: tuple[int, ...] = Field(
        default=(),
        description="Indices (within that run's plan) of the calls that blocked this one.",
    )


register_body_schema(UNPRODUCED_OUTPUT_BODY_SCHEMA_URI, UnproducedOutputBodyV1)
