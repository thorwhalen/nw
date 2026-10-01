"""Make a genre project's media *retrievable* from its host — the host's artifact catalog.

A still, a recording or a render that lands in a genre's project is a file on disk,
recorded in the graph by its content-addressed id. The host serves media through
``GET /api/artifacts/{id}/bytes``, which answers only for ids registered in the
project's catalog. Until something registers it, asking for an id the graph holds
answers **404**: a project that looks complete in every census and delivers nothing.

This module is that registration, written once (nw#92). braidio's importer and
muvid's hosted productions each carried a copy of it; two copies of a wire contract
whose row shape belongs to the host is how they drift. nw is where every genre
already meets the host (project factories, genre ops, :mod:`nw.delivery`), so the
writer lives here and the genres call it.

Two writes make an id resolvable, and the order between them is the whole
correctness argument:

1. the **blob** lands at ``blobs/<sha256>`` — the filename *is* the digest;
2. the **row** lands at ``catalog/<sha256>.json``, second and only once the blob is
   there, because the host's route reads the row first: a row with no blob behind
   it is not a 404, it is a 500 from inside a stream that already promised a 200.

Three decisions that are the point rather than detail:

- **The id IS the content hash** (SHA-256 of the bytes, 64 lowercase hex). The graph
  already recorded ``artifact_id=<sha256>`` in every body that names the file, so
  registering under any other id yields a populated catalog and a project that still
  404s on every id it holds. It is the digest lacing computes (pinned by a test).
- **Blobs are HARDLINKED, never copied.** The media is already inside the project; a
  copy would double a 300 MB production on the server's disk. The link is safe, not
  merely cheap, *because* the name is the digest: a shared inode can only ever be
  rewritten with identical bytes. A destination that cannot link **refuses**
  (:class:`CrossDeviceCatalog`) instead of quietly copying gigabytes, unless the
  caller asks to pay for the copy (``allow_cross_device_copy=True``).
- **A row's ``url`` is never a ``file://`` path.** It is the host's own bytes route.
  A row registered by hand with a ``file://`` url once reached an ``<img src>`` and
  put the owner's home directory into a page's DOM: a local path in a served record
  is a disclosure, and it is also wrong — the consumer is not on this machine.

**Writing another package's record shape.** The layout is lacing's
(``ArtifactStore.from_directory``: a ``catalog/`` of JSON documents beside a
``blobs/`` of content-addressed files), but the row *shape* belongs to the host
that reads it, and the host validates with ``extra="forbid"``. nw cannot import the
host (the dependency runs the other way), so the shape is carried here as a declared
**wire contract** — :class:`CatalogRow` — and ``tests/test_media_catalog.py``
validates rows through the host's own model whenever the host is importable.

**One refusal guards the whole module**: :func:`assert_local_backend`. Rows written
next to a project whose host reads its artifacts from an object store would make
every id 404 while the write reported success — the defect this module exists to
remove, reintroduced one layer up. :class:`HostArtifactCatalog` checks it on every
registration unless told the caller already has.

**What comes next** is nw#95: one content-addressed store shared by every genre and
the host, replacing each project's private ``blobs/``. Its design is open; when it
lands it changes where :class:`HostArtifactCatalog` writes, not who calls it.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import KW_ONLY, dataclass, field
from pathlib import Path
from typing import Iterator, Mapping, Optional

#: Where the host keeps a project's artifact store, relative to the project root:
#: lacing's layout, the host's directory name.
DELIVERY_CATALOG_SUBPATH: tuple[str, ...] = (".reelee", "artifacts")
_CATALOG_DIRNAME = "catalog"
_BLOBS_DIRNAME = "blobs"

#: Content categories the host's catalog can hold. Deliberately not extended: a row
#: whose ``kind`` the host's ``Literal`` does not name fails validation at read time,
#: which turns one unservable file into a broken catalog. A ``text`` artifact (an
#: ``.srt`` sidecar) has no home here; it is reported unregistered, never coerced.
CATALOG_KINDS: frozenset[str] = frozenset({"image", "video", "audio", "json"})

#: The route the host serves a registered artifact from. A row's ``url`` is this,
#: never a local path.
BYTES_ROUTE = "/api/artifacts/{artifact_id}/bytes"

#: The host's backend switch. Read, never written. The host treats every value
#: other than these as its local backend (an unrecognised value falls back to the
#: filesystem), so only these are refused — refusing more would block a write the
#: host would in fact read.
BACKEND_ENV_KEY = "REELEE_ARTIFACT_BACKEND"
_OBJECT_STORE_BACKENDS = frozenset({"aws"})

#: ``os.link`` failures that mean "this filesystem cannot make the link", as opposed
#: to a real error: a different device, the link ceiling, a filesystem that refuses
#: links outright. Everything else propagates. Same allow-list as the host's own
#: project fork.
_LINK_REFUSALS = (errno.EXDEV, errno.EMLINK, errno.EPERM)

#: The host's ``content_hash`` validator, mirrored: 64 lowercase hex characters and
#: nothing else. Always ``fullmatch`` — ``$`` also matches before a trailing newline.
_IS_DIGEST = re.compile(r"[0-9a-f]{64}")
_HASH_CHUNK = 1 << 20


class CrossDeviceCatalog(OSError):
    """The project's blob store cannot hardlink the media (another filesystem)."""


