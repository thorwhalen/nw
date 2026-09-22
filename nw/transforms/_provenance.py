"""Provenance construction for Transform outputs.

Every Transform output annotation needs a :class:`lacing.Provenance` whose
``was_derived_from`` is the union of all input annotation ids — that edge is
what ``nw.descendants_of`` / ``nw.stale_after`` walk for freshness analysis.
This helper centralizes that so every Transform doesn't reinvent it.

It also writes the **artifact tier** of that edge (nw#55): the asset ids the
inputs name, per each input's declared body schema
(:mod:`nw.transforms.asset_refs`), after the annotation ids. An input whose
schema is undeclared contributes none, so a producer that declares nothing gets
exactly the provenance it always got.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from lacing import Provenance, RationalTime

from . import Transform, TransformInputs
from .asset_refs import _ASSET_ID, AssetRefDeclarationError, asset_refs_of


def derive_provenance(
    transform: "Transform",
    inputs: TransformInputs,
    *,
    activity: str = "derive",
    attributed_to: str | None = None,
    asset_refs: Iterable[str] = (),
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
        asset_refs: Extra artifact ``asset_id`` values this output was derived
            from that no input body names — the escape hatch for a Transform
            that consumed an artifact directly. Merged after the declared ones,
            deduplicated. Each must be a bare 64-hex asset id. Unlike a declared
            ref, nothing ties one of these to an annotation's body, so freshness
            trusts it as-is: an asset id names immutable bytes, but nw does not
            check the artifact still exists or has been superseded.

    Returns:
        A :class:`lacing.Provenance` ready to attach to a skeleton annotation,
        with ``was_generated_by`` in the ``transform:<name>@<impl_version>``
        shape it has always had.
    """
    consumed = list(inputs.primary)
    for group in inputs.context.values():
        consumed.extend(group)
    parent_ids: list[UUID] = [a.id for a in consumed]
    assets: dict[str, None] = {}
    for ann in consumed:
        assets.update(dict.fromkeys(asset_refs_of(ann)))
    assets.update(dict.fromkeys(_checked_asset_refs(asset_refs)))
    return Provenance(
        was_generated_by=f"transform:{transform.name}@{transform.impl_version}",
        was_attributed_to=attributed_to or f"agent:{transform.name}",
        # Annotation ids first, unchanged; artifact ids after (nw#55).
        was_derived_from=[*parent_ids, *assets],
        generated_at_time=RationalTime.now(),
        activity=activity,
    )


def _checked_asset_refs(asset_refs: Iterable[str]) -> list[str]:
    """``asset_refs`` validated as bare 64-hex ids.

    A bare ``str`` is refused rather than iterated character by character, and a
    UUID string is refused rather than silently becoming an *annotation* parent
    through lacing's ``UUID | AssetId`` union.

    >>> _checked_asset_refs(["a" * 64])[0][:4]
    'aaaa'
    >>> _checked_asset_refs("a" * 64)
    Traceback (most recent call last):
    ...
    TypeError: asset_refs must be an iterable of asset ids, not a single str
    """
    if isinstance(asset_refs, str):
        raise TypeError("asset_refs must be an iterable of asset ids, not a single str")
    refs = list(asset_refs)
    for ref in refs:
        if not isinstance(ref, str) or not _ASSET_ID.fullmatch(ref):
            raise AssetRefDeclarationError(
                f"asset_refs: {ref!r} is not a bare 64-hex asset id"
            )
    return refs
