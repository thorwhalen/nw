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
    transform_name: str,
    transform_version: str | int,
    inputs: TransformInputs,
    *,
    activity: str = "derive",
    attributed_to: str | None = None,
) -> Provenance:
    """Build the :class:`lacing.Provenance` for a Transform-produced annotation.

    Args:
        transform_name: The Transform's ``name`` (e.g. ``"beat_to_panel.llm.default"``).
        transform_version: A version stamp — a prompt version for LLM
            Transforms, a model id, or the package version for deterministic
            ones. Recorded in ``was_generated_by`` so a replayed plan is
            traceable to exactly what produced it.
        inputs: The :class:`TransformInputs` the Transform consumed.
            ``was_derived_from`` is set to the ids of every annotation in
            ``primary`` plus every annotation in every ``context`` group.
        activity: PROV-O activity — almost always ``"derive"`` for a Transform.
        attributed_to: Who is responsible. Defaults to
            ``f"agent:{transform_name}"`` (a deterministic transformer);
            LLM-backed Transforms should pass ``"agent:claude-<model>@<hash>"``
            and human-in-the-loop ones ``"user:<handle>"``.

    Returns:
        A :class:`lacing.Provenance` ready to attach to a skeleton annotation.
    """
    parent_ids: list[UUID] = [a.id for a in inputs.primary]
    for group in inputs.context.values():
        parent_ids.extend(a.id for a in group)
    return Provenance(
        was_generated_by=f"transform:{transform_name}@{transform_version}",
        was_attributed_to=attributed_to or f"agent:{transform_name}",
        was_derived_from=parent_ids,
        generated_at_time=RationalTime.now(),
        activity=activity,
    )
