# nw.media_catalog

Make a genre project’s media *retrievable* from its host — the host’s artifact catalog.

A still, a recording or a render that lands in a genre’s project is a file on disk,
recorded in the graph by its content-addressed id. The host serves media through
`GET /api/artifacts/{id}/bytes`, which answers only for ids registered in the
project’s catalog. Until something registers it, asking for an id the graph holds
answers **404**: a project that looks complete in every census and delivers nothing.

This module is that registration, written once (nw#92). braidio’s importer and
muvid’s hosted productions each carried a copy of it; two copies of a wire contract
whose row shape belongs to the host is how they drift. nw is where every genre
already meets the host (project factories, genre ops, [`nw.delivery`](nw.delivery.md#module-nw.delivery)), so the
writer lives here and the genres call it.

Two writes make an id resolvable, and the order between them is the whole
correctness argument:

1. the **blob** lands at `blobs/<sha256>` — the filename *is* the digest;
2. the **row** lands at `catalog/<sha256>.json`, second and only once the blob is
   there, because the host’s route reads the row first: a row with no blob behind
   it is not a 404, it is a 500 from inside a stream that already promised a 200.

Three decisions that are the point rather than detail:

- **The id IS the content hash** (SHA-256 of the bytes, 64 lowercase hex). The graph
  already recorded `artifact_id=<sha256>` in every body that names the file, so
  registering under any other id yields a populated catalog and a project that still
  404s on every id it holds. It is the digest lacing computes (pinned by a test).
- **Blobs are HARDLINKED, never copied.** The media is already inside the project; a
  copy would double a 300 MB production on the server’s disk. The link is safe, not
  merely cheap, *because* the name is the digest: a shared inode can only ever be
  rewritten with identical bytes. A destination that cannot link **refuses**
  ([`CrossDeviceCatalog`](#nw.media_catalog.CrossDeviceCatalog)) instead of quietly copying gigabytes, unless the
  caller asks to pay for the copy (`allow_cross_device_copy=True`).
- \*\*A row’s `url` is never a `file://` path.\*\* It is the host’s own bytes route.
  A row registered by hand with a `file://` url once reached an `<img src>` and
  put the owner’s home directory into a page’s DOM: a local path in a served record
  is a disclosure, and it is also wrong — the consumer is not on this machine.

**Writing another package’s record shape.** The layout is lacing’s
(`ArtifactStore.from_directory`: a `catalog/` of JSON documents beside a
`blobs/` of content-addressed files), but the row *shape* belongs to the host
that reads it, and the host validates with `extra="forbid"`. nw cannot import the
host (the dependency runs the other way), so the shape is carried here as a declared
**wire contract** — [`CatalogRow`](#nw.media_catalog.CatalogRow) — and `tests/test_media_catalog.py`
validates rows through the host’s own model whenever the host is importable.

**One refusal guards the whole module**: [`assert_local_backend()`](#nw.media_catalog.assert_local_backend). Rows written
next to a project whose host reads its artifacts from an object store would make
every id 404 while the write reported success — the defect this module exists to
remove, reintroduced one layer up. [`HostArtifactCatalog`](#nw.media_catalog.HostArtifactCatalog) checks it on every
registration unless told the caller already has.

**What comes next** is nw#95: one content-addressed store shared by every genre and
the host, replacing each project’s private `blobs/`. Its design is open; when it
lands it changes where [`HostArtifactCatalog`](#nw.media_catalog.HostArtifactCatalog) writes, not who calls it.

### Module Attributes

| [`DELIVERY_CATALOG_SUBPATH`](#nw.media_catalog.DELIVERY_CATALOG_SUBPATH)                         | Where the host keeps a project's artifact store, relative to the project root: lacing's layout, the host's directory name.                  |
|---------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------|
| [`CATALOG_KINDS`](#nw.media_catalog.CATALOG_KINDS)                                    | a row whose `kind` the host's `Literal` does not name fails validation at read time, which turns one unservable file into a broken catalog. |
| [`BYTES_ROUTE`](#nw.media_catalog.BYTES_ROUTE)                                      | The route the host serves a registered artifact from.                                                                                       |
| [`BACKEND_ENV_KEY`](#nw.media_catalog.BACKEND_ENV_KEY)                                  | The host's backend switch.                                                                                                                  |
| [`catalog_row`](#nw.media_catalog.catalog_row)(artifact_id, \*, kind, generated_at) | The function spelling of [`CatalogRow.build()`](#nw.media_catalog.CatalogRow.build).                                               |

### Functions

| [`assert_local_backend`](#nw.media_catalog.assert_local_backend)([env, remedy])              | Refuse to register into a filesystem the host will not read.                                  |
|---------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------|
| [`catalog_row`](#nw.media_catalog.catalog_row)(artifact_id, \*, kind, generated_at) | The function spelling of [`CatalogRow.build()`](#nw.media_catalog.CatalogRow.build). |
| [`hash_file`](#nw.media_catalog.hash_file)(path, \*[, chunk_size])                | SHA-256 of a file's bytes, read in chunks — the catalog id of that file.                      |

### Classes

| [`CatalogReport`](#nw.media_catalog.CatalogReport)([rows_written, ...])           | What registering a batch of media did, and what the catalog could not hold.   |
|-----------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| [`CatalogRow`](#nw.media_catalog.CatalogRow)()                                 | The host's artifact record, as JSON — a wire contract, not a model.           |
| [`HostArtifactCatalog`](#nw.media_catalog.HostArtifactCatalog)(project_root, \*[, ...]) | The artifact catalog of the project at `project_root`, in the host's layout.  |

### Exceptions

| [`CatalogBackendMismatch`](#nw.media_catalog.CatalogBackendMismatch)   | The host reads its artifacts from somewhere other than the project's blobs.   |
|---------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| [`CorruptBlob`](#nw.media_catalog.CorruptBlob)              | A blob already stored under a digest does not hold the bytes it names.        |
| [`CrossDeviceCatalog`](#nw.media_catalog.CrossDeviceCatalog)       | The project's blob store cannot hardlink the media (another filesystem).      |

### nw.media_catalog.BACKEND_ENV_KEY *= 'REELEE_ARTIFACT_BACKEND'*

The host’s backend switch. Read, never written. The host treats every value
other than these as its local backend (an unrecognised value falls back to the
filesystem), so only these are refused — refusing more would block a write the
host would in fact read.

### nw.media_catalog.BYTES_ROUTE *= '/api/artifacts/{artifact_id}/bytes'*

The route the host serves a registered artifact from. A row’s `url` is this,
never a local path.

### nw.media_catalog.CATALOG_KINDS *: [frozenset](https://docs.python.org/3/builtins/stdtypes.html#frozenset)[[str](https://docs.python.org/3/builtins/stdtypes.html#str)]* *= frozenset({'audio', 'image', 'json', 'video'})*

a row
whose `kind` the host’s `Literal` does not name fails validation at read time,
which turns one unservable file into a broken catalog. A `text` artifact (an
`.srt` sidecar) has no home here; it is reported unregistered, never coerced.

* **Type:**
  Content categories the host’s catalog can hold. Deliberately not extended

### *exception* nw.media_catalog.CatalogBackendMismatch

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

The host reads its artifacts from somewhere other than the project’s blobs.

### *class* nw.media_catalog.CatalogReport(rows_written=0, rows_unchanged=0, blobs_linked=0, blobs_present=0, blobs_copied=0, bytes_copied=0, cross_device=False, unregistered=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

What registering a batch of media did, and what the catalog could not hold.

#### unregistered *: [list](https://docs.python.org/3/builtins/stdtypes.html#list)[[tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [str](https://docs.python.org/3/builtins/stdtypes.html#str)]]*

`(path, reason)` for a file the catalog cannot hold — today only a kind
outside [`CATALOG_KINDS`](#nw.media_catalog.CATALOG_KINDS). Reported, never silently dropped: an
unregistered artifact is one a caller will ask for and not get.

### *class* nw.media_catalog.CatalogRow

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The host’s artifact record, as JSON — a wire contract, not a model.

Deliberately not a pydantic model: this is *the host’s* schema, and nw has no
business owning a second authority for it. It is a dict-builder with every
field spelled out, so a failure to load on the host’s side shows up as a diff
against these lists rather than as an absence.

#### FIELDS *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), ...]* *= ('id', 'kind', 'url', 'width', 'height', 'duration_seconds', 'cost_usd', 'provenance', 'content_hash')*

Every key a row EMITS, in the host’s own order.

An omission is safe and an addition is fatal, which is the opposite of how it
first reads. Every field the host declares is optional except `id`,
`kind`, `url` and `provenance.generated_at`, so a key left out takes its
default — while a key the host does not know fails validation for the
**whole catalog**, because the host’s index validates every row. One unknown
key does not cost one artifact; it costs all of them. So: emit the minimum,
and only fields that have been in the host’s model long enough to be
everywhere.

#### PROVENANCE_FIELDS *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), ...]* *= ('source', 'model', 'request_id', 'prompt', 'generated_at', 'triggered_by')*

it is
the newest field in the host’s model (2026-09-19) and emitting it would make
every row unreadable by an older host, whole catalog at a time. The cost is a
download named after its id instead of a friendly name. Put it back once every
host this writes for is reliably newer than that date.

* **Type:**
  The provenance keys a row emits. `filename` is deliberately ABSENT

#### *static* build(artifact_id, , kind, generated_at, width=None, height=None, duration_s=None, note='')

One record, ready to serialize.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

```pycon
>>> row = CatalogRow.build("ab" * 32, kind="image",
...     generated_at="2026-09-20T00:00:00Z", width=800, height=600)
>>> row["id"] == row["content_hash"] == "ab" * 32
True
>>> row["url"]
'/api/artifacts/abababababababababababababababababababababababababababababababab/bytes'
>>> sorted(row) == sorted(CatalogRow.FIELDS)
True
```

### *exception* nw.media_catalog.CorruptBlob

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

A blob already stored under a digest does not hold the bytes it names.

### *exception* nw.media_catalog.CrossDeviceCatalog

Bases: [`OSError`](https://docs.python.org/3/builtins/exceptions.html#OSError)

The project’s blob store cannot hardlink the media (another filesystem).

### nw.media_catalog.DELIVERY_CATALOG_SUBPATH *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), ...]* *= ('.reelee', 'artifacts')*

Where the host keeps a project’s artifact store, relative to the project root:
lacing’s layout, the host’s directory name.

### *class* nw.media_catalog.HostArtifactCatalog(project_root, , allow_cross_device_copy=False, check_backend=True, subpath=('.reelee', 'artifacts'))

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The artifact catalog of the project at `project_root`, in the host’s layout.

`allow_cross_device_copy` pays for a copy where a hardlink is impossible
(refused by default). `check_backend=False` is for a caller that already
called [`assert_local_backend()`](#nw.media_catalog.assert_local_backend) once for a whole batch. `subpath` is
where the host keeps the store inside a project.

A reader asks [`blob_path()`](#nw.media_catalog.HostArtifactCatalog.blob_path) for an artifact’s bytes rather than joining
onto `blobs_dir` itself: the per-project `blobs/` directory is today’s
store, not a promise (nw#95 designs a shared one).

```pycon
>>> import tempfile, pathlib
>>> with tempfile.TemporaryDirectory() as d:
...     media = pathlib.Path(d, "song.wav"); _ = media.write_bytes(b"RIFF")
...     cat = HostArtifactCatalog(d)
...     aid = cat.register(media, kind="audio")
...     cat.has(aid), aid in cat.registered_ids(), cat.blob_path(aid).name == aid
(True, True, True)
```

#### blob_path(artifact_id)

Where `artifact_id`’s bytes are, or `None` if they are not stored.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)]

#### has(artifact_id)

Whether `artifact_id` resolves: its row AND its blob are both there.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

#### iter_rows()

Every row, parsed, in id order. For tests and diagnostics.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

#### register(path, , kind, artifact_id=None, generated_at=None, width=None, height=None, duration_s=None, note='', report=None)

Register `path` (a file inside the project); return its id, or `None`.

`None` only for a `kind` the catalog cannot hold, which `report` (when
given) records; the caller keeps the file without an id rather than
inventing one. Pass `artifact_id` when the file is already hashed (a
300 MB clip is not worth reading twice): its *shape* is checked, because the
host validates the same shape and a row written under anything else is
accepted here and refused there; its value is trusted. `generated_at` is
when the artifact was first registered (default: now) and never moves once
a row exists; re-registering an unchanged artifact does not rewrite its row.

The file must resolve inside the project. A hardlink shares an inode, so
linking a file that lives elsewhere (directly, or through a symlink) would
let a rewrite out there change the bytes under a digest that names them.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

#### registered_ids()

Every artifact id this catalog currently has a row for.

* **Return type:**
  [`frozenset`](https://docs.python.org/3/builtins/stdtypes.html#frozenset)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### nw.media_catalog.assert_local_backend(env=None, , remedy='skip registration deliberately')

Refuse to register into a filesystem the host will not read.

Everything else here fails loudly — a missing blob raises, an unlinkable
destination raises, an unholdable kind is reported. Writing a correct
`catalog/` and `blobs/` next to a project whose host resolves artifacts out
of an object store would instead report complete success and leave every id
404ing. A caller that wants the graph without the catalog skips registration
explicitly; it does not get it by accident. `remedy` lets a caller name its
own way of doing that in the message (an importer’s opt-out flag, say).

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

```pycon
>>> assert_local_backend({})
>>> assert_local_backend({"REELEE_ARTIFACT_BACKEND": "fs"})
>>> assert_local_backend({"REELEE_ARTIFACT_BACKEND": "local"})  # host reads fs
>>> assert_local_backend({"REELEE_ARTIFACT_BACKEND": "aws"})
Traceback (most recent call last):
    ...
nw.media_catalog.CatalogBackendMismatch: ...
```

### nw.media_catalog.catalog_row(artifact_id, , kind, generated_at, width=None, height=None, duration_s=None, note='')

The function spelling of [`CatalogRow.build()`](#nw.media_catalog.CatalogRow.build).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### nw.media_catalog.hash_file(path, , chunk_size=1048576)

SHA-256 of a file’s bytes, read in chunks — the catalog id of that file.

The same digest lacing computes for an `asset_id` (pinned by a test), kept
here so the writer can be reasoned about without the graph layer.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> import tempfile, pathlib
>>> with tempfile.TemporaryDirectory() as d:
...     p = pathlib.Path(d, "x"); _ = p.write_bytes(b"abc")
...     hash_file(p)
'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
```
