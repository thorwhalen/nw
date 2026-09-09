"""The project annotation graph — read/write helpers + reelee-style traversals.

A nw project's SSOT for sections, shots, character/environment refs, and
decisions is a per-project lacing :class:`SqliteStore` at
``project.annot.sqlite``. This module wraps that store with typed helpers
so the rest of nw doesn't need to know about tier names, MediaRef
construction, or annotation envelopes.

Usage from inside the package (illustrative — ``project_root``, ``shot_body``
and ``TimeInterval`` are the caller's fixtures, not defined here, so the
executable lines are skipped rather than exercised):

    >>> from nw.graph import ProjectGraph
    >>> g = ProjectGraph(project_root)  # doctest: +SKIP
    >>> g.upsert_shot_body(shot_body, interval=TimeInterval.from_seconds(0, 8))  # doctest: +SKIP
    >>> for shot in g.shots():  # doctest: +SKIP
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

import logging
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
    UNPRODUCED_OUTPUT_BODY_SCHEMA_URI,
    UNPRODUCED_OUTPUT_TIER,
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
    UnproducedOutputBodyV1,
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
    open_project_graph_readonly,
    project_asset_id,
)


_logger = logging.getLogger("nw.graph")


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


@dataclass(frozen=True)
class StoredUnproducedOutput:
    annotation_id: UUID
    body: UnproducedOutputBodyV1


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

    @contextmanager
    def _open_read(self) -> Iterator[Optional[IntervalAnnotationStore]]:
        """Open for a READ, without taking a write lock. May yield ``None``.

        This class's docstring already promises that "concurrent reads/writes
        from different processes are safe (SqliteStore is file-locked)".
        :func:`open_project_graph` broke that promise for readers: it calls
        ``_ensure_tiers``, which writes on every open, so a read contended with
        any live writer. Measured with a writer holding ``BEGIN IMMEDIATE``, a
        read of one project's genre envelope took **5.4 s** and then raised —
        and because callers of ``resolved_genre`` catch per project, what a
        user saw was a listing that stalled for five seconds per contended
        project and then quietly lost that project's metadata.

        Two fallbacks, each preserving a guarantee the read-write open makes:

        - **No graph on disk yields ``None``**, and the caller returns its
          empty answer. A project with no store has no sections; creating one
          to discover that is the write this exists to avoid.
        - **A file needing migration falls back to the read-write open.**
          ``open_project_graph`` passes ``migrate=True`` deliberately —
          without it every pre-D5 project refuses to open — and dropping that
          for reads would strand those projects until something wrote to them.
          So the rare case pays the lock and the common case does not.
        """
        from lacing.store.sqlite import SchemaMismatchError

        try:
            store = open_project_graph_readonly(self.project_root)
        except FileNotFoundError:
            yield None
            return
        except SchemaMismatchError:
            with self._open() as store:
                yield store
            return
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
        with self._open_read() as store:
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
        with self._open_read() as store:
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
        with self._open_read() as store:
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
        with self._open_read() as store:
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
        with self._open_read() as store:
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
        with self._open_read() as store:
            if store is None:
                return None
            for ann in store.by_tier(GENRE_ENVELOPE_TIER):
                if ann.body_schema_uri == GENRE_ENVELOPE_BODY_SCHEMA_URI:
                    try:
                        return GenreEnvelopeBodyV1.model_validate(ann.body)
                    except Exception:
                        continue
        return None

    # -- arbitrary annotations (for things that don't fit a typed bucket) ----

    def add_annotation(
        self,
        ann: Annotation,
        *,
        instance_id: Optional[str] = None,
        call_index: Optional[int] = None,
    ) -> None:
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

        It is also where a matching :meth:`add_unproduced_output` record is
        retired (nw#44): a successful write is proof the thing that record
        described has now been produced. ``instance_id`` / ``call_index``
        identify *this write's* unit precisely (see
        :mod:`nw.bodies.unproduced_output`'s module docstring for the key);
        omit ``instance_id`` only when it is not known — the retirement then
        falls back to ``(transform_name, call_index, upstream)``, which still
        cannot distinguish two DIFFERENT units sharing both an upstream set
        and a ``call_index`` (the residual case the module docstring names),
        so it may retire nothing, or (rarely) the wrong record.

        **Bypassing this method (a raw ``store.add``) leaves a matching
        unproduced-output record in place** — it reads as a live blocker
        after reload even though this write produced the thing it described.
        """
        from lacing import Tier, TierStereotype

        trace = self._verifying_trace_for(ann.id, ann.provenance.was_derived_from)
        retire_key = _unproduced_output_key_for(
            ann, instance_id=instance_id, call_index=call_index
        )
        with self._open() as store:
            store.add_tier(Tier(name=ann.tier, stereotype=TierStereotype.NONE))
            store.add(ann)
            _add_trace(store, trace)
            if retire_key is not None:
                _retire_unproduced_outputs(store, retire_key)

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

    # -- unproduced outputs (nw#44) -------------------------------------------

    def add_unproduced_output(
        self,
        skeleton: Annotation,
        *,
        transform_name: str,
        status: str,
        reason: str = "",
        error: Optional[BaseException] = None,
        blocked_by: tuple[int, ...] = (),
        instance_id: Optional[str] = None,
        call_index: Optional[int] = None,
        was_attributed_to: Optional[str] = None,
    ) -> UUID:
        """Persist why a planned output was never produced (nw#44).

        Mirrors one entry of ``TransformResult.failed`` / ``.blocked``
        (:class:`nw.transforms.FailedOutput`) — pass its ``skeleton``,
        ``status``, ``reason``, ``error`` and ``blocked_by`` straight through.
        Written under :data:`nw.bodies.UNPRODUCED_OUTPUT_BODY_SCHEMA_URI`,
        never under ``skeleton.body_schema_uri`` (see the module's docstring
        on why that tier is reserved for what was actually produced).

        ``instance_id`` (a fan-out unit's
        :func:`~nw.transforms.fanout.work_item_instance_id`) and
        ``call_index`` (this output's position within its ``execute()``
        call's skeleton tuple) together form the identity
        :meth:`add_annotation` retires by — see
        :mod:`nw.bodies.unproduced_output`'s module docstring for the full
        key. **Dedupes on that identity**: a record already outstanding for
        the same key is removed before this one is written, so a unit
        failing twice leaves one current record, not two.

        Parentless, like a verifying trace.

        ``reason`` never stores raw exception text — see the module
        docstring's "reason never carries raw exception text". When
        ``error`` is given, the original ``reason`` is logged
        (``logging.getLogger("nw.graph")``, ``WARNING``) and the persisted
        ``reason`` becomes a fixed sentence naming ``error``'s type.
        """
        from lacing import Tier, TierStereotype

        parent_ids = tuple(dict.fromkeys(skeleton.provenance.was_derived_from))
        stored_reason = reason
        if error is not None:
            _logger.warning(
                "nw#44 unproduced output (%s, %s): %s", transform_name, status, reason
            )
            stored_reason = f"{type(error).__name__}: see logs for details"
        body = UnproducedOutputBodyV1(
            transform_name=transform_name,
            instance_id=instance_id,
            call_index=call_index,
            upstream=tuple(str(p) for p in parent_ids),
            output_kind=skeleton.body_schema_uri,
            status=status,
            reason=stored_reason,
            error_type=type(error).__name__ if error is not None else None,
            blocked_by=tuple(blocked_by),
        )
        new_id = uuid4()
        ann = Annotation(
            id=new_id,
            tier=UNPRODUCED_OUTPUT_TIER,
            reference=MediaRef(
                asset_id=self.asset_id, interval=TimeInterval.from_seconds(0, 0)
            ),
            body=body.model_dump(mode="json"),
            body_schema_uri=UNPRODUCED_OUTPUT_BODY_SCHEMA_URI,
            provenance=Provenance(
                was_generated_by=f"transform:{transform_name}",
                was_attributed_to=was_attributed_to or f"agent:{transform_name}",
                was_derived_from=[],
                generated_at_time=_now_rt(),
                activity="record",
            ),
        )
        dedupe_key = (
            transform_name,
            instance_id,
            call_index,
            frozenset(str(p) for p in parent_ids),
        )
        with self._open() as store:
            store.add_tier(
                Tier(name=UNPRODUCED_OUTPUT_TIER, stereotype=TierStereotype.NONE)
            )
            _retire_unproduced_outputs(store, dedupe_key)
            store.add(ann)
        return new_id

    def unproduced_outputs(
        self, *, transform_name: Optional[str] = None
    ) -> list[StoredUnproducedOutput]:
        """Every unproduced-output record still outstanding, oldest first.

        A record disappears the moment :meth:`add_annotation` writes a real
        output for the same identity, or another :meth:`add_unproduced_output`
        call for the same identity supersedes it (dedupe) — what this
        returns is exactly "still missing", survives a reload, and is not a
        cache of any in-memory ``TransformResult``.
        """
        with self._open_read() as store:
            rows = _collect_typed(
                store,
                tier=UNPRODUCED_OUTPUT_TIER,
                schema_uri=UNPRODUCED_OUTPUT_BODY_SCHEMA_URI,
                model=UnproducedOutputBodyV1,
                wrap=lambda ann, body: StoredUnproducedOutput(
                    annotation_id=ann.id, body=body
                ),
                sort_key=lambda s: 0,  # insertion order, like decisions
            )
        if transform_name is not None:
            rows = [r for r in rows if r.body.transform_name == transform_name]
        return rows

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

    Deterministic order: (generation time, id) — the same public ordering
    contract as :func:`nw.freshness.stale_verdicts`. The closure used to be
    returned in set-iteration (hash-derived) order, which leaked into every
    consumer's output (nw#39).
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

    return sorted(
        (by_id[i] for i in seen if i in by_id),
        key=lambda a: (a.provenance.generated_at_time.to_seconds(), str(a.id)),
    )


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

    Asks each store for the tier rather than deserializing every annotation
    and filtering. ``by_tier`` is a real indexed query on all four lacing
    backends and was called by nothing in nw; this walked the whole project
    to answer a question about one tier. Measured on 2000 annotations with
    200 at the tier: **33.5 ms → 3.9 ms**, and the gap widens with project
    size because one is O(all rows) and the other O(matching).
    """
    out: list[Annotation] = []
    with open_project_stores(project_root) as stores:
        for store in stores:
            out.extend(store.by_tier(tier))
    return out


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
                if (target := _trace_target(ann)) is not None and target not in existing
            ]
            for annotation_id in orphans:
                store.remove(annotation_id)
            removed.extend(orphans)
    return removed


def backfill_traces(project_root: str | Path, *, execute: bool = False) -> dict:
    """Bless a pre-trace project so the verifying-trace rule can read it (nw#58).

    On a project whose annotations predate nw#24's trace-writing, the
    verifying-trace rule is a behavior change, not a wrapper: every derived
    annotation reads stale (``no-trace``), and nothing heals them — regen
    skips non-Transform-produced annotations, and body updates write no
    trace. This writes, for each derived annotation with no trace, a trace
    against its parents' CURRENT content digests: blessing at-rest state as
    fresh, which is exactly the old timestamp rule's verdict for at-rest
    data — semantics-preserving at the moment of migration.

    **Report-only by default.** The first thing run against a real user's
    projects should be a read; pass ``execute=True`` to write. Idempotent
    either way: an annotation that already has a usable trace is counted in
    ``already_traced`` and never rewritten, so a partial run is simply
    re-run rather than reasoned about. One caveat keeps "a read" honest at
    the FILE level: stores are opened with ``migrate=True``, so a store
    stamped at an older lacing schema is upgraded ON OPEN even under the
    default — run this only where the build that serves these stores is
    already the new one (the D-vg-mcp-10 deploy ordering; a pre-migrated
    file makes an old serving build refuse it).

    **Not blessed, by design — the old rule's own stale verdicts.** A parent
    edited AFTER the annotation was derived is exactly the
    pending-regeneration state the old timestamp rule reported stale;
    blessing it would silently clear a real signal. Such annotations land in
    ``skipped`` and stay no-trace-stale — same verdict, and a later regen
    writes the true trace through the chokepoint. (Exact preservation in the
    other direction is impossible — the trace rule recurses where the old
    rule was one-hop — but that residual over-reports, the direction
    :mod:`nw.freshness` documents as the safe one.)

    What is deliberately NOT blessed, each with a ``skipped`` entry naming
    the annotation and the reason:

    - a parent that no longer exists — that annotation is genuinely
      ``upstream-missing``, and a fabricated trace would hide a real hole;
    - a parent list carrying artifact refs (64-hex asset ids) — the
      annotation-tier trace cannot cover them (nw#55), and a trace over a
      subset of the parents reads as stale anyway ("upstream set is not
      exactly ``was_derived_from``"), so writing one would be decoration;
    - a parent whose body cannot be digested — broken data at the producer,
      same rule as :func:`build_verifying_trace`.

    Parentless annotations are never stale by contract, so they are counted
    (``parentless``) and need nothing.

    Returns one project's report — callers migrating a tree of projects loop
    and get per-project summaries for free:
    ``{"project", "stores_found", "examined", "backfilled", "already_traced",
    "traced_unusable", "parentless",
    "skipped": [{"annotation_id", "reason"}, ...], "executed"}``.
    ``backfilled`` is the count of traces written when ``execute=True``, and
    of traces that WOULD be written otherwise; ``executed`` says which
    reading applies. Read ``stores_found`` before trusting zeros: a typo'd
    or empty root reports all-zero COUNTS, and ``stores_found == 0`` is what
    distinguishes "nothing to migrate" from "not a project here".
    ``traced_unusable`` counts annotations whose existing trace the
    freshness rule cannot use (foreign digest scheme, mismatched upstream
    set) — permanently stale, deliberately not overwritten here; expect it
    to be zero on genuine pre-trace projects. A broken project (corrupt
    store, unreadable ``project.json``) RAISES rather than reporting —
    catch per root in a tree loop so one damaged project is recorded, not
    silently averaged away.
    """
    from nw.freshness import _traces_by_target  # one owner of the trace-reading rule

    graph = ProjectGraph(project_root)
    annotations: list[Annotation] = []
    stores_found = 0
    with open_project_stores(graph.project_root) as stores:
        for store in stores:
            stores_found += 1
            annotations.extend(store.all())
    by_id = {a.id: a for a in annotations}
    traced = _traces_by_target(annotations)

    to_write: list[Annotation] = []
    already_traced = 0
    traced_unusable = 0
    parentless = 0
    skipped: list[dict] = []
    examined = 0

    for ann in annotations:
        if ann.body_schema_uri == VERIFYING_TRACE_BODY_SCHEMA_URI:
            continue  # traces describe; they are not described
        examined += 1
        parents = tuple(dict.fromkeys(ann.provenance.was_derived_from))
        if not parents:
            parentless += 1
            continue
        existing = traced.get(ann.id)
        if existing is not None:
            # "Has a trace" is not "has a trace the freshness rule can USE" —
            # a foreign digest scheme or a mismatched upstream set reads
            # stale forever, and counting it as already_traced would hand
            # the operator an instrument that reads "healthy" over a row
            # that is not. Never rewritten here (a second trace would win
            # by recency, silently overriding what may be deliberate) —
            # just counted where it can be seen.
            if _trace_is_usable(existing, parents):
                already_traced += 1
            else:
                traced_unusable += 1
            continue
        alien = [p for p in parents if not isinstance(p, UUID)]
        if alien:
            skipped.append(
                {
                    "annotation_id": str(ann.id),
                    "reason": (
                        "parents include artifact refs (asset ids); the "
                        "annotation-tier trace cannot cover them (nw#55)"
                    ),
                }
            )
            continue
        missing = [p for p in parents if p not in by_id]
        if missing:
            skipped.append(
                {
                    "annotation_id": str(ann.id),
                    "reason": (
                        f"parent(s) missing: {[str(m) for m in missing]} — "
                        "genuinely upstream-missing; a fabricated trace "
                        "would hide a real hole"
                    ),
                }
            )
            continue
        unplaceable = [
            i
            for i in (ann.id, *parents)
            if not by_id[i].provenance.generated_at_is_known
        ]
        if unplaceable:
            # Tick 0 is lacing's UNKNOWN sentinel, not the epoch (lacing#44).
            # The timestamp rule below cannot order this row against its
            # parents — and a tick-0 PARENT would read older than every child,
            # blessing a row that is not verifiable. It stays stale
            # (generated-at-unknown) until the timestamp itself is backfilled.
            skipped.append(
                {
                    "annotation_id": str(ann.id),
                    "reason": (
                        f"generated_at_time unknown (tick 0, lacing#44) on "
                        f"{[str(u) for u in unplaceable]} — cannot be ordered "
                        "against its parents; unverifiable stays stale. "
                        "Backfill the timestamp, then re-run"
                    ),
                }
            )
            continue
        own_t = ann.provenance.generated_at_time.to_seconds()
        edited = [
            p
            for p in parents
            if by_id[p].provenance.generated_at_time.to_seconds() > own_t
        ]
        if edited:
            # The old timestamp rule read this STALE (a parent edited after
            # the derive, regeneration pending) — blessing it would silently
            # clear a real pending-regen signal. It stays no-trace-stale,
            # which is the same verdict; a later regen writes the true trace
            # through the chokepoint.
            skipped.append(
                {
                    "annotation_id": str(ann.id),
                    "reason": (
                        f"parent(s) edited after this was derived "
                        f"({[str(e) for e in edited]}) — stale under the old "
                        "timestamp rule too; regenerate rather than bless"
                    ),
                }
            )
            continue
        trace = build_verifying_trace(
            for_annotation_id=ann.id,
            parent_ids=parents,
            upstream=[by_id[p] for p in parents],
            asset_id=graph.asset_id,
        )
        if trace is None:
            skipped.append(
                {
                    "annotation_id": str(ann.id),
                    "reason": "a parent's body could not be digested",
                }
            )
            continue
        to_write.append(trace)

    if execute and to_write:
        from lacing import Tier, TierStereotype

        with graph._open() as store:
            store.add_tier(
                Tier(name=VERIFYING_TRACE_TIER, stereotype=TierStereotype.NONE)
            )
            for trace in to_write:
                store.add(trace)

    return {
        "project": str(graph.project_root),
        "stores_found": stores_found,
        "examined": examined,
        "backfilled": len(to_write),
        "already_traced": already_traced,
        "traced_unusable": traced_unusable,
        "parentless": parentless,
        "skipped": skipped,
        "executed": bool(execute),
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _trace_is_usable(body, parents: tuple) -> bool:
    """Whether an existing trace can actually VERIFY — the freshness rule's
    own preconditions: the digest scheme matches, every recorded upstream id
    parses, and the recorded upstream set is exactly the parents. A trace
    failing any of these reads stale forever (digest-scheme-changed /
    trace-upstream-mismatch), which is not "already traced" in any sense an
    operator's instrument should report as healthy."""
    from lacing.digest import VALUE_DIGEST_SCHEME

    if body.digest_scheme != VALUE_DIGEST_SCHEME:
        return False
    try:
        recorded = {UUID(u.annotation_id) for u in body.upstream}
    except (ValueError, TypeError, AttributeError):
        return False
    return recorded == set(parents)


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


_UnproducedOutputKey = tuple[str, Optional[str], Optional[int], frozenset]
"""``(transform_name, instance_id, call_index, upstream)`` — see
:mod:`nw.bodies.unproduced_output`'s module docstring for why all four are
needed rather than ``(transform_name, upstream)`` alone."""


def _unproduced_output_key_for(
    ann: Annotation,
    *,
    instance_id: Optional[str] = None,
    call_index: Optional[int] = None,
) -> Optional[_UnproducedOutputKey]:
    """The retirement key a written annotation settles, or ``None`` when it
    was not produced by a Transform.

    Reads ``ann.provenance.was_generated_by``, which
    :func:`nw.transforms._provenance.derive_provenance` always sets to
    ``"transform:<name>@<impl_version>"`` on both a completed output and the
    ``FailedOutput.skeleton`` an unproduced-output record was built from — so
    the same parse recovers the same ``transform_name`` from either side
    without either module importing the other. ``instance_id`` /
    ``call_index`` are the caller's, passed straight through — this function
    only resolves the name and ``upstream``.
    """
    generated_by = ann.provenance.was_generated_by
    if not isinstance(generated_by, str) or not generated_by.startswith("transform:"):
        return None
    name = generated_by[len("transform:") :].partition("@")[0]
    if not name:
        return None
    upstream = frozenset(str(p) for p in ann.provenance.was_derived_from)
    return name, instance_id, call_index, upstream


def _unproduced_output_key_matches(body: dict, key: _UnproducedOutputKey) -> bool:
    """Whether a stored unproduced-output ``body`` settles ``key``.

    ``instance_id`` identifies the fan-out **unit** — matching on it alone
    would collapse two *different calls of the same unit's own plan* (a
    Transform is free to plan more than one call per work item) into one
    key, so ``call_index`` still has to agree even when both sides carry an
    ``instance_id``. ``upstream`` is dropped from that comparison, though: a
    retried unit is free to re-plan its calls against a different upstream
    shape and still be the same unit.

    Without ``instance_id`` (a non-fan-out call, or a Transform that does
    not forward ``unit_instance_id``), the fallback pair — ``call_index``
    *and* ``upstream`` — must both match; either alone is exactly the
    collision this key exists to avoid (a batch call's other outputs share
    ``upstream``; two different calls in two different batches can share an
    index).
    """
    transform_name, instance_id, call_index, upstream = key
    if body.get("transform_name") != transform_name:
        return False
    if body.get("call_index") != call_index:
        return False
    body_instance_id = body.get("instance_id")
    if instance_id is not None and body_instance_id is not None:
        return body_instance_id == instance_id
    return frozenset(body.get("upstream") or ()) == upstream


def _retire_unproduced_outputs(
    store: IntervalAnnotationStore, key: _UnproducedOutputKey
) -> None:
    """Remove every unproduced-output record matching ``key`` (nw#44).

    Also the dedupe primitive :meth:`ProjectGraph.add_unproduced_output`
    calls on itself before writing a new record for the same identity.
    """
    for ann in list(store.by_tier(UNPRODUCED_OUTPUT_TIER)):
        if ann.body_schema_uri != UNPRODUCED_OUTPUT_BODY_SCHEMA_URI:
            continue
        if not isinstance(ann.body, dict):
            continue
        if _unproduced_output_key_matches(ann.body, key):
            store.remove(ann.id)


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
    store: Optional[IntervalAnnotationStore],
    *,
    tier: str,
    schema_uri: str,
    model,
    wrap,
    sort_key,
) -> list:
    """Typed rows at one tier. ``None`` store — no graph on disk — yields ``[]``.

    Reads through ``by_tier``, the indexed query every lacing backend
    implements, rather than filtering ``all()`` in Python. Measured on a
    2000-annotation project with 200 rows at the tier: **33.5 ms → 3.9 ms**,
    and the gap widens with project size because one is O(all rows) and the
    other O(matching). ``by_tier`` was implemented on all four backends and
    called by nothing in nw.
    """
    if store is None:
        return []
    out = []
    for ann in store.by_tier(tier):
        if ann.body_schema_uri != schema_uri:
            continue
        try:
            body = model.model_validate(ann.body)
        except Exception:
            continue
        out.append(wrap(ann, body))
    return sorted(out, key=sort_key)
