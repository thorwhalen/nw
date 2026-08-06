"""Body schema for verifying traces — what makes early cutoff possible.

URI: ``annot://schema/verifying-trace/v1``

A **verifying trace** records, for one derived annotation, the *content
digest* each of its provenance parents had at the moment it was written::

    output annotation  X   was_derived_from = [A, B]
    verifying trace    T   for_annotation_id = X
                           upstream = [(A, sha256…), (B, sha256…)]

``provenance.was_derived_from`` alone says only *which* annotations X came
from. That makes freshness a reachability question — "A changed, so
everything reachable from A is suspect" — which is the *Make* cell of the
`Build Systems à la Carte` taxonomy and cannot cut off early. The digests
turn it into a **verifying-trace rebuilder** (Ninja / Shake / Salsa): when
A's current value digest still equals the one X recorded, X is provably
unaffected and the walk stops there. See :mod:`nw.freshness` for the query
side.

Why a sidecar annotation rather than a field on ``lacing.Provenance``
---------------------------------------------------------------------
:class:`lacing.Provenance` is ``frozen`` / ``extra="forbid"`` and the
envelope has no migration ladder — ``lacing.schema.register_migration``
migrates **bodies**, keyed by ``body_schema_uri``, and the persisted store
refuses to open at a different ``SCHEMA_VERSION``. Adding a field there is a
real on-disk migration against live project data.

lacing stores ``body`` as free-form JSON validated by ``body_schema_uri``,
and ``register_body_schema`` is public API nw already calls six times, so a
new *body* type costs nothing. :mod:`nw.bodies.decision` is the precedent —
a timeless, project-local, typed provenance record stored under a sentinel
zero-duration reference. This is the same shape with a typed payload.
(thorwhalen/reelee#253 decision D6.)

Two properties this schema deliberately has
--------------------------------------------
1. **A trace is not a descendant of what it describes.** Its
   ``was_derived_from`` is empty and the link runs through the body's
   ``for_annotation_id`` instead. Wiring it as a provenance edge would put
   every trace into its target's ``descendants_of`` set — i.e. bookkeeping
   would show up in the user-facing freshness answer.
2. **A missing trace means "stale", never "fresh".** :func:`build_verifying_trace`
   returns ``None`` rather than a partial record when a parent cannot be
   resolved, and :mod:`nw.freshness` treats an annotation with no usable
   trace exactly as today's reachability walk does. That is what makes this
   change need no migration: every pre-existing annotation keeps its current
   behaviour.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Iterable, Optional, Sequence
from uuid import UUID

from pydantic import BaseModel, Field

from lacing import Annotation, MediaRef, Provenance, RationalTime, TimeInterval
from lacing.digest import VALUE_DIGEST_SCHEME, annotation_value_digest
from lacing.schema import register_body_schema


VERIFYING_TRACE_BODY_SCHEMA_URI = "annot://schema/verifying-trace/v1"
VERIFYING_TRACE_TIER = "verifying-trace"


class UpstreamDigestV1(BaseModel):
    """One ``(upstream annotation, its value digest)`` pair."""

    model_config = {"frozen": True, "extra": "forbid"}

    annotation_id: str = Field(
        ..., description="UUID (as a string) of the upstream annotation."
    )
    value_digest: str = Field(
        ...,
        description=(
            "``lacing.annotation_value_digest`` of that annotation as it was "
            "when the described output was written."
        ),
    )


class VerifyingTraceBodyV1(BaseModel):
    """Body of a verifying-trace annotation.

    ``digest_scheme`` is recorded rather than assumed: lacing documents that
    changing ``VALUE_FIELDS`` or the canonicalisation is a breaking
    cache-invalidation event and bumps the scheme string. A trace written
    under an older scheme is not comparable, so :mod:`nw.freshness` treats
    the mismatch as *unverifiable* (therefore stale) instead of comparing
    digests that mean different things.
    """

    model_config = {"frozen": True, "extra": "forbid"}

    for_annotation_id: str = Field(
        ..., description="UUID (as a string) of the annotation this trace describes."
    )
    digest_scheme: str = Field(
        ...,
        description=(
            "The ``lacing`` digest scheme the ``upstream`` digests were "
            "computed under (``lacing.digest.VALUE_DIGEST_SCHEME``)."
        ),
    )
    upstream: tuple[UpstreamDigestV1, ...] = Field(
        default=(),
        description=(
            "One entry per distinct provenance parent, in ``was_derived_from`` order."
        ),
    )


register_body_schema(VERIFYING_TRACE_BODY_SCHEMA_URI, VerifyingTraceBodyV1)


def build_verifying_trace(
    *,
    for_annotation_id: UUID,
    parent_ids: Iterable[UUID],
    upstream: Sequence[Annotation],
    asset_id: str,
) -> Optional[Annotation]:
    """Build the trace annotation for one derived annotation, or ``None``.

    Args:
        for_annotation_id: Id of the annotation being described.
        parent_ids: Its ``provenance.was_derived_from``. Duplicates are
            collapsed, order preserved.
        upstream: The resolved parent annotations. **Must cover every id in
            ``parent_ids``** — a trace that omits a parent would let that
            parent change unnoticed.
        asset_id: The project's asset id, for the sentinel reference.

    Returns:
        The trace annotation, or ``None`` when there is nothing to verify
        (no parents) or the trace would be incomplete (a parent could not be
        resolved, or its value could not be digested). ``None`` is the safe
        answer in both cases: :mod:`nw.freshness` reads *no trace* as
        *unverifiable*, so the annotation keeps today's conservative
        reachability behaviour instead of being silently declared fresh.
    """
    wanted = tuple(dict.fromkeys(parent_ids))
    if not wanted:
        return None

    by_id = {a.id: a for a in upstream}
    entries: list[UpstreamDigestV1] = []
    for pid in wanted:
        parent = by_id.get(pid)
        if parent is None:
            return None
        try:
            digest = annotation_value_digest(parent)
        except Exception:
            # A body that cannot be digested (e.g. lacing's
            # NonStringBodyKeyError on a non-JSON key) is broken data at the
            # producer. Recording nothing keeps the consumer conservative;
            # recording a partial trace would not.
            return None
        entries.append(UpstreamDigestV1(annotation_id=str(pid), value_digest=digest))

    body = VerifyingTraceBodyV1(
        for_annotation_id=str(for_annotation_id),
        digest_scheme=VALUE_DIGEST_SCHEME,
        upstream=tuple(entries),
    )
    return Annotation(
        id=_uuid.uuid4(),
        tier=VERIFYING_TRACE_TIER,
        reference=MediaRef(asset_id=asset_id, interval=TimeInterval.from_seconds(0, 0)),
        body=body.model_dump(mode="json"),
        body_schema_uri=VERIFYING_TRACE_BODY_SCHEMA_URI,
        provenance=Provenance(
            was_generated_by="agent:nw.freshness",
            was_attributed_to="agent:nw.freshness",
            # Deliberately empty — see the module docstring, property 1.
            was_derived_from=[],
            generated_at_time=RationalTime.now(),
            activity="record",
        ),
    )
