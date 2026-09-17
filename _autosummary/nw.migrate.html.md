# nw.migrate

Idempotent migration: project.json (sections/shots/refs) → lacing graph.

Pre-graph nw projects (and all the_bells_v\* fixtures from the muvid era)
keep sections, shots, characters, and environments in `project.json` as
arrays. From Phase 3 forward, those live in a per-project lacing annotation
store so reelee can walk the graph for freshness analysis, provenance
queries, and view rendering. The backend is chosen by [`nw.graph_backend`](nw.graph_backend.html.md#module-nw.graph_backend)
— a per-project `SqliteStore` file by default, or a shared Postgres DB when
`NW_GRAPH_BACKEND=postgres` (Phase 4, reelee#177).

This module’s job is to bridge the two formats \*without losing data and
without requiring the user to do anything\*. [`migrate_to_graph()`](#nw.migrate.migrate_to_graph):

- Is idempotent — running it twice is a no-op.
- Only writes the graph; the original `project.json` is left in place
  (and trimmed of fields the graph now owns, with the original kept under
  `.nw/project.json.pre-graph.bak` so a downgrade is possible).
- Marks completion via a `.nw/migrated_to_graph` sentinel file.

Project-level metadata (title, song, global_style, notes, schema_version)
stays in `project.json`. Sections, shots, characters, environments,
decisions move into the graph.

### Functions

| [`is_migrated`](#nw.migrate.is_migrated)(project_root)                         | True iff this project has been migrated to the lacing graph.             |
|----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| [`migrate_to_graph`](#nw.migrate.migrate_to_graph)(project_root, \*[, backup, ...]) | Migrate `project_root`'s project.json into the lacing graph.             |
| [`open_project_graph`](#nw.migrate.open_project_graph)(project_root)                  | Open (and create on first call) the project's graph store, with tiers.   |
| [`open_project_graph_readonly`](#nw.migrate.open_project_graph_readonly)(project_root)         | Open the project's graph store to **read**, without taking a write lock. |
| [`project_asset_id`](#nw.migrate.project_asset_id)(project_root)                    | The asset_id used as the project's graph anchor.                         |
| `project_graph_db_path`(project_root)                                                              |                                                                          |

### nw.migrate.is_migrated(project_root)

True iff this project has been migrated to the lacing graph.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### nw.migrate.migrate_to_graph(project_root, , backup=True, was_attributed_to='agent:nw.migrate')

Migrate `project_root`’s project.json into the lacing graph.

Idempotent: returns `{"already_migrated": 1, ...}` with zero writes if
the sentinel exists. Otherwise reads `project.json`, writes equivalent
annotations into `project.annot.sqlite`, drops the migrated arrays from
`project.json`, and writes the sentinel.

* **Parameters:**
  * **project_root** ([`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – Path to a project root.
  * **backup** ([`bool`](https://docs.python.org/3/builtins/functions.html#bool)) – When True (default), copy the original `project.json` to
    `.nw/project.json.pre-graph.bak` before trimming.
  * **was_attributed_to** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Provenance for the migrator. Defaults to
    `"agent:nw.migrate"`; pass `"user:<handle>"` from a CLI.
* **Returns:**
  ```
  ``
  ```

  {“sections”: N, “shots”: N, “characters”: N, “environments”: N,
  : ”decisions”: N}\`\`.
* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`int`](https://docs.python.org/3/builtins/functions.html#int)]

### nw.migrate.open_project_graph(project_root)

Open (and create on first call) the project’s graph store, with tiers.

The backend (SQLite file by default, or a shared Postgres DB when
`NW_GRAPH_BACKEND=postgres`) is resolved by [`nw.graph_backend`](nw.graph_backend.html.md#module-nw.graph_backend) — the
single config-driven seam. Callers get an `IntervalAnnotationStore` and
never learn which backend answered.

* **Return type:**
  `IntervalAnnotationStore`

### nw.migrate.open_project_graph_readonly(project_root)

Open the project’s graph store to **read**, without taking a write lock.

[`open_project_graph()`](#nw.migrate.open_project_graph) calls `_ensure_tiers()`, which issues an
`add_tier` per project tier on *every* open. That is a write, so a reader
contended with any live writer — and on SQLite it does not merely wait.
Measured with a writer holding `BEGIN IMMEDIATE`:

| `SqliteStore(path)`        | OK in **0.19 ms**                  |
|----------------------------|------------------------------------|
| `open_project_graph(root)` | `OperationalError` after **5.4 s** |

**This read does not merely block — it raises**, and it raises slowly
enough to be mistaken for a hang and chased in the wrong layer. Phrasing
it as efficiency would invite a revert; phrasing it as blocking would
invite a busy-timeout tweak. The failure is categorical.

That matters the moment anything fans out across projects while a render
is running. Measured through the consumer that motivated it — listing
sibling projects, each of which reads a genre envelope — one contended
project cost **5429 ms** and *then* lost its metadata, because the caller
catches per project. Through this path the same call is **1.33 ms** and
keeps it.

Two deliberate differences from the read-write open, both following from
“a read does not write”:

- **No tier creation.** The store is opened as it is on disk. A tier that
  is missing simply has no annotations to return, which is the honest
  answer to a reader.
- **No migration.** [`open_project_graph()`](#nw.migrate.open_project_graph) passes `migrate=True` so a
  file written under an older lacing schema upgrades on open. Upgrading is
  a write, and a surface that silently migrated every project it *listed*
  would be the same class of defect as one that created metadata for them.
  `ProjectGraph` falls back to the read-write open for such a file, so
  the rare case pays the lock and the common case does not.

In Postgres mode this delegates to the ordinary open: MVCC readers do not
block writers, so the problem being solved is SQLite-specific and inventing
a second path there would add a seam with nothing behind it.

* **Raises:**
  [**FileNotFoundError**](https://docs.python.org/3/builtins/exceptions.html#FileNotFoundError) – in SQLite mode, when the project has no graph
      database yet. Creating one is a write; a reader is told plainly
      that there is nothing to read rather than quietly making it.
* **Return type:**
  `IntervalAnnotationStore`

### nw.migrate.project_asset_id(project_root)

The asset_id used as the project’s graph anchor.

For projects with a song registered in project.json, this is the SHA-256
of the song bytes. Otherwise a stable fallback derived from the title.

Mirrors [`nw.storyboard.project_asset_id()`](nw.storyboard.html.md#nw.storyboard.project_asset_id) so the project graph and
the storyboard share an asset_id.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)
