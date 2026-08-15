"""Provenance construction for Transform outputs.

Every Transform output annotation needs a :class:`lacing.Provenance` whose
``was_derived_from`` is the union of all input annotation ids — that edge is
what ``nw.descendants_of`` / ``nw.stale_after`` walk for freshness analysis.
This helper centralizes that so every Transform doesn't reinvent it.
"""

from __future__ import annotations

from uuid import UUID

from lacing import Provenance, RationalTime

from . import TransformInputs


def derive_provenance(
    transform,
    inputs: TransformInputs,
    *,
    activity: str = "derive",
    attributed_to: str | None = None,
) -> Provenance:
    """Build the :class:`lacing.Provenance` for a Transform-produced annotation.

    Args:
        transform: The producing Transform — anything satisfying the
            :class:`~nw.transforms.Transform` Protocol. Its ``name`` and
            ``impl_version`` are read *off the instance* rather than passed
            as per-callsite strings, so the version recorded in provenance
            is the same one the cache identity is salted with — one value,
            two readers, no drift (nw#27).
        inputs: The :class:`TransformInputs` the Transform consumed.
            ``was_derived_from`` is set to the ids of every annotation in
            ``primary`` plus every annotation in every ``context`` group.
        activity: PROV-O activity — almost always ``"derive"`` for a Transform.
        attributed_to: Who is responsible. Defaults to
            ``f"agent:{transform.name}"`` (a deterministic transformer);
            LLM-backed Transforms should pass ``"agent:claude-<model>@<hash>"``
            and human-in-the-loop ones ``"user:<handle>"``.

    Returns:
        A :class:`lacing.Provenance` ready to attach to a skeleton annotation,
        with ``was_generated_by`` in the ``transform:<name>@<impl_version>``
        shape it has always had.
    """
    parent_ids: list[UUID] = [a.id for a in inputs.primary]
    for group in inputs.context.values():
        parent_ids.extend(a.id for a in group)
    return Provenance(
        was_generated_by=f"transform:{transform.name}@{transform.impl_version}",
        was_attributed_to=attributed_to or f"agent:{transform.name}",
        was_derived_from=parent_ids,
        generated_at_time=RationalTime.now(),
        activity=activity,
    )
