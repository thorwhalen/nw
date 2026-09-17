# nw.graph_backend

Config-driven backend selection for nw’s annotation graph stores.

Phase 4 of the storage migration (reelee#177). A nw project keeps its
annotations in lacing `IntervalAnnotationStore``s — historically one
``SqliteStore` file per *scope* (the project graph, the storyboard, the
lyrics alignment). This module is the **single seam** that decides whether a
given scope is backed by SQLite (the default — byte-for-byte the old
behaviour, one file per scope) or by a shared Postgres database
(`lacing.store.PostgresStore`, tenant-scoped).

## The facade principle in action

Every nw site that needs a graph store — [`nw.migrate.open_project_graph()`](nw.migrate.html.md#nw.migrate.open_project_graph),
the storyboard load/save, the lyrics-alignment read, and the provenance walk in
[`nw.graph`](nw.graph.html.md#module-nw.graph) — routes through [`open_graph_store()`](#nw.graph_backend.open_graph_store) /
[`iter_scope_stores()`](#nw.graph_backend.iter_scope_stores) here. None of them learns *which* backend answered;
[`nw.graph.ProjectGraph`](nw.graph.html.md#nw.graph.ProjectGraph) and every typed accessor are unchanged.

## The environment contract

| Variable            | Meaning                                                                                                                                                   |
|---------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------|
| `NW_GRAPH_BACKEND`  | `sqlite` (**default**) | `postgres`. Unknown /<br/>empty → `sqlite`.                                                                                      |
| `NW_GRAPH_DB_URL`   | (postgres) psycopg conninfo URL for the shared DB.                                                                                                        |
| `NW_GRAPH_OWNER_ID` | (postgres, optional) tenant owner; defaults to<br/>lacing’s `DEFAULT_OWNER_ID`. Forward seam for the<br/>access layer (reelee#174); enforcement deferred. |

**Safety first.** The default (no env, or any unrecognized backend) is always
SQLite — *identical* to the behaviour before this module existed. A local run
never changes and never crashes because Postgres env happens to be unset; if
`NW_GRAPH_BACKEND=postgres` but `NW_GRAPH_DB_URL` is missing, we log a
warning and fall back to SQLite rather than failing.

## Tenant scoping across scopes

In SQLite mode each scope is a distinct file, so they never collide. In
Postgres mode they share tables, so each scope gets a distinct `project_id`
built from the project’s stable `project_asset_id` and the scope name:
`"<asset_id>:<scope>"`. [`iter_scope_stores()`](#nw.graph_backend.iter_scope_stores) enumerates exactly the same
scope set the SQLite walk did, so [`nw.graph.iter_all_annotations()`](nw.graph.html.md#nw.graph.iter_all_annotations) yields
each annotation once under either backend.

### Functions

| [`selected_backend`](#nw.graph_backend.selected_backend)([env])                        | Resolve the configured graph backend from the environment.              |
|-------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------|
| [`open_graph_store`](#nw.graph_backend.open_graph_store)(db_path, \*, asset_id[, ...]) | Open the annotation store for one scope, backend chosen by env.         |
| [`iter_scope_stores`](#nw.graph_backend.iter_scope_stores)(scope_paths, \*, asset_id)   | Yield an iterator of open stores, one per scope, backend chosen by env. |
| [`scope_name_for_db`](#nw.graph_backend.scope_name_for_db)(db_path)                     | Map a legacy per-scope SQLite filename to its scope name.               |

### Classes

| [`GraphBackend`](#nw.graph_backend.GraphBackend)   |    |
|-----------------------------------------------------------------|----|

### nw.graph_backend.GraphBackend

alias of [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### nw.graph_backend.iter_scope_stores(scope_paths, , asset_id, env=None)

Yield an iterator of open stores, one per scope, backend chosen by env.

The provenance walk in [`nw.graph.iter_all_annotations()`](nw.graph.html.md#nw.graph.iter_all_annotations) needs *every*
store under a project. In SQLite mode that’s “every existing per-scope
file”; in Postgres mode it’s “every scope’s tenant” — and this generator
enumerates exactly the same scope set under both backends, so each
annotation is yielded once either way.

* **Parameters:**
  * **scope_paths** ([`Mapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)]) – `{scope_name: legacy_sqlite_path}` — only paths that
    *exist* on disk are visited in SQLite mode; in Postgres mode every
    listed scope is visited (existence is a DB question, not a file
    one).
  * **asset_id** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – The project’s `project_asset_id` (Postgres tenant anchor).
  * **env** ([`Mapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)] | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Environment mapping. Defaults to `os.environ`.
* **Yields:**
  A single iterator that produces each scope’s open store in turn. Each
  store is closed before the next is opened, so callers must consume
  annotations eagerly per store (which the walk does).
* **Return type:**
  [*Iterator*](https://docs.python.org/3/library/typing.html#typing.Iterator)[[*Iterator*](https://docs.python.org/3/library/typing.html#typing.Iterator)[*IntervalAnnotationStore*]]

### nw.graph_backend.open_graph_store(db_path, , asset_id, scope=None, rate=None, env=None)

Open the annotation store for one scope, backend chosen by env.

The single place that decides SQLite-vs-Postgres for a graph store. Callers
pass the legacy SQLite path (still the source of truth for *where* the file
lives in SQLite mode) plus the project’s `asset_id` (the tenant anchor in
Postgres mode).

* **Parameters:**
  * **db_path** ([`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path) | [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – The per-scope SQLite path (used directly in SQLite mode; in
    Postgres mode only its filename is used to derive the scope).
  * **asset_id** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – The project’s stable `project_asset_id` — the Postgres
    tenant anchor. Ignored in SQLite mode.
  * **scope** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – The logical scope name (`"graph"` / `"storyboard"` /
    `"alignment"` / …). Defaults to deriving it from `db_path`.
  * **rate** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`int`](https://docs.python.org/3/builtins/functions.html#int)]) – Project-wide rate for the Postgres store. Defaults to lacing’s
    `DEFAULT_RATE`. Ignored in SQLite mode.
  * **env** ([`Mapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)] | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Environment mapping. Defaults to `os.environ`.
* **Return type:**
  `IntervalAnnotationStore`
* **Returns:**
  A live `IntervalAnnotationStore` — `SqliteStore`
  (default) or a tenant-scoped `PostgresStore`. Caller closes it (or
  uses it as a context manager).

### nw.graph_backend.scope_name_for_db(db_path)

Map a legacy per-scope SQLite filename to its scope name.

`project.annot.sqlite` → `"graph"`; `storyboard.annot.sqlite` →
`"storyboard"`; `alignment.annot` → `"alignment"`. Anything else maps
to the file’s stem so a new store kind gets a stable scope automatically.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### nw.graph_backend.selected_backend(env=None)

Resolve the configured graph backend from the environment.

Pure and side-effect-free. Any unrecognized / empty value resolves to
`"sqlite"` — the safe default that never changes a local run.

* **Parameters:**
  **env** ([`Mapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)] | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – Environment mapping to read. Defaults to `os.environ`.
* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)
* **Returns:**
  `"sqlite"` or `"postgres"`.
