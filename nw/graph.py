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
:func:`derived_from` and :func:`descendants_of` walk the
``provenance.was_derived_from`` edges across **all** stores in a project
(project graph + storyboard + alignment). Those are *reachability* queries.
The freshness query that compares content — ``nw.stale_after`` — lives in
:mod:`nw.freshness`; this module is where its input, the verifying trace, is
written (see :meth:`ProjectGraph.add_annotation`).
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional
from uuid import UUID, uuid4

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
    GENRE_ENVELOPE_BODY_SCHEMA_URI,
    GENRE_ENVELOPE_TIER,
    GenreEnvelopeBodyV1,
    SECTION_BODY_SCHEMA_URI,
    SHOT_BODY_SCHEMA_URI,
    VERIFYING_TRACE_BODY_SCHEMA_URI,
    VERIFYING_TRACE_TIER,
    CharacterRefBodyV1,
    DecisionBodyV1,
    EnvironmentRefBodyV1,
    SectionBodyV1,
    ShotBodyV1,
    build_verifying_trace,
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
        new_id = uuid4()
        # Resolve upstream digests *before* opening the store — the lookup
        # walks every scope and must not nest inside this store's context.
        trace = self._verifying_trace_for(new_id, was_derived_from)
        with self._open() as store:
            _put(
                store,
                tier=_TIER_DECISION,
                schema_uri=DECISION_BODY_SCHEMA_URI,
                body=body,
                interval=TimeInterval.from_seconds(0, 0),
                asset_id=self.asset_id,
                was_attributed_to=was_attributed_to,
                was_derived_from=was_derived_from,
                annotation_id=new_id,
            )
            _add_trace(store, trace)
        return new_id

    # -- genre envelope (singleton) ------------------------------------------

    def set_genre_envelope(
        self,
        body: GenreEnvelopeBodyV1,
        *,
        was_attributed_to: str = "agent:nw.genres",
    ) -> UUID:
        """Record the resolved ``{genre, template, params}`` envelope; return its id.

        Singleton per project (nw#32): the tier is the identity, so
        re-initializing replaces the recorded envelope in place — the
        annotation id is stable across replacements, like every entity
        upsert. A no-op write (same envelope) writes nothing.
        """
        from lacing import Tier, TierStereotype

        with self._open() as store:
            store.add_tier(
                Tier(name=GENRE_ENVELOPE_TIER, stereotype=TierStereotype.NONE)
            )
            return _upsert(
                store,
                tier=GENRE_ENVELOPE_TIER,
                schema_uri=GENRE_ENVELOPE_BODY_SCHEMA_URI,
                body=body,
                interval=TimeInterval.from_seconds(0, 0),
                asset_id=self.asset_id,
                was_attributed_to=was_attributed_to,
            )

    def genre_envelope(self) -> Optional[GenreEnvelopeBodyV1]:
        """The recorded genre envelope, or ``None`` for a genre-less project.

        The read half of :meth:`set_genre_envelope`; consumers should reach
        it through :meth:`nw.Project.resolved_genre`, which returns the
        plain-dict envelope shape :func:`nw.genres.resolve_genre` produces.
        """
        with self._open() as store:
            for ann in store.all():
                if (
                    ann.tier == GENRE_ENVELOPE_TIER
                    and ann.body_schema_uri == GENRE_ENVELOPE_BODY_SCHEMA_URI
                ):
                    try:
                        return GenreEnvelopeBodyV1.model_validate(ann.body)
                    except Exception:
                        continue
        return None

    # -- arbitrary annotations (for things that don't fit a typed bucket) ----

    def add_annotation(self, ann: Annotation) -> None:
        """Write one annotation to the project graph, plus its verifying trace.

        Registers ``ann.tier`` if it isn't a known tier yet — ``SqliteStore``
        enforces a foreign key on ``tier``, so writing under a fresh tier
        (e.g. a Transform output kind) would otherwise fail. ``add_tier`` is
        idempotent, so this is a no-op for the built-in project tiers.

        This is the single choke point every *derived* annotation in nw and
        reelee passes through, which is why the verifying trace
        (:mod:`nw.bodies.verifying_trace`) is recorded here rather than in
        ``derive_provenance``: that helper returns a ``Provenance`` and has
        no store to write to, and threading one in would change a signature
        with production callsites in three repos. Writing at persist time
        also covers the paths that build a ``Provenance`` by hand.

        Annotations with no ``was_derived_from`` parents get no trace — there
        is nothing to verify, and they are nobody's descendant.
        """
        from lacing import Tier, TierStereotype

        trace = self._verifying_trace_for(ann.id, ann.provenance.was_derived_from)
        with self._open() as store:
            store.add_tier(Tier(name=ann.tier, stereotype=TierStereotype.NONE))
            store.add(ann)
            _add_trace(store, trace)

    def remove_annotation(self, annotation_id: UUID) -> bool:
        """Remove one annotation from the project graph, with its verifying traces.

        The delete counterpart of :meth:`add_annotation` (nw#36): every trace
        whose ``for_annotation_id`` names ``annotation_id`` goes with it, so a
        deletion never leaves a sidecar behind — an orphaned trace is inert
        for freshness but grows the store without bound, and if the id is
        later re-used it can even answer a freshness query from digests
        recorded for content that is no longer there.

        Only the project graph store is touched — the store
        :meth:`add_annotation` writes to. Annotations living in the other
        scopes (storyboard, alignment) are removed by their own facades;
        :func:`collect_orphan_traces` is the project-wide backstop.

        Returns:
            Whether ``annotation_id`` itself was present (its traces are
            removed either way).
        """
        with self._open() as store:
            removed = remove_annotations_with_traces(store, (annotation_id,))
        return annotation_id in removed

    # -- verifying traces ----------------------------------------------------

    def _verifying_trace_for(
        self, annotation_id: UUID, parent_ids: Iterable[UUID]
    ) -> Optional[Annotation]:
        """Build the verifying trace for a derived annotation, or ``None``.

        ``None`` whenever the trace would be incomplete — no parents, or a
        parent that cannot be resolved. :mod:`nw.freshness` reads a missing
        trace as *unverifiable*, i.e. stale, so an incomplete record is never
        preferable to none.
        """
        wanted = tuple(dict.fromkeys(parent_ids))
        if not wanted:
            return None
        return build_verifying_trace(
            for_annotation_id=annotation_id,
            parent_ids=wanted,
            upstream=self._resolve_annotations(wanted),
            asset_id=self.asset_id,
        )

    def _resolve_annotations(self, ids: tuple[UUID, ...]) -> list[Annotation]:
        """Fetch ``ids`` from the project's stores, stopping once all are found.

        lacing's store protocol has no point lookup by annotation id (its
        ``__getitem__`` is keyed by :class:`~lacing.TimeInterval`), so this is
        a scan. It runs once per *derived* write and short-circuits on the
        last hit; entity upserts and other parentless writes never reach it.
        """
        wanted = set(ids)
        found: list[Annotation] = []
        for ann in iter_all_annotations(self.project_root):
            if ann.id in wanted:
                found.append(ann)
                wanted.discard(ann.id)
                if not wanted:
                    break
        return found


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