class CatalogBackendMismatch(RuntimeError):
    """The host reads its artifacts from somewhere other than the project's blobs."""


class CorruptBlob(RuntimeError):
    """A blob already stored under a digest does not hold the bytes it names."""


def assert_local_backend(
    env: Optional[Mapping[str, str]] = None,
    *,
    remedy: str = "skip registration deliberately",
) -> None:
    """Refuse to register into a filesystem the host will not read.

    Everything else here fails loudly — a missing blob raises, an unlinkable
    destination raises, an unholdable kind is reported. Writing a correct
    ``catalog/`` and ``blobs/`` next to a project whose host resolves artifacts out
    of an object store would instead report complete success and leave every id
    404ing. A caller that wants the graph without the catalog skips registration
    explicitly; it does not get it by accident. ``remedy`` lets a caller name its
    own way of doing that in the message (an importer's opt-out flag, say).

    >>> assert_local_backend({})
    >>> assert_local_backend({"REELEE_ARTIFACT_BACKEND": "fs"})
    >>> assert_local_backend({"REELEE_ARTIFACT_BACKEND": "local"})  # host reads fs
    >>> assert_local_backend({"REELEE_ARTIFACT_BACKEND": "aws"})
    Traceback (most recent call last):
        ...
    nw.media_catalog.CatalogBackendMismatch: ...
    """
    backend = ((os.environ if env is None else env).get(BACKEND_ENV_KEY) or "").strip()
    if backend.lower() not in _OBJECT_STORE_BACKENDS:
        return
    raise CatalogBackendMismatch(
        f"{BACKEND_ENV_KEY}={backend!r}: the host resolves artifacts from an object "
        "store, not from the project's own blobs/ directory, so rows written here "
        "would be invisible and every artifact id would still 404 — with the write "
        "reporting success. Register with the backend unset (or 'fs') and upload "
        f"afterwards, or {remedy}."
    )


