# nw.storage

Where a project’s state lives: the project-storage seam (nw#101).

An nw project has three kinds of state, and this module is the one place that
decides where each of them is kept:

- **JSON documents** (`project.json`, `.nw/…` sentinels, entity cards):
  a `MutableMapping` keyed by project-relative POSIX path.
- **The annotation graph** (sections, shots, genre envelope, derived
  annotations, verifying traces), plus any other annotation *scopes* a project
  has (the storyboard, the lyric alignment): lacing
  `IntervalAnnotationStore`s.
- **Files** (media, renders): a folder, `root`. nw never abstracts these;
  a genre’s renderer writes where it writes.

[`ProjectStorage`](#nw.storage.ProjectStorage) is the strategy. [`FolderStorage`](#nw.storage.FolderStorage) is the
default and is exactly nw’s historical layout: `project.json` and a
`project.annot.sqlite` beside it, the backend chosen by
[`nw.graph_backend`](nw.graph_backend.html.md#module-nw.graph_backend). [`MappingStorage`](#nw.storage.MappingStorage) keeps the graph and the
documents in mappings the *caller* owns, so an app that already persists its
data through `dol` stores (`an`’s project mall) hands nw those stores and
nw writes no second copy beside them. That removes the two couplings `an`’s
ADR 0004 recorded against adopting nw: a hard-wired layout and a second
persistence path.

Everything in nw that takes a “project” accepts a path **or** a storage, and
[`as_project_storage()`](#nw.storage.as_project_storage) turns the first into the second. A path is
resolved by asking each registered *storage resolver* in turn (a genre
registers one with [`register_project_storage()`](#nw.storage.register_project_storage) for the folders it
recognises), and falls back to [`FolderStorage`](#nw.storage.FolderStorage). That is what lets a
host that only knows a project’s path (reelee opening a guest genre’s project)
reach a genre’s graph wherever the genre keeps it.

```pycon
>>> from lacing import MemoryStore
>>> s = MappingStorage(root=".", graph={}, docs={}, store_factory=lambda m: MemoryStore())
>>> as_project_storage(s) is s
True
```

### Module Attributes

| [`DFLT_INIT_FOLDERS`](#nw.storage.DFLT_INIT_FOLDERS)   | The subfolders [`nw.Project.init()`](nw.html.md#nw.Project.init) creates in a [`FolderStorage`](#nw.storage.FolderStorage) project: the music-video layout nw started with.   |
|----------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`project_storages`](#nw.storage.project_storages)    | Storage resolvers, `name -> (root: Path) -> ProjectStorage | None`.                                                                                                                                                              |

### Functions

| [`as_project_storage`](#nw.storage.as_project_storage)(project)              | The [`ProjectStorage`](#nw.storage.ProjectStorage) for a path, a storage, or an `nw.Project`.   |
|-------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------|
| [`asset_id_of`](#nw.storage.asset_id_of)(root, spec)                  | The asset id anchoring a project's annotations.                                                                  |
| [`json_docs`](#nw.storage.json_docs)(root)                          | The folder-backed store for a project's JSON documents.                                                          |
| [`register_project_storage`](#nw.storage.register_project_storage)(name, resolver) | Register a resolver that recognises a genre's project folders.                                                   |

### Classes

| [`FolderStorage`](#nw.storage.FolderStorage)(root, \*[, init_folders])      | nw's historical layout: everything under `root`.                      |
|-----------------------------------------------------------------------------------------------|-----------------------------------------------------------------------|
| [`MappingStorage`](#nw.storage.MappingStorage)(\*, root, graph, docs[, ...]) | A project whose graph and documents live in mappings the caller owns. |
| [`ProjectStorage`](#nw.storage.ProjectStorage)(\*args, \*\*kwargs)           | Where one project's documents and annotation stores live.             |

### nw.storage.DFLT_INIT_FOLDERS *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), ...]* *= ('characters', 'environments', 'shots', 'output', 'lyrics', 'script', 'song', '.nw')*

The subfolders [`nw.Project.init()`](nw.html.md#nw.Project.init) creates in a [`FolderStorage`](#nw.storage.FolderStorage)
project: the music-video layout nw started with. A genre with another layout
passes its own `init_folders`.

### *class* nw.storage.FolderStorage(root, , init_folders=('characters', 'environments', 'shots', 'output', 'lyrics', 'script', 'song', '.nw'))

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

nw’s historical layout: everything under `root`.

`project.json` and the other documents are JSON files under `root`;
the graph is `root/project.annot.sqlite` (or a Postgres tenant, by
[`nw.graph_backend`](nw.graph_backend.html.md#module-nw.graph_backend)); the storyboard and lyric-alignment scopes are
their legacy files. Legacy (pre-graph) projects are migrated on open by
[`nw.Project`](nw.html.md#nw.Project), which is why `migrates_legacy` is true here.

#### scope_paths()

`{scope_name: legacy_sqlite_path}` for this project’s stores.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)]

### *class* nw.storage.MappingStorage(\*, root, graph, docs, scopes=(), store_factory=<function \_dflt_store_factory>, init_folders=(), asset_id=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

A project whose graph and documents live in mappings the caller owns.

* **Parameters:**
  * **root** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – The project’s folder, for files (media, renders) and for the
    asset id. nw writes no document or store file here.
  * **graph** ([`MutableMapping`](https://docs.python.org/3/library/typing.html#typing.MutableMapping)) – The mapping the graph store persists into.
  * **docs** ([`MutableMapping`](https://docs.python.org/3/library/typing.html#typing.MutableMapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]) – The mapping holding nw’s JSON documents (`project.json`, …).
  * **scopes** ([`Mapping`](https://docs.python.org/3/library/typing.html#typing.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`MutableMapping`](https://docs.python.org/3/library/typing.html#typing.MutableMapping)]) – Further annotation scopes, `{scope_name: mapping}`; walked
    by `open_stores()` after the graph.
  * **store_factory** ([`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`MutableMapping`](https://docs.python.org/3/library/typing.html#typing.MutableMapping)], `IntervalAnnotationStore`]) – `mapping -> IntervalAnnotationStore`. Defaults to
    `lacing.store.MappingStore`. One store object is kept per
    mapping, so every open sees the same in-memory index; the
    underlying store has no cross-process lock, so one writer at a
    time.
  * **init_folders** ([`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis)]) – Subfolders [`nw.Project.init()`](nw.html.md#nw.Project.init) creates under
    `root` (none by default: the genre owns its layout).
  * **asset_id** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Fix the asset id instead of deriving it from `root` and
    the project’s title.

### *class* nw.storage.ProjectStorage(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

Where one project’s documents and annotation stores live.

`open_graph` creates the graph store if needed and ensures nw’s tiers.
`open_graph_readonly` must not write, and raises `FileNotFoundError`
when there is no graph yet. `open_stores` yields every annotation scope
the project has (graph included), each store closed before the next opens.

### nw.storage.as_project_storage(project)

The [`ProjectStorage`](#nw.storage.ProjectStorage) for a path, a storage, or an `nw.Project`.

A path goes to the first registered resolver that recognises it, else to
[`FolderStorage`](#nw.storage.FolderStorage).

* **Return type:**
  [`ProjectStorage`](#nw.storage.ProjectStorage)

### nw.storage.asset_id_of(root, spec)

The asset id anchoring a project’s annotations.

The SHA-256 of the registered song’s bytes when there is one on disk,
else a stable hash of the project’s resolved root and title.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### nw.storage.json_docs(root)

The folder-backed store for a project’s JSON documents.

A `dol` `MutableMapping` keyed by project-relative POSIX path
(`"project.json"`, `"characters/<name>/card.json"`,
`"shots/<id>/shot.json"`, the `".nw/migrated_to_graph"` sentinel).
Values are Python objects; on disk they are `indent=2` JSON. Parent
directories are created on write; a missing key raises `KeyError`.

* **Return type:**
  [`MutableMapping`](https://docs.python.org/3/library/typing.html#typing.MutableMapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]

### nw.storage.project_storages *: Registry* *= <Registry nw.project_storages>*

Storage resolvers, `name -> (root: Path) -> ProjectStorage | None`. Asked
in registration order by [`as_project_storage()`](#nw.storage.as_project_storage); the first non-`None`
answer wins. `on_conflict="error"`, so two genres cannot silently claim
one name.

### nw.storage.register_project_storage(name, resolver)

Register a resolver that recognises a genre’s project folders.

The resolver gets a resolved folder path and returns the
[`ProjectStorage`](#nw.storage.ProjectStorage) for it, or `None` when the folder is not one of
its projects. It must be cheap and must not write: hosts call it for every
project they list.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/typing.html#typing.Callable)[[[`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)], [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`ProjectStorage`](#nw.storage.ProjectStorage)]]