def annotations_at_tier(project_root: str | Path, tier: str) -> list[Annotation]:
    """Return every annotation at the given tier across all of the project's stores.

    Useful for reelee views that lens on a single annotation kind:
    ``annotations_at_tier(root, "shot")`` returns every shot annotation
    regardless of which store it lives in (project graph vs. storyboard
    vs. alignment).
    """
    return [ann for ann in iter_all_annotations(project_root) if ann.tier == tier]


# ---------------------------------------------------------------------------
# Deletion — an annotation travels with its verifying traces (nw#36)
# ---------------------------------------------------------------------------


def remove_annotations_with_traces(
    store: IntervalAnnotationStore,
    annotation_ids: Iterable[UUID],
    *,
    annotations: Optional[list[Annotation]] = None,
) -> set[UUID]:
    """Remove annotations from ``store``, plus every trace in it naming them.

    The store-level primitive behind every nw deletion path
    (:meth:`ProjectGraph.remove_annotation`, ``write_spec``'s entity
    reconciliation, the storyboard wipe). A verifying trace is a sidecar of
    the annotation it describes; removing one without the other leaks an
    inert row per deletion, forever (nw#36).

    Args:
        store: An **open** store — the caller owns its lifecycle.
        annotation_ids: Ids to remove. Missing ids are ignored.
        annotations: The store's annotations, if the caller already
            materialized ``list(store.all())`` — avoids a second scan.

    Returns:
        The subset of ``annotation_ids`` that was actually present. Trace
        removals are not reported: they are bookkeeping, not content.
    """
    wanted = set(annotation_ids)
    if not wanted:
        return set()
    if annotations is None:
        annotations = list(store.all())
    removed: set[UUID] = set()
    trace_ids: list[UUID] = []
    for ann in annotations:
        if ann.id in wanted:
            removed.add(ann.id)
        else:
            target = _trace_target(ann)
            if target is not None and target in wanted:
                trace_ids.append(ann.id)
    for annotation_id in (*removed, *trace_ids):
        store.remove(annotation_id)
    return removed