def hash_file(path, *, chunk_size: int = _HASH_CHUNK) -> str:
    """SHA-256 of a file's bytes, read in chunks — the catalog id of that file.

    The same digest lacing computes for an ``asset_id`` (pinned by a test), kept
    here so the writer can be reasoned about without the graph layer.

    >>> import tempfile, pathlib
    >>> with tempfile.TemporaryDirectory() as d:
    ...     p = pathlib.Path(d, "x"); _ = p.write_bytes(b"abc")
    ...     hash_file(p)
    'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


class CatalogRow:
    """The host's artifact record, as JSON — a wire contract, not a model.

    Deliberately not a pydantic model: this is *the host's* schema, and nw has no
    business owning a second authority for it. It is a dict-builder with every
    field spelled out, so a failure to load on the host's side shows up as a diff
    against these lists rather than as an absence.
    """

    #: Every key a row EMITS, in the host's own order.
    #:
    #: An omission is safe and an addition is fatal, which is the opposite of how it
    #: first reads. Every field the host declares is optional except ``id``,
    #: ``kind``, ``url`` and ``provenance.generated_at``, so a key left out takes its
    #: default — while a key the host does not know fails validation for the
    #: **whole catalog**, because the host's index validates every row. One unknown
    #: key does not cost one artifact; it costs all of them. So: emit the minimum,
    #: and only fields that have been in the host's model long enough to be
    #: everywhere.
    FIELDS: tuple[str, ...] = (
        "id",
        "kind",
        "url",
        "width",
        "height",
        "duration_seconds",
        "cost_usd",
        "provenance",
        "content_hash",
    )
    #: The provenance keys a row emits. ``filename`` is deliberately ABSENT: it is
    #: the newest field in the host's model (2026-09-19) and emitting it would make
    #: every row unreadable by an older host, whole catalog at a time. The cost is a
    #: download named after its id instead of a friendly name. Put it back once every
    #: host this writes for is reliably newer than that date.
    PROVENANCE_FIELDS: tuple[str, ...] = (
        "source",
        "model",
        "request_id",
        "prompt",
        "generated_at",
        "triggered_by",
    )

    @staticmethod
    def build(
        artifact_id: str,
        *,
        kind: str,
        generated_at: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
        duration_s: Optional[float] = None,
        note: str = "",
    ) -> dict:
        """One record, ready to serialize.

        >>> row = CatalogRow.build("ab" * 32, kind="image",
        ...     generated_at="2026-09-20T00:00:00Z", width=800, height=600)
        >>> row["id"] == row["content_hash"] == "ab" * 32
        True
        >>> row["url"]
        '/api/artifacts/abababababababababababababababababababababababababababababababab/bytes'
        >>> sorted(row) == sorted(CatalogRow.FIELDS)
        True
        """
        return {
            "id": artifact_id,
            "kind": kind,
            # NEVER a file:// path. See the module docstring.
            "url": BYTES_ROUTE.format(artifact_id=artifact_id),
            "width": width,
            "height": height,
            "duration_seconds": duration_s,
            "cost_usd": None,
            "provenance": {
                # "manual" is the host's value for "a person put these bytes here",
                # which is what registering a genre's media is. The alternatives
                # name generation vendors and would be a false claim.
                "source": "manual",
                "model": None,
                "request_id": None,
                "prompt": note or None,
                "generated_at": generated_at,
                "triggered_by": None,
            },
            "content_hash": artifact_id,
        }


#: The function spelling of :meth:`CatalogRow.build`.
catalog_row = CatalogRow.build


@dataclass
class CatalogReport:
    """What registering a batch of media did, and what the catalog could not hold."""

    rows_written: int = 0
    rows_unchanged: int = 0
    blobs_linked: int = 0
    blobs_present: int = 0
    blobs_copied: int = 0
    bytes_copied: int = 0
    cross_device: bool = False
    #: ``(path, reason)`` for a file the catalog cannot hold — today only a kind
    #: outside :data:`CATALOG_KINDS`. Reported, never silently dropped: an
    #: unregistered artifact is one a caller will ask for and not get.
    unregistered: list[tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rows_written": self.rows_written,
            "rows_unchanged": self.rows_unchanged,
            "blobs_linked": self.blobs_linked,
            "blobs_present": self.blobs_present,
            "blobs_copied": self.blobs_copied,
            "bytes_copied": self.bytes_copied,
            "cross_device": self.cross_device,
            "unregistered": [list(u) for u in self.unregistered],
        }


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass(frozen=True)
class HostArtifactCatalog:
    """The artifact catalog of the project at ``project_root``, in the host's layout.

    ``allow_cross_device_copy`` pays for a copy where a hardlink is impossible
    (refused by default). ``check_backend=False`` is for a caller that already
    called :func:`assert_local_backend` once for a whole batch. ``subpath`` is
    where the host keeps the store inside a project.

    A reader asks :meth:`blob_path` for an artifact's bytes rather than joining
    onto :attr:`blobs_dir` itself: the per-project ``blobs/`` directory is today's
    store, not a promise (nw#95 designs a shared one).

    >>> import tempfile, pathlib
    >>> with tempfile.TemporaryDirectory() as d:
    ...     media = pathlib.Path(d, "song.wav"); _ = media.write_bytes(b"RIFF")
    ...     cat = HostArtifactCatalog(d)
    ...     aid = cat.register(media, kind="audio")
    ...     cat.has(aid), aid in cat.registered_ids(), cat.blob_path(aid).name == aid
    (True, True, True)
    """

    project_root: Path
    _: KW_ONLY
    allow_cross_device_copy: bool = False
    check_backend: bool = True
    subpath: tuple[str, ...] = DELIVERY_CATALOG_SUBPATH

    def __post_init__(self):
        object.__setattr__(self, "project_root", Path(self.project_root))

    @property
    def root(self) -> Path:
        return self.project_root.joinpath(*self.subpath)

    @property
    def blobs_dir(self) -> Path:
        return self.root / _BLOBS_DIRNAME

    @property
    def rows_dir(self) -> Path:
        return self.root / _CATALOG_DIRNAME

    def blob_path(self, artifact_id: str) -> Optional[Path]:
        """Where ``artifact_id``'s bytes are, or ``None`` if they are not stored."""
        if not _IS_DIGEST.fullmatch(artifact_id or ""):
            return None
        blob = self.blobs_dir / artifact_id
        return blob if blob.is_file() else None

    def has(self, artifact_id: str) -> bool:
        """Whether ``artifact_id`` resolves: its row AND its blob are both there."""
        return (
            self.blob_path(artifact_id) is not None
            and (self.rows_dir / f"{artifact_id}.json").is_file()
        )

    def registered_ids(self) -> frozenset[str]:
        """Every artifact id this catalog currently has a row for."""
        if not self.rows_dir.is_dir():
            return frozenset()
        return frozenset(p.stem for p in self.rows_dir.glob("*.json"))

    def iter_rows(self) -> Iterator[dict]:
        """Every row, parsed, in id order. For tests and diagnostics."""
        for p in sorted(self.rows_dir.glob("*.json")):
            yield json.loads(p.read_text(encoding="utf-8"))

    def register(
        self,
        path,
        *,
        kind: str,
        artifact_id: Optional[str] = None,
        generated_at: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        duration_s: Optional[float] = None,
        note: str = "",
        report: Optional[CatalogReport] = None,
    ) -> Optional[str]:
        """Register ``path`` (a file inside the project); return its id, or ``None``.

        ``None`` only for a ``kind`` the catalog cannot hold, which ``report`` (when
        given) records; the caller keeps the file without an id rather than
        inventing one. Pass ``artifact_id`` when the file is already hashed (a
        300 MB clip is not worth reading twice): its *shape* is checked, because the
        host validates the same shape and a row written under anything else is
        accepted here and refused there; its value is trusted. ``generated_at`` is
        when the artifact was first registered (default: now) and never moves once
        a row exists; re-registering an unchanged artifact does not rewrite its row.

        The file must resolve inside the project. A hardlink shares an inode, so
        linking a file that lives elsewhere (directly, or through a symlink) would
        let a rewrite out there change the bytes under a digest that names them.
        """
        report = report if report is not None else CatalogReport()
        src = Path(path)
        if artifact_id is not None:
            _check_digest(artifact_id)
        if kind not in CATALOG_KINDS:
            report.unregistered.append(
                (str(src), f"kind {kind!r} is not one the catalog can hold")
            )
            return None
        if self.check_backend:
            assert_local_backend()
        self._check_inside_project(src)
        aid = artifact_id if artifact_id is not None else hash_file(src)
        _check_digest(aid)
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.rows_dir.mkdir(parents=True, exist_ok=True)

        self._store_blob(src, aid, report)

        # The row LAST, and only now the blob is there (see the module docstring).
        row_path = self.rows_dir / f"{aid}.json"
        existing = _read(row_path) if row_path.exists() else None
        first_registered = ((existing or {}).get("provenance") or {}).get(
            "generated_at"
        )
        row = CatalogRow.build(
            aid,
            kind=kind,
            generated_at=first_registered or generated_at or _now_iso(),
            width=width,
            height=height,
            duration_s=duration_s,
            note=note,
        )
        if existing is not None and _same_but_for_stamp(existing, row):
            report.rows_unchanged += 1
            return aid
        # Atomic: the host indexes every row, and a half-written one would fail
        # its whole catalog, not this artifact.
        _atomic_write_text(row_path, json.dumps(row, indent=2))
        report.rows_written += 1
        return aid

    def _check_inside_project(self, src: Path) -> None:
        root = self.project_root.resolve()
        resolved = src.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError(
                f"{src} resolves to {resolved}, outside the project {root}. The "
                "catalog hardlinks its blobs, so registering a file that lives "
                "elsewhere would let a change out there rewrite the bytes under a "
                "digest that names them. Copy it into the project first."
            )

    def _store_blob(self, src: Path, aid: str, report: CatalogReport) -> None:
        """Put ``src``'s bytes under ``blobs/<aid>``, or confirm they are there."""
        blob = self.blobs_dir / aid
        if blob.exists():
            self._confirm_blob(blob, src, aid)
            report.blobs_present += 1
            return
        try:
            os.link(src, blob)
        except FileExistsError:
            # A concurrent registration of the same bytes won the race.
            self._confirm_blob(blob, src, aid)
            report.blobs_present += 1
            return
        except OSError as exc:
            if exc.errno not in _LINK_REFUSALS:
                raise
            if not self.allow_cross_device_copy:
                raise CrossDeviceCatalog(
                    exc.errno,
                    f"cannot hardlink {src.name} into {blob.parent}: the project's "
                    "blob store is not on the same filesystem as the media. The "
                    "catalog shares bytes by inode, so this would silently copy the "
                    "media a second time. Keep the project on the media's "
                    "filesystem, or pass allow_cross_device_copy=True to pay for "
                    "the copy deliberately.",
                ) from exc
            # Copy beside the blob, then rename into place: a copy that fails
            # half-way must never leave a short file under a digest, because
            # every later run trusts whatever is there.
            tmp = _tmp_sibling(blob)
            try:
                shutil.copy2(src, tmp)
                os.replace(tmp, blob)
            finally:
                tmp.unlink(missing_ok=True)
            report.blobs_copied += 1
            report.bytes_copied += blob.stat().st_size
            report.cross_device = True
            return
        report.blobs_linked += 1

    @staticmethod
    def _confirm_blob(blob: Path, src: Path, aid: str) -> None:
        """Refuse a stored blob that cannot be ``src``'s bytes (a cheap size check).

        A blob is trusted by its name; a size mismatch means that trust is broken
        (an interrupted write from an older writer, a hand edit), and serving it
        would answer 200 with the wrong bytes under a live id.
        """
        if blob.stat().st_size != src.stat().st_size:
            raise CorruptBlob(
                f"blobs/{aid} is {blob.stat().st_size} bytes but {src} is "
                f"{src.stat().st_size}: the stored blob does not hold the bytes its "
                "name claims. Remove it and register again."
            )


def _check_digest(aid: str) -> None:
    if not _IS_DIGEST.fullmatch(aid or ""):
        raise ValueError(
            f"artifact_id {aid!r} is not a 64-character lowercase hex digest. "
            "The catalog is content-addressed and the host validates the same "
            "shape, so a row written under any other id is accepted here and "
            "refused there."
        )


def _tmp_sibling(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = _tmp_sibling(path)
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _read(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _same_but_for_stamp(a: Optional[dict], b: dict) -> bool:
    """Two rows equal, ignoring when they were registered.

    ``generated_at`` is when an artifact was FIRST registered, so a re-registration
    must not move it — and comparing it would make every re-import rewrite every
    row.

    >>> row = catalog_row("ab" * 32, kind="image", generated_at="2026-01-01T00:00:00Z")
    >>> later = catalog_row("ab" * 32, kind="image", generated_at="2027-06-06T12:00:00Z")
    >>> _same_but_for_stamp(row, later)
    True
    >>> _same_but_for_stamp(row, catalog_row("cd" * 32, kind="image",
    ...     generated_at="2026-01-01T00:00:00Z"))
    False
    >>> _same_but_for_stamp(None, row)
    False
    """

    def _strip(row: dict) -> dict:
        out = dict(row)
        prov = dict(out.get("provenance") or {})
        prov.pop("generated_at", None)
        out["provenance"] = prov
        return out

    return a is not None and _strip(a) == _strip(b)


__all__ = [
    "BACKEND_ENV_KEY",
    "BYTES_ROUTE",
    "CATALOG_KINDS",
    "DELIVERY_CATALOG_SUBPATH",
    "CatalogBackendMismatch",
    "CatalogReport",
    "CatalogRow",
    "CorruptBlob",
    "CrossDeviceCatalog",
    "HostArtifactCatalog",
    "assert_local_backend",
    "catalog_row",
    "hash_file",
]
