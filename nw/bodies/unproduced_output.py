"""Body schema for unproduced-output records — nw#44.

URI: ``annot://schema/unproduced-output/v1``

``TransformResult.failed`` / ``.blocked`` (nw#25) carry a reason for a
planned output that was never produced, but only within the response that
produced them — reload the project and the hole is unexplained again. This
schema is the persisted record of that reason, written through
:meth:`nw.graph.ProjectGraph.add_unproduced_output` at the same choke point
:meth:`~nw.transforms.BaseTransform.execute` writes successes through.

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

Lifecycle
---------
Keyed on ``(transform_name, frozenset(upstream))`` — the same pair a retry's
skeleton is built from (:func:`nw.transforms._provenance.derive_provenance`
sets ``was_derived_from`` to the same parent ids every time, for the same
inputs). :meth:`~nw.graph.ProjectGraph.add_annotation` retires any record
matching a real output annotation's ``(transform_name, upstream)`` pair the
moment that annotation is written — a successful retry removes its own
tombstone. A retry that fails again simply gets a fresh record (the retiring
write never happened).

Two properties this schema deliberately shares with the verifying trace
--------------------------------------------------------------------------
1. **Parentless.** ``was_derived_from`` is empty; the link to what it
   describes runs through the body's ``transform_name`` + ``upstream``
   instead. Wiring it as a provenance edge would put every record into its
   subject's ``descendants_of`` set, and — worse — :mod:`nw.freshness` would
   read matching digests as :data:`~nw.freshness.REASON_FRESH`, reporting a
   hole as verified-fresh.
2. **Excluded from freshness by construction.** Because it is parentless it
   never appears in any ``descendants_of`` walk, so ``stale_verdicts_all`` /
   ``/api/freshness`` are untouched and no lacing migration is owed.
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
    (an upstream call in the same plan failed first). ``upstream`` is the
    retirement key's other half — the output annotation's ``was_derived_from``
    parents, as strings (JSON has no UUID type), in insertion order with
    duplicates collapsed.
    """

    model_config = {"frozen": True, "extra": "forbid"}

    transform_name: str = Field(
        ..., description="The Transform whose planned output this describes."
    )
    upstream: tuple[str, ...] = Field(
        default=(),
        description=(
            "UUIDs (as strings) of the output's provenance parents — half of "
            "the retirement key, matched against a later successful output's "
            "own `was_derived_from`."
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
        default="", description="Human-readable cause, from nw.transforms.FailedOutput."
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
