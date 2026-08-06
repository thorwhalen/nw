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
from .graph_backend import (
    SCOPE_ALIGNMENT,
    SCOPE_GRAPH,
    SCOPE_STORYBOARD,
    iter_scope_stores,
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
    def _open(self) -> Iterator[IntervalAnnotationStore]:
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
        """Insert-or-update the section with this ``section_id``; return its id.

        The id is *stable* across edits — see :func:`_upsert`.
        """
        with self._open() as store:
            return _upsert(
                store,
                tier=_TIER_SECTION,
                schema_uri=SECTION_BODY_SCHEMA_URI,
                body=body,
                interval=interval,
                asset_id=self.asset_id,
                was_attributed_to=was_attributed_to,
                identity_key="section_id",
                identity_value=body.section_id,
            )

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
        """Insert-or-update the shot with this ``shot_id``; return its id.

        The id is *stable* across edits — see :func:`_upsert`.
        """
        with self._open() as store:
            return _upsert(
                store,
                tier=_TIER_SHOT,
                schema_uri=SHOT_BODY_SCHEMA_URI,
                body=body,
                interval=interval,
                asset_id=self.asset_id,
                was_attributed_to=was_attributed_to,
                identity_key="shot_id",
                identity_value=body.shot_id,
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
        """Insert-or-update the character ref with this ``name``; return its id.

        The id is *stable* across edits — see :func:`_upsert`.
        """
        with self._open() as store:
            return _upsert(
                store,
                tier=_TIER_CHARACTER_REF,
                schema_uri=CHARACTER_REF_BODY_SCHEMA_URI,
                body=body,
                interval=TimeInterval.from_seconds(0, 0),
                asset_id=self.asset_id,
                was_attributed_to=was_attributed_to,
                identity_key="name",
                identity_value=body.name,
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
        """Insert-or-update the environment ref with this ``name``; return its id.

        The id is *stable* across edits — see :func:`_upsert`.
        """
        with self._open() as store:
            return _upsert(
                store,
                tier=_TIER_ENVIRONMENT_REF,
                schema_uri=ENVIRONMENT_REF_BODY_SCHEMA_URI,
                body=body,
                interval=TimeInterval.from_seconds(0, 0),
                asset_id=self.asset_id,
                was_attributed_to=was_attributed_to,
                identity_key="name",
                identity_value=body.name,
            )

    # -- decisions -----------------------------------------------------------

    def decisions(self) -> list[StoredDecision]:
        with self._open() as store:
            return _collect_typed(
                store,
                tier=_TIER_DECISION,
                schema_uri=DECISION_BODY_SCHEMA_URI,
                model=DecisionBodyV1,
                wrap=lambda ann, body: StoredDecision(annotation_id=ann.id, body=body),
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


def _scope_paths(project_root: str | Path) -> dict[str, Path]:
    """The ``{scope_name: legacy_sqlite_path}`` map for a project's stores.

    The legacy per-scope SQLite layout (graph + storyboard + alignment). The
    backend seam (:mod:`nw.graph_backend`) uses the scope names to address the
    same stores in Postgres mode.
    """
    p = Path(project_root)
    return {
        SCOPE_GRAPH: p / _PROJECT_GRAPH_DB_NAME,
        SCOPE_STORYBOARD: p / "storyboard.annot.sqlite",
        SCOPE_ALIGNMENT: p / "lyrics" / "alignment.annot",
    }


def all_project_stores(project_root: str | Path) -> list[Path]:
    """Return the **existing** lacing-store file paths under a project.

    SQLite-mode only — these are filesystem paths. Code that walks or mutates
    project stores should route through :func:`open_project_stores` (which
    honours the backend seam) rather than opening these paths directly, so it
    keeps working when the backend is Postgres.
    """
    return [p for p in _scope_paths(project_root).values() if p.exists()]


@contextmanager
def open_project_stores(
    project_root: str | Path,
) -> Iterator[Iterator[IntervalAnnotationStore]]:
    """Yield an iterator of open stores, one per scope, honouring the backend.

    The backend-aware replacement for ``for p in all_project_stores(...):
    SqliteStore(p)``. Under SQLite it visits each existing per-scope file;
    under Postgres it visits each scope's tenant in the shared DB. Use it for
    both reads (walk ``.all()``) and writes (``.remove`` / ``.add``).

    Each store is closed before the next opens, so consume each store's
    annotations before advancing.
    """
    asset_id = project_asset_id(Path(project_root))
    with iter_scope_stores(_scope_paths(project_root), asset_id=asset_id) as stores:
        yield stores


def iter_all_annotations(project_root: str | Path) -> Iterator[Annotation]:
    """Walk every annotation in every store under a project (any backend)."""
    with open_project_stores(project_root) as stores:
        for store in stores:
            yield from store.all()


def descendants_of(project_root: str | Path, ancestor_id: UUID) -> list[Annotation]:
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


def derived_from(project_root: str | Path, annotation_id: UUID) -> list[Annotation]:
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


def stale_after(project_root: str | Path, changed_id: UUID) -> list[Annotation]:
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


def annotations_at_tier(project_root: str | Path, tier: str) -> list[Annotation]:
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


def _upsert(
    store: IntervalAnnotationStore,
    *,
    tier: str,
    schema_uri: str,
    body,
    interval: TimeInterval,
    asset_id: str,
    was_attributed_to: str,
    identity_key: str,
    identity_value,
) -> UUID:
    """Insert-or-update one *entity* annotation, preserving its identity.

    An entity (a shot, a section, a character/environment ref) is identified
    by a natural key in its body — ``shot_id``, ``section_id``, ``name``.
    The annotation id is that entity's identity **in the provenance graph**,
    so it must survive an edit:

    - Minting a fresh ``uuid4`` on every edit orphans every
      ``was_derived_from`` edge pointing at the entity. Because
      :func:`descendants_of` walks those edges, the freshness walk then
      returns *nothing* for the single most common edit ("change the
      costume, re-render everything showing them") — under-reporting to
      zero, silently. This is the bug that made every freshness-derived
      number in :meth:`nw.Project.resumption_brief` structurally dead.
    - ``write_spec`` re-upserts every entity on *any* spec write, so under
      the old behaviour even ``set_title()`` was enough to sever the whole
      graph.

    An edit that changes nothing writes nothing, so ``generated_at_time``
    marks a *real* change and "the last thing the user edited" is a
    meaningful question.
    """
    # ``mode="json"`` is what round-trips through the store: a tuple field
    # dumps to a tuple in python mode but reads back as a list, so comparing
    # python-mode dumps would report every no-op edit as a change.
    body_dict = (
        body.model_dump(mode="json") if hasattr(body, "model_dump") else dict(body)
    )
    existing = next(
        (
            ann
            for ann in store.all()
            if ann.tier == tier
            and isinstance(ann.body, dict)
            and ann.body.get(identity_key) == identity_value
        ),
        None,
    )
    if existing is not None:
        if existing.body == body_dict and existing.reference.interval == interval:
            return existing.id  # no-op edit: do not touch generated_at_time
        store.remove(existing.id)
    return _put(
        store,
        tier=tier,
        schema_uri=schema_uri,
        body=body_dict,
        interval=interval,
        asset_id=asset_id,
        was_attributed_to=was_attributed_to,
        annotation_id=existing.id if existing is not None else None,
    )


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
    annotation_id: Optional[UUID] = None,
) -> UUID:
    """Insert one annotation; return its UUID.

    ``annotation_id`` reuses an existing id (see :func:`_upsert`); omit it to
    mint a fresh one.
    """
    import uuid as _uuid

    new_id = annotation_id if annotation_id is not None else _uuid.uuid4()
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
