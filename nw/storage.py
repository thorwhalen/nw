"""Where a project's state lives: the project-storage seam (nw#101).

An nw project has three kinds of state, and this module is the one place that
decides where each of them is kept:

- **JSON documents** (``project.json``, ``.nw/…`` sentinels, entity cards):
  a ``MutableMapping`` keyed by project-relative POSIX path.
- **The annotation graph** (sections, shots, genre envelope, derived
  annotations, verifying traces), plus any other annotation *scopes* a project
  has (the storyboard, the lyric alignment): lacing
  ``IntervalAnnotationStore``\\ s.
- **Files** (media, renders): a folder, ``root``. nw never abstracts these;
  a genre's renderer writes where it writes.

:class:`ProjectStorage` is the strategy. :class:`FolderStorage` is the
default and is exactly nw's historical layout: ``project.json`` and a
``project.annot.sqlite`` beside it, the backend chosen by
:mod:`nw.graph_backend`. :class:`MappingStorage` keeps the graph and the
documents in mappings the *caller* owns, so an app that already persists its
data through ``dol`` stores (``an``'s project mall) hands nw those stores and
nw writes no second copy beside them. That removes the two couplings ``an``'s
ADR 0004 recorded against adopting nw: a hard-wired layout and a second
persistence path.

Everything in nw that takes a "project" accepts a path **or** a storage, and
:func:`as_project_storage` turns the first into the second. A path is
resolved by asking each registered *storage resolver* in turn (a genre
registers one with :func:`register_project_storage` for the folders it
recognises), and falls back to :class:`FolderStorage`. That is what lets a
host that only knows a project's path (reelee opening a guest genre's project)
reach a genre's graph wherever the genre keeps it.

>>> from lacing import MemoryStore
>>> s = MappingStorage(root=".", graph={}, docs={}, store_factory=lambda m: MemoryStore())
>>> as_project_storage(s) is s
True
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from typing import (
    Any,
    Callable,
    Iterator,
    Mapping,
    MutableMapping,
    Optional,
    Protocol,
    Union,
    runtime_checkable,
)

from dol import Files, mk_dirs_if_missing, wrap_kvs
from lacing import IntervalAnnotationStore
from xdol import Registry

from .graph_backend import (
    SCOPE_ALIGNMENT,
    SCOPE_GRAPH,
    SCOPE_STORYBOARD,
    iter_scope_stores,
)

PROJECT_FILE_NAME = "project.json"

#: The subfolders :meth:`nw.Project.init` creates in a :class:`FolderStorage`
#: project: the music-video layout nw started with. A genre with another layout
#: passes its own ``init_folders``.
DFLT_INIT_FOLDERS: tuple[str, ...] = (
    "characters",
    "environments",
    "shots",
    "output",
    "lyrics",
    "script",
    "song",
    ".nw",
)


@runtime_checkable
class ProjectStorage(Protocol):
    """Where one project's documents and annotation stores live.

    ``open_graph`` creates the graph store if needed and ensures nw's tiers.
    ``open_graph_readonly`` must not write, and raises ``FileNotFoundError``
    when there is no graph yet. ``open_stores`` yields every annotation scope
    the project has (graph included), each store closed before the next opens.
    """

    root: Path
    docs: MutableMapping[str, Any]
    init_folders: tuple[str, ...]

    @property
    def asset_id(self) -> str: ...

    def open_graph(self) -> IntervalAnnotationStore: ...

    def open_graph_readonly(self) -> IntervalAnnotationStore: ...

    def open_stores(self) -> Any:  # ContextManager[Iterator[IntervalAnnotationStore]]
        ...


ProjectLike = Union[str, Path, ProjectStorage]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def json_docs(root: str | Path) -> MutableMapping[str, Any]:
    """The folder-backed store for a project's JSON documents.

    A ``dol`` ``MutableMapping`` keyed by project-relative POSIX path
    (``"project.json"``, ``"characters/<name>/card.json"``,
    ``"shots/<id>/shot.json"``, the ``".nw/migrated_to_graph"`` sentinel).
    Values are Python objects; on disk they are ``indent=2`` JSON. Parent
    directories are created on write; a missing key raises ``KeyError``.
    """
    return wrap_kvs(
        mk_dirs_if_missing(Files(str(root))),
        data_of_obj=lambda obj: json.dumps(obj, indent=2).encode("utf-8"),
        obj_of_data=lambda data: json.loads(data),
    )


def asset_id_of(root: str | Path, spec: Optional[Mapping[str, Any]]) -> str:
    """The asset id anchoring a project's annotations.

    The SHA-256 of the registered song's bytes when there is one on disk,
    else a stable hash of the project's resolved root and title.
    """
    spec = spec or {}
    title = spec.get("title") or ""
    song = spec.get("song")
    if isinstance(song, dict) and song.get("audio_path"):
        sp = Path(song["audio_path"])
        song_path = sp if sp.is_absolute() else Path(root) / sp
        if song_path.exists():
            from lacing import hash_file

            return hash_file(song_path)
    seed = f"nw:project:{Path(root).resolve()}:{title}".encode()
    return hashlib.sha256(seed).hexdigest()


def _project_doc(docs: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    try:
        return docs[PROJECT_FILE_NAME]
    except KeyError:
        return None


def _close_quietly(store) -> None:
    close = getattr(store, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# The default: nw's own folder layout
# ---------------------------------------------------------------------------


class FolderStorage:
    """nw's historical layout: everything under ``root``.

    ``project.json`` and the other documents are JSON files under ``root``;
    the graph is ``root/project.annot.sqlite`` (or a Postgres tenant, by
    :mod:`nw.graph_backend`); the storyboard and lyric-alignment scopes are
    their legacy files. Legacy (pre-graph) projects are migrated on open by
    :class:`nw.Project`, which is why :attr:`migrates_legacy` is true here.
    """

    migrates_legacy = True

    def __init__(
        self, root: str | Path, *, init_folders: tuple[str, ...] = DFLT_INIT_FOLDERS
    ) -> None:
        self.root = Path(root).resolve()
        self.init_folders = tuple(init_folders)
        self.docs = json_docs(self.root)

    def __repr__(self) -> str:
        return f"FolderStorage({str(self.root)!r})"

    @property
    def asset_id(self) -> str:
        # Read through each time: the title or song can change, and the
        # historical contract is "derived from project.json as it is now".
        from .migrate import project_asset_id

        return project_asset_id(self.root)

    def scope_paths(self) -> dict[str, Path]:
        """``{scope_name: legacy_sqlite_path}`` for this project's stores."""
        from .migrate import project_graph_db_path

        return {
            SCOPE_GRAPH: project_graph_db_path(self.root),
            SCOPE_STORYBOARD: self.root / "storyboard.annot.sqlite",
            SCOPE_ALIGNMENT: self.root / "lyrics" / "alignment.annot",
        }

    def open_graph(self) -> IntervalAnnotationStore:
        from .migrate import open_project_graph

        return open_project_graph(self.root)

    def open_graph_readonly(self) -> IntervalAnnotationStore:
        from .migrate import open_project_graph_readonly

        return open_project_graph_readonly(self.root)

    @contextmanager
    def open_stores(self) -> Iterator[Iterator[IntervalAnnotationStore]]:
        with iter_scope_stores(self.scope_paths(), asset_id=self.asset_id) as stores:
            yield stores