def collect_orphan_traces(project_root: str | Path) -> list[UUID]:
    """Drop verifying traces whose target annotation no longer exists.

    The backstop for deletion paths that do not (or cannot) go through
    :func:`remove_annotations_with_traces` — a direct ``store.remove``, an
    external tool, history from before deletions collected traces (nw#36).
    Walks every store under the project; a trace is an orphan when its
    ``for_annotation_id`` resolves in **none** of them. Idempotent, and safe
    to run as routine maintenance: an orphaned trace is never consulted by
    :mod:`nw.freshness`, so removing it changes no freshness answer.

    A trace whose body cannot be read (not a dict, unparseable target id) is
    left in place: it may be an orphan, but deleting what we cannot identify
    is worse than carrying it.

    Returns:
        The ids of the trace annotations removed, in store order.
    """
    existing = {ann.id for ann in iter_all_annotations(project_root)}
    removed: list[UUID] = []
    with open_project_stores(project_root) as stores:
        for store in stores:
            orphans = [
                ann.id
                for ann in list(store.all())
                if (target := _trace_target(ann)) is not None
                and target not in existing
            ]
            for annotation_id in orphans:
                store.remove(annotation_id)
            removed.extend(orphans)
    return removed


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _trace_target(ann: Annotation) -> Optional[UUID]:
    """The annotation id a verifying trace describes, or ``None``.

    ``None`` for anything that is not a readable verifying trace — the same
    "when in doubt, leave it alone" reading :func:`nw.freshness` applies on
    the query side, applied to deletion.
    """
    if ann.body_schema_uri != VERIFYING_TRACE_BODY_SCHEMA_URI:
        return None
    if not isinstance(ann.body, dict):
        return None
    try:
        return UUID(str(ann.body.get("for_annotation_id")))
    except (ValueError, TypeError):
        return None


def _add_trace(store: IntervalAnnotationStore, trace: Optional[Annotation]) -> None:
    """Write a verifying trace (registering its tier), or do nothing for ``None``."""
    if trace is None:
        return
    from lacing import Tier, TierStereotype

    store.add_tier(Tier(name=VERIFYING_TRACE_TIER, stereotype=TierStereotype.NONE))
    store.add(trace)


def _upsert(
    store: IntervalAnnotationStore,
    *,
    tier: str,
    schema_uri: str,
    body,
    interval: TimeInterval,
    asset_id: str,
    was_attributed_to: str,
    identity_key: Optional[str] = None,
    identity_value=None,
) -> UUID:
    """Insert-or-update one *entity* annotation, preserving its identity.

    An entity (a shot, a section, a character/environment ref) is identified
    by a natural key in its body — ``shot_id``, ``section_id``, ``name``.
    ``identity_key=None`` means the **tier itself** is the identity: at most
    one annotation lives at that tier (e.g. the genre envelope), and an
    upsert replaces it whatever its body says.
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
            and (identity_key is None or ann.body.get(identity_key) == identity_value)
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
    new_id = annotation_id if annotation_id is not None else uuid4()
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
