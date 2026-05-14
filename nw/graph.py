"""The project annotation graph — read/write helpers + reelee-style traversals.

A nw project's SSOT for sections, shots, character/environment refs, and
decisions is a per-project lacing :class:`SqliteStore` at
``project.annot.sqlite``. This module wraps that store with typed helpers
so the rest of nw doesn't need to know about tier names, MediaRef
construction, or annotation envelopes.

Usage from inside the package:

    >>> from nw.graph import ProjectGraph
    >>> g = ProjectGraph(project_root)
    >>> g.upsert_shot_body(shot_body, interval=TimeInterval.from_seconds(0, 8))
    >>> for shot in g.shots():
    ...     ...

For reelee's freshness analysis (planned in §7 of the system overview),
:func:`derived_from`, :func:`descendants_of`, and :func:`stale_after`
walk the ``provenance.was_derived_from`` edges across **all** stores in
a project (project graph + storyboard + alignment).
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional
from uuid import UUID

from lacing import (
    Annotation,
    IntervalAnnotationStore,
    MediaRef,
    Provenance,
    SqliteStore,
    TimeInterval,
)
from lacing.artifact import _now_rt

from .bodies import (
    CHARACTER_REF_BODY_SCHEMA_URI,
    DECISION_BODY_SCHEMA_URI,
    ENVIRONMENT_REF_BODY_SCHEMA_URI,
    SECTION_BODY_SCHEMA_URI,
    SHOT_BODY_SCHEMA_URI,
    CharacterRefBodyV1,
    DecisionBodyV1,
    EnvironmentRefBodyV1,
    SectionBodyV1,
    ShotBodyV1,
)
from .migrate import (
    _PROJECT_GRAPH_DB_NAME,
    _TIER_CHARACTER_REF,
    _TIER_DECISION,
    _TIER_ENVIRONMENT_REF,
    _TIER_SECTION,
    _TIER_SHOT,
    open_project_graph,
    project_asset_id,
)


# ---------------------------------------------------------------------------
# Typed views over annotations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredSection:
    annotation_id: UUID
    interval: TimeInterval
    body: SectionBodyV1


@dataclass(frozen=True)
class StoredShot:
    annotation_id: UUID
    interval: TimeInterval
    body: ShotBodyV1


@dataclass(frozen=True)
class StoredCharacterRef:
    annotation_id: UUID
    body: CharacterRefBodyV1


@dataclass(frozen=True)
class StoredEnvironmentRef:
    annotation_id: UUID
    body: EnvironmentRefBodyV1


@dataclass(frozen=True)
class StoredDecision:
    annotation_id: UUID
    body: DecisionBodyV1


# ---------------------------------------------------------------------------
# ProjectGraph
# ---------------------------------------------------------------------------


class ProjectGraph:
    """Typed read/write facade over the project's lacing graph store.

    Use :meth:`Project.graph` to get one rather than constructing directly.
    Each method opens-and-closes the underlying store so concurrent
    reads/writes from different processes are safe (SqliteStore is
    file-locked).
    """

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.asset_id = project_asset_id(self.project_root)

    # -- lifecycle -----------------------------------------------------------

    @contextmanager
    def _open(self) -> Iterator[SqliteStore]:
        store = open_project_graph(self.project_root)
        try:
            yield store
        finally:
            close = getattr(store, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    # -- sections ------------------------------------------------------------

    def sections(self) -> list[StoredSection]:
        with self._open() as store:
            return _collect_typed(
                store,
                tier=_TIER_SECTION,
                schema_uri=SECTION_BODY_SCHEMA_URI,
                model=SectionBodyV1,
                wrap=lambda ann, body: StoredSection(
                    annotation_id=ann.id, interval=ann.reference.interval, body=body
                ),
                sort_key=lambda s: s.interval.start.to_seconds(),
            )

    def upsert_section(
        self,
        body: SectionBodyV1,
        interval: TimeInterval,
        *,
        was_attributed_to: str = "user:nw",
    ) -> UUID:
        """Replace any existing section with the same ``section_id``; return new uuid."""
        with self._open() as store:
            for ann in list(store.all()):
                if (
                    ann.tier == _TIER_SECTION
                    and isinstance(ann.body, dict)
                    and ann.body.get("section_id") == body.section_id
                ):
                    store.remove(ann.id)
            new_id = _put(
                store,
                tier=_TIER_SECTION,
                schema_uri=SECTION_BODY_SCHEMA_URI,
                body=body,
                interval=interval,
                asset_id=self.asset_id,
                was_attributed_to=was_attributed_to,
            )
            return new_id

    # -- shots ---------------------------------------------------------------

    def shots(self) -> list[StoredShot]:
        with self._open() as store:
            return _collect_typed(
                store,
                tier=_TIER_SHOT,
                schema_uri=SHOT_BODY_SCHEMA_URI,
                model=ShotBodyV1,
                wrap=lambda ann, body: StoredShot(
                    annotation_id=ann.id, interval=ann.reference.interval, body=body
                ),
                sort_key=lambda s: s.interval.start.to_seconds(),
            )

    def upsert_shot(
        self,
        body: ShotBodyV1,
        interval: TimeInterval,
        *,
        was_attributed_to: str = "user:nw",
    ) -> UUID:
        """Replace any existing shot with the same ``shot_id``; return new uuid."""
        with self._open() as store:
            for ann in list(store.all()):
                if (
                    ann.tier == _TIER_SHOT
                    and isinstance(ann.body, dict)
                    and ann.body.get("shot_id") == body.shot_id
                ):
                    store.remove(ann.id)
            return _put(
                store,
                tier=_TIER_SHOT,
                schema_uri=SHOT_BODY_SCHEMA_URI,
                body=body,
                interval=interval,
                asset_id=self.asset_id,
                was_attributed_to=was_attributed_to,
            )

    # -- character / environment refs ---------------------------------------

    def character_refs(self) -> list[StoredCharacterRef]:
        with self._open() as store:
            return _collect_typed(
                store,
                tier=_TIER_CHARACTER_REF,
                schema_uri=CHARACTER_REF_BODY_SCHEMA_URI,
                model=CharacterRefBodyV1,
                wrap=lambda ann, body: StoredCharacterRef(
                    annotation_id=ann.id, body=body
                ),
                sort_key=lambda s: s.body.name,
            )

    def upsert_character_ref(
        self,
        body: CharacterRefBodyV1,
        *,
        was_attributed_to: str = "user:nw",
    ) -> UUID:
        with self._open() as store:
            for ann in list(store.all()):
                if (
                    ann.tier == _TIER_CHARACTER_REF
                    and isinstance(ann.body, dict)
                    and ann.body.get("name") == body.name
                ):
                    store.remove(ann.id)
            return _put(
                store,
                tier=_TIER_CHARACTER_REF,
                schema_uri=CHARACTER_REF_BODY_SCHEMA_URI,
                body=body,
                interval=TimeInterval.from_seconds(0, 0),
                asset_id=self.asset_id,
                was_attributed_to=was_attributed_to,
            )

    def environment_refs(self) -> list[StoredEnvironmentRef]:
        with self._open() as store:
            return _collect_typed(
                store,
                tier=_TIER_ENVIRONMENT_REF,
                schema_uri=ENVIRONMENT_REF_BODY_SCHEMA_URI,
                model=EnvironmentRefBodyV1,
                wrap=lambda ann, body: StoredEnvironmentRef(
                    annotation_id=ann.id, body=body
                ),
                sort_key=lambda s: s.body.name,
            )

    def upsert_environment_ref(
        self,
        body: EnvironmentRefBodyV1,
        *,
        was_attributed_to: str = "user:nw",
    ) -> UUID:
        with self._open() as store:
            for ann in list(store.all()):
                if (
                    ann.tier == _TIER_ENVIRONMENT_REF
                    and isinstance(ann.body, dict)
                    and ann.body.get("name") == body.name
                ):
                    store.remove(ann.id)
            return _put(
                store,
                tier=_TIER_ENVIRONMENT_REF,
                schema_uri=ENVIRONMENT_REF_BODY_SCHEMA_URI,
                body=body,
                interval=TimeInterval.from_seconds(0, 0),
                asset_id=self.asset_id,
                was_attributed_to=was_attributed_to,
            )

    # -- decisions -----------------------------------------------------------

    def decisions(self) -> list[StoredDecision]:
        with self._open() as store:
            return _collect_typed(
                store,
                tier=_TIER_DECISION,
                schema_uri=DECISION_BODY_SCHEMA_URI,
                model=DecisionBodyV1,
                wrap=lambda ann, body: StoredDecision(
                    annotation_id=ann.id, body=body
                ),
                sort_key=lambda s: 0,  # decisions arrive in insertion order
            )

    def append_decision(
        self,
        body: DecisionBodyV1,
        *,
        was_attributed_to: str = "user:nw",
        was_derived_from: tuple[UUID, ...] = (),
    ) -> UUID:
        """Append a decision; never replaces an existing one (the log is append-only)."""
        with self._open() as store:
            return _put(
                store,
                tier=_TIER_DECISION,
                schema_uri=DECISION_BODY_SCHEMA_URI,
                body=body,
                interval=TimeInterval.from_seconds(0, 0),
                asset_id=self.asset_id,
                was_attributed_to=was_attributed_to,
                was_derived_from=was_derived_from,
            )

    # -- arbitrary annotations (for things that don't fit a typed bucket) ----

    def add_annotation(self, ann: Annotation) -> None:
        """Write one annotation to the project graph.

        Registers ``ann.tier`` if it isn't a known tier yet — ``SqliteStore``
        enforces a foreign key on ``tier``, so writing under a fresh tier
        (e.g. a Transform output kind) would otherwise fail. ``add_tier`` is
        idempotent, so this is a no-op for the built-in project tiers.
        """
        from lacing import Tier, TierStereotype

        with self._open() as store:
            store.add_tier(Tier(name=ann.tier, stereotype=TierStereotype.NONE))
            store.add(ann)


# ---------------------------------------------------------------------------
# Reelee-style traversals
# ---------------------------------------------------------------------------


def all_project_stores(project_root: str | Path) -> list[Path]:
    """Return all lacing-store paths under a project (graph + storyboard + alignment)."""
    p = Path(project_root)
    out: list[Path] = []
    candidates = [
        p / _PROJECT_GRAPH_DB_NAME,
        p / "storyboard.annot.sqlite",
        p / "lyrics" / "alignment.annot",
    ]
    for c in candidates:
        if c.exists():
            out.append(c)
    return out


def iter_all_annotations(project_root: str | Path) -> Iterator[Annotation]:
    """Walk every annotation in every store under a project."""
    for db in all_project_stores(project_root):
        store = SqliteStore(str(db))
        try:
            yield from store.all()
        finally:
            close = getattr(store, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def descendants_of(
    project_root: str | Path, ancestor_id: UUID
) -> list[Annotation]:
    """Return every annotation whose provenance chain leads back to ``ancestor_id``.

    Walks ``provenance.was_derived_from`` *transitively* across all of the
    project's lacing stores. This is the operation reelee's freshness
    analysis is built on (system overview §7): when a node changes, every
    annotation in the closure of this set is "downstream of the change."
    """
    # Collect once so we can do multi-hop closure without re-opening stores.
    all_anns = list(iter_all_annotations(project_root))
    by_id = {ann.id: ann for ann in all_anns}

    # Children index: parent_id -> [child_id, ...]
    children: dict[UUID, list[UUID]] = {}
    for ann in all_anns:
        for parent in ann.provenance.was_derived_from:
            children.setdefault(parent, []).append(ann.id)

    # BFS from ancestor_id.
    seen: set[UUID] = set()
    frontier = list(children.get(ancestor_id, ()))
    while frontier:
        cur = frontier.pop()
        if cur in seen:
            continue
        seen.add(cur)
        frontier.extend(children.get(cur, ()))

    return [by_id[i] for i in seen if i in by_id]


def derived_from(
    project_root: str | Path, annotation_id: UUID
) -> list[Annotation]:
    """Return the annotations this one was directly derived from.

    Walks ``provenance.was_derived_from`` *one hop only* across all of the
    project's stores.
    """
    all_anns = list(iter_all_annotations(project_root))
    by_id = {ann.id: ann for ann in all_anns}
    target = by_id.get(annotation_id)
    if target is None:
        return []
    return [by_id[i] for i in target.provenance.was_derived_from if i in by_id]


def stale_after(
    project_root: str | Path, changed_id: UUID
) -> list[Annotation]:
    """Return every annotation that is downstream of ``changed_id``.

    Reelee's freshness operation (system overview §7): when a node changes
    (a character description, a screenplay scene, a model parameter), the
    set of annotations now potentially out of date is the transitive closure
    of ``was_derived_from`` edges leading to ``changed_id``. This is just
    :func:`descendants_of` under a more user-facing name; both names are
    exposed because reelee's UI surfaces use both verbs.

    The returned list does NOT include ``changed_id`` itself (it's the source
    of the change, not a stale derivative).
    """
    return descendants_of(project_root, changed_id)


def annotations_at_tier(
    project_root: str | Path, tier: str
) -> list[Annotation]:
    """Return every annotation at the given tier across all of the project's stores.

    Useful for reelee views that lens on a single annotation kind:
    ``annotations_at_tier(root, "shot")`` returns every shot annotation
    regardless of which store it lives in (project graph vs. storyboard
    vs. alignment).
    """
    return [ann for ann in iter_all_annotations(project_root) if ann.tier == tier]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _put(
    store: IntervalAnnotationStore,
    *,
    tier: str,
    schema_uri: str,
    body,
    interval: TimeInterval,
    asset_id: str,
    was_attributed_to: str,
    was_derived_from: tuple[UUID, ...] = (),
) -> UUID:
    """Insert one annotation; return its UUID."""
    import uuid as _uuid
    new_id = _uuid.uuid4()
    ann = Annotation(
        id=new_id,
        tier=tier,
        reference=MediaRef(asset_id=asset_id, interval=interval),
        body=body.model_dump() if hasattr(body, "model_dump") else dict(body),
        body_schema_uri=schema_uri,
        provenance=Provenance(
            was_generated_by="agent:nw.graph",
            was_attributed_to=was_attributed_to,
            was_derived_from=list(was_derived_from),
            generated_at_time=_now_rt(),
            activity="create",
        ),
    )
    store.add(ann)
    return new_id


def _collect_typed(
    store: IntervalAnnotationStore,
    *,
    tier: str,
    schema_uri: str,
    model,
    wrap,
    sort_key,
) -> list:
    out = []
    for ann in store.all():
        if ann.tier != tier:
            continue
        if ann.body_schema_uri != schema_uri:
            continue
        try:
            body = model.model_validate(ann.body)
        except Exception:
            continue
        out.append(wrap(ann, body))
    return sorted(out, key=sort_key)
