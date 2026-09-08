"""Test helpers for lacing's UNKNOWN generation-time sentinel (lacing#44).

Shared by the freshness, backfill and project suites so the one way to
manufacture a "row written through the REST path before lacing#35" lives
in one place.
"""

from __future__ import annotations

from uuid import UUID

import nw
from lacing import Annotation, RationalTime


def restamp_unknown(proj, ann: Annotation) -> Annotation:
    """Rewrite ``ann`` in place with ``generated_at_time`` at tick 0, raw.

    Raw ``store.add``, not ``add_annotation``: the value is unchanged, so any
    trace that names it (or that it recorded) still matches every digest.
    That is the point — the only thing that changes is that the row can no
    longer be placed in time.
    """
    updated = ann.model_copy(
        update={
            "provenance": ann.provenance.model_copy(
                update={"generated_at_time": RationalTime.zero()}
            )
        }
    )
    assert updated.provenance.generated_at_is_known is False
    with nw.open_project_stores(proj.root) as stores:
        for store in stores:
            if store.remove(ann.id) is not None:
                break
    with proj.graph._open() as store:
        store.add(updated)
    return updated


def by_id(proj) -> dict[UUID, Annotation]:
    return {a.id: a for a in nw.iter_all_annotations(proj.root)}