# ---------------------------------------------------------------------------
# Caller-owned mappings (an app's dol stores)
# ---------------------------------------------------------------------------


def _dflt_store_factory(mapping: MutableMapping) -> IntervalAnnotationStore:
    from lacing.store import MappingStore  # lacing >= the release that ships it

    return MappingStore(mapping)


class MappingStorage:
    """A project whose graph and documents live in mappings the caller owns.

    Args:
        root: The project's folder, for files (media, renders) and for the
            asset id. nw writes no document or store file here.
        graph: The mapping the graph store persists into.
        docs: The mapping holding nw's JSON documents (``project.json``, …).
        scopes: Further annotation scopes, ``{scope_name: mapping}``; walked
            by :meth:`open_stores` after the graph.
        store_factory: ``mapping -> IntervalAnnotationStore``. Defaults to
            ``lacing.store.MappingStore``. One store object is kept per
            mapping, so every open sees the same in-memory index; the
            underlying store has no cross-process lock, so one writer at a
            time.
        init_folders: Subfolders :meth:`nw.Project.init` creates under
            ``root`` (none by default: the genre owns its layout).
        asset_id: Fix the asset id instead of deriving it from ``root`` and
            the project's title.
    """

    migrates_legacy = False

    def __init__(
        self,
        *,
        root: str | Path,
        graph: MutableMapping,
        docs: MutableMapping[str, Any],
        scopes: Mapping[str, MutableMapping] = (),
        store_factory: Callable[[MutableMapping], IntervalAnnotationStore] = (
            _dflt_store_factory
        ),
        init_folders: tuple[str, ...] = (),
        asset_id: Optional[str] = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.docs = docs
        self.init_folders = tuple(init_folders)
        self._mappings: dict[str, MutableMapping] = {SCOPE_GRAPH: graph}
        self._mappings.update(dict(scopes or {}))
        self._store_factory = store_factory
        self._stores: dict[str, IntervalAnnotationStore] = {}
        self._asset_id = asset_id

    def __repr__(self) -> str:
        return f"MappingStorage(root={str(self.root)!r}, scopes={list(self._mappings)})"

    @property
    def asset_id(self) -> str:
        if self._asset_id is not None:
            return self._asset_id
        return asset_id_of(self.root, _project_doc(self.docs))

    def _store(self, scope: str) -> IntervalAnnotationStore:
        if scope not in self._stores:
            self._stores[scope] = self._store_factory(self._mappings[scope])
        return self._stores[scope]

    def open_graph(self) -> IntervalAnnotationStore:
        from .migrate import _ensure_tiers

        store = self._store(SCOPE_GRAPH)
        if not _has_graph_tiers(store):
            _ensure_tiers(store)
        return store

    def open_graph_readonly(self) -> IntervalAnnotationStore:
        mapping = self._mappings[SCOPE_GRAPH]
        if SCOPE_GRAPH not in self._stores and not len(mapping):
            raise FileNotFoundError(f"no graph yet for project: {self.root}")
        return self._store(SCOPE_GRAPH)

    @contextmanager
    def open_stores(self) -> Iterator[Iterator[IntervalAnnotationStore]]:
        def _gen():
            for scope, mapping in self._mappings.items():
                if scope in self._stores or len(mapping):
                    yield self._store(scope)

        yield _gen()


def _has_graph_tiers(store: IntervalAnnotationStore) -> bool:
    from .migrate import _PROJECT_TIERS

    names = {t.name for t in store.tiers()}
    return all(n in names for n in _PROJECT_TIERS)


# ---------------------------------------------------------------------------
# Resolution: path -> storage
# ---------------------------------------------------------------------------

#: Storage resolvers, ``name -> (root: Path) -> ProjectStorage | None``. Asked
#: in registration order by :func:`as_project_storage`; the first non-``None``
#: answer wins. ``on_conflict="error"``, so two genres cannot silently claim
#: one name.
project_storages: Registry = Registry(name="nw.project_storages", on_conflict="error")


def register_project_storage(
    name: str, resolver: Callable[[Path], Optional[ProjectStorage]]
) -> Callable[[Path], Optional[ProjectStorage]]:
    """Register a resolver that recognises a genre's project folders.

    The resolver gets a resolved folder path and returns the
    :class:`ProjectStorage` for it, or ``None`` when the folder is not one of
    its projects. It must be cheap and must not write: hosts call it for every
    project they list.
    """
    if not callable(resolver):
        raise TypeError(f"resolver for {name!r} must be callable")
    project_storages.register(name, resolver)
    return resolver


def as_project_storage(project: Any) -> ProjectStorage:
    """The :class:`ProjectStorage` for a path, a storage, or an ``nw.Project``.

    A path goes to the first registered resolver that recognises it, else to
    :class:`FolderStorage`.
    """
    storage = getattr(project, "storage", None)
    if storage is not None and not isinstance(project, (str, Path)):
        return storage
    if isinstance(project, ProjectStorage):
        return project
    if isinstance(project, (str, Path)):
        root = Path(project).resolve()
        for name in list(project_storages):
            found = project_storages[name](root)
            if found is not None:
                return found
        return FolderStorage(root)
    raise TypeError(
        f"expected a project path, a ProjectStorage or an nw.Project, "
        f"got {type(project).__name__}"
    )


__all__ = [
    "DFLT_INIT_FOLDERS",
    "FolderStorage",
    "MappingStorage",
    "PROJECT_FILE_NAME",
    "ProjectLike",
    "ProjectStorage",
    "as_project_storage",
    "asset_id_of",
    "json_docs",
    "project_storages",
    "register_project_storage",
]
