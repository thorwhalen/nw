"""Config-driven backend selection for nw's annotation graph stores.

Phase 4 of the storage migration (reelee#177). A nw project keeps its
annotations in lacing ``IntervalAnnotationStore``s — historically one
``SqliteStore`` file per *scope* (the project graph, the storyboard, the
lyrics alignment). This module is the **single seam** that decides whether a
given scope is backed by SQLite (the default — byte-for-byte the old
behaviour, one file per scope) or by a shared Postgres database
(:class:`lacing.store.PostgresStore`, tenant-scoped).

The facade principle in action
------------------------------

Every nw site that needs a graph store — :func:`nw.migrate.open_project_graph`,
the storyboard load/save, the lyrics-alignment read, and the provenance walk in
:mod:`nw.graph` — routes through :func:`open_graph_store` /
:func:`iter_scope_stores` here. None of them learns *which* backend answered;
:class:`nw.graph.ProjectGraph` and every typed accessor are unchanged.

The environment contract
------------------------

==========================  ====================================================
Variable                    Meaning
==========================  ====================================================
``NW_GRAPH_BACKEND``        ``sqlite`` (**default**) | ``postgres``. Unknown /
                            empty → ``sqlite``.
``NW_GRAPH_DB_URL``         (postgres) psycopg conninfo URL for the shared DB.
``NW_GRAPH_OWNER_ID``       (postgres, optional) tenant owner; defaults to
                            lacing's ``DEFAULT_OWNER_ID``. Forward seam for the
                            access layer (reelee#174); enforcement deferred.
==========================  ====================================================

**Safety first.** The default (no env, or any unrecognized backend) is always
SQLite — *identical* to the behaviour before this module existed. A local run
never changes and never crashes because Postgres env happens to be unset; if
``NW_GRAPH_BACKEND=postgres`` but ``NW_GRAPH_DB_URL`` is missing, we log a
warning and fall back to SQLite rather than failing.

Tenant scoping across scopes
----------------------------

In SQLite mode each scope is a distinct file, so they never collide. In
Postgres mode they share tables, so each scope gets a distinct ``project_id``
built from the project's stable ``project_asset_id`` and the scope name:
``"<asset_id>:<scope>"``. :func:`iter_scope_stores` enumerates exactly the same
scope set the SQLite walk did, so :func:`nw.graph.iter_all_annotations` yields
each annotation once under either backend.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from lacing import SqliteStore
from lacing.store import IntervalAnnotationStore

__all__ = [
    "GraphBackend",
    "selected_backend",
    "open_graph_store",
    "iter_scope_stores",
    "scope_name_for_db",
]

logger = logging.getLogger(__name__)

# --- the env contract (single source of truth for variable names) ---------

ENV_BACKEND = "NW_GRAPH_BACKEND"
ENV_DB_URL = "NW_GRAPH_DB_URL"
ENV_OWNER_ID = "NW_GRAPH_OWNER_ID"

GraphBackend = str  # "sqlite" | "postgres" at the boundary; str for forgiveness
_SQLITE = "sqlite"
_POSTGRES = "postgres"

# The scope names. These are the logical stores a project owns; they map 1:1 to
# the per-scope SQLite filenames in the legacy layout (see scope_name_for_db).
SCOPE_GRAPH = "graph"
SCOPE_STORYBOARD = "storyboard"
SCOPE_ALIGNMENT = "alignment"


def selected_backend(env: Mapping[str, str] | None = None) -> GraphBackend:
    """Resolve the configured graph backend from the environment.

    Pure and side-effect-free. Any unrecognized / empty value resolves to
    ``"sqlite"`` — the safe default that never changes a local run.

    Args:
        env: Environment mapping to read. Defaults to ``os.environ``.

    Returns:
        ``"sqlite"`` or ``"postgres"``.
    """
    if env is None:
        env = os.environ
    raw = (env.get(ENV_BACKEND) or "").strip().lower()
    return _POSTGRES if raw == _POSTGRES else _SQLITE


def scope_name_for_db(db_path: Path) -> str:
    """Map a legacy per-scope SQLite filename to its scope name.

    ``project.annot.sqlite`` → ``"graph"``; ``storyboard.annot.sqlite`` →
    ``"storyboard"``; ``alignment.annot`` → ``"alignment"``. Anything else maps
    to the file's stem so a new store kind gets a stable scope automatically.
    """
    name = Path(db_path).name
    if name.startswith("project.annot"):
        return SCOPE_GRAPH
    if name.startswith("storyboard.annot"):
        return SCOPE_STORYBOARD
    if name.startswith("alignment"):
        return SCOPE_ALIGNMENT
    return Path(db_path).stem


def _postgres_project_id(asset_id: str, scope: str) -> str:
    """The Postgres ``project_id`` for one (project, scope): keeps scopes apart
    in the shared tables yet groups them under one project asset."""
    return f"{asset_id}:{scope}"


def open_graph_store(
    db_path: Path | str,
    *,
    asset_id: str,
    scope: Optional[str] = None,
    rate: Optional[int] = None,
    env: Mapping[str, str] | None = None,
) -> IntervalAnnotationStore:
    """Open the annotation store for one scope, backend chosen by env.

    The single place that decides SQLite-vs-Postgres for a graph store. Callers
    pass the legacy SQLite path (still the source of truth for *where* the file
    lives in SQLite mode) plus the project's ``asset_id`` (the tenant anchor in
    Postgres mode).

    Args:
        db_path: The per-scope SQLite path (used directly in SQLite mode; in
            Postgres mode only its filename is used to derive the scope).
        asset_id: The project's stable ``project_asset_id`` — the Postgres
            tenant anchor. Ignored in SQLite mode.
        scope: The logical scope name (``"graph"`` / ``"storyboard"`` /
            ``"alignment"`` / …). Defaults to deriving it from ``db_path``.
        rate: Project-wide rate for the Postgres store. Defaults to lacing's
            ``DEFAULT_RATE``. Ignored in SQLite mode.
        env: Environment mapping. Defaults to ``os.environ``.

    Returns:
        A live :class:`~lacing.store.IntervalAnnotationStore` — ``SqliteStore``
        (default) or a tenant-scoped ``PostgresStore``. Caller closes it (or
        uses it as a context manager).
    """
    if env is None:
        env = os.environ
    db_path = Path(db_path)
    if scope is None:
        scope = scope_name_for_db(db_path)

    if selected_backend(env) == _POSTGRES:
        store = _open_postgres(asset_id=asset_id, scope=scope, rate=rate, env=env)
        if store is not None:
            return store
        # _open_postgres logged a warning and we fall through to SQLite.

    db_path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteStore(str(db_path))


def _open_postgres(
    *,
    asset_id: str,
    scope: str,
    rate: Optional[int],
    env: Mapping[str, str],
) -> Optional[IntervalAnnotationStore]:
    """Build a tenant-scoped ``PostgresStore`` from env, or None to fall back."""
    db_url = (env.get(ENV_DB_URL) or "").strip() or None
    if db_url is None:
        logger.warning(
            "%s=postgres but %s is unset; falling back to the per-project "
            "SQLite store. Set the Postgres conninfo to use the shared DB.",
            ENV_BACKEND,
            ENV_DB_URL,
        )
        return None

    from lacing import DEFAULT_RATE
    from lacing.store import DEFAULT_OWNER_ID, PostgresStore

    owner_id = (env.get(ENV_OWNER_ID) or "").strip() or DEFAULT_OWNER_ID
    return PostgresStore(
        db_url,
        rate=rate if rate is not None else DEFAULT_RATE,
        owner_id=owner_id,
        project_id=_postgres_project_id(asset_id, scope),
    )


@contextmanager
def iter_scope_stores(
    scope_paths: Mapping[str, Path],
    *,
    asset_id: str,
    env: Mapping[str, str] | None = None,
) -> Iterator[Iterator[IntervalAnnotationStore]]:
    """Yield an iterator of open stores, one per scope, backend chosen by env.

    The provenance walk in :func:`nw.graph.iter_all_annotations` needs *every*
    store under a project. In SQLite mode that's "every existing per-scope
    file"; in Postgres mode it's "every scope's tenant" — and this generator
    enumerates exactly the same scope set under both backends, so each
    annotation is yielded once either way.

    Args:
        scope_paths: ``{scope_name: legacy_sqlite_path}`` — only paths that
            *exist* on disk are visited in SQLite mode; in Postgres mode every
            listed scope is visited (existence is a DB question, not a file
            one).
        asset_id: The project's ``project_asset_id`` (Postgres tenant anchor).
        env: Environment mapping. Defaults to ``os.environ``.

    Yields:
        A single iterator that produces each scope's open store in turn. Each
        store is closed before the next is opened, so callers must consume
        annotations eagerly per store (which the walk does).
    """
    if env is None:
        env = os.environ
    backend = selected_backend(env)

    def _gen() -> Iterator[IntervalAnnotationStore]:
        for scope, db_path in scope_paths.items():
            if backend == _SQLITE and not Path(db_path).exists():
                continue
            store = open_graph_store(
                db_path, asset_id=asset_id, scope=scope, env=env
            )
            try:
                yield store
            finally:
                _close_if_possible(store)

    yield _gen()


def _close_if_possible(store) -> None:
    close = getattr(store, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass
