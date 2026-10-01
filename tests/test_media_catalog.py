"""The host media-catalog writer (nw#92): one copy, pinned to the host's contract.

braidio and muvid each carried this writer; these tests are the union of what
each copy promised, so both can delegate here without losing a guarantee.
"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from nw.media_catalog import (
    BACKEND_ENV_KEY,
    CatalogBackendMismatch,
    CatalogReport,
    CatalogRow,
    CorruptBlob,
    CrossDeviceCatalog,
    HostArtifactCatalog,
    catalog_row,
    hash_file,
)


@pytest.fixture(autouse=True)
def _local_backend(monkeypatch):
    monkeypatch.delenv(BACKEND_ENV_KEY, raising=False)


@pytest.fixture
def project(tmp_path) -> Path:
    root = tmp_path / "project"
    (root / "media").mkdir(parents=True)
    return root


def _media(project: Path, name: str = "clip.mp4", data: bytes = b"\x00mp4" * 64):
    p = project / "media" / name
    p.write_bytes(data)
    return p


# --- what makes an id resolvable ----------------------------------------------


def test_registering_makes_the_content_hash_resolvable(project):
    media = _media(project)
    cat = HostArtifactCatalog(project)

    aid = cat.register(media, kind="video", duration_s=2.5)

    assert aid == hash_file(media)
    assert cat.has(aid)
    assert aid in cat.registered_ids()
    (row,) = cat.iter_rows()
    assert row["id"] == row["content_hash"] == aid
    assert row["duration_seconds"] == 2.5
    assert row["url"] == f"/api/artifacts/{aid}/bytes"


def test_a_row_never_carries_a_local_path(project):
    aid = HostArtifactCatalog(project).register(_media(project), kind="video")
    text = (project / ".reelee/artifacts/catalog" / f"{aid}.json").read_text()
    assert "file://" not in text
    assert str(project) not in text


def test_the_blob_is_a_hardlink_not_a_copy(project):
    media = _media(project)
    cat = HostArtifactCatalog(project)
    report = CatalogReport()
    aid = cat.register(media, kind="video", report=report)
    assert os.stat(cat.blobs_dir / aid).st_ino == os.stat(media).st_ino
    assert (report.blobs_linked, report.blobs_copied) == (1, 0)


def test_the_digest_is_the_one_lacing_computes(project):
    """The id is a lacing ``asset_id``; if they disagreed, every row would sit
    under an id the graph does not use."""
    from lacing.artifact import hash_file as lacing_hash

    media = _media(project, data=b"some bytes" * 1000)
    assert hash_file(media) == lacing_hash(media)


# --- re-registration does not churn -------------------------------------------


def test_reregistering_unchanged_media_rewrites_nothing(project):
    media = _media(project)
    cat = HostArtifactCatalog(project)
    aid = cat.register(media, kind="video", generated_at="2026-01-01T00:00:00Z")
    row_path = cat.rows_dir / f"{aid}.json"
    before = row_path.read_text()

    report = CatalogReport()
    cat.register(
        media, kind="video", generated_at="2027-01-01T00:00:00Z", report=report
    )

    assert row_path.read_text() == before  # first registration's stamp kept
    assert (report.rows_unchanged, report.rows_written, report.blobs_present) == (
        1,
        0,
        1,
    )


def test_a_changed_row_is_rewritten_but_keeps_its_first_stamp(project):
    media = _media(project)
    cat = HostArtifactCatalog(project)
    aid = cat.register(media, kind="video", generated_at="2026-01-01T00:00:00Z")
    cat.register(
        media,
        kind="video",
        width=1920,
        height=1080,
        generated_at="2027-01-01T00:00:00Z",
    )
    (row,) = cat.iter_rows()
    assert (row["id"], row["width"], row["height"]) == (aid, 1920, 1080)
    assert row["provenance"]["generated_at"] == "2026-01-01T00:00:00Z"


def test_blob_path_is_how_a_reader_finds_the_bytes(project):
    media = _media(project)
    cat = HostArtifactCatalog(project)
    aid = cat.register(media, kind="video")
    assert cat.blob_path(aid).read_bytes() == media.read_bytes()
    assert cat.blob_path("ab" * 32) is None
    assert cat.blob_path("") is None and not cat.has("")


def test_options_are_keyword_only(project):
    with pytest.raises(TypeError):
        HostArtifactCatalog(project, True)


# --- refusals -------------------------------------------------------------------


def test_a_kind_the_host_cannot_hold_is_reported_not_coerced(project):
    sidecar = _media(project, "subs.srt", b"1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    report = CatalogReport()
    assert (
        HostArtifactCatalog(project).register(sidecar, kind="text", report=report)
        is None
    )
    assert report.unregistered and "text" in report.unregistered[0][1]
    assert not (project / ".reelee").exists()


@pytest.mark.parametrize("bad", ["ab" * 31, "AB" * 32, "ab" * 32 + "\n", "../x"])
def test_an_id_that_is_not_a_digest_is_refused(project, bad):
    with pytest.raises(ValueError, match="hex digest"):
        HostArtifactCatalog(project).register(
            _media(project), kind="video", artifact_id=bad
        )


def test_an_object_store_host_is_refused(project, monkeypatch):
    monkeypatch.setenv(BACKEND_ENV_KEY, "aws")
    with pytest.raises(CatalogBackendMismatch):
        HostArtifactCatalog(project).register(_media(project), kind="video")
    assert not (project / ".reelee").exists()


@pytest.mark.parametrize("value", ["fs", "local", "FS", " "])
def test_backends_the_host_reads_as_local_are_not_refused(project, monkeypatch, value):
    """The host treats anything but ``aws`` as its filesystem backend."""
    monkeypatch.setenv(BACKEND_ENV_KEY, value)
    assert HostArtifactCatalog(project).register(_media(project), kind="video")


def test_the_refusal_names_the_callers_own_remedy():
    from nw.media_catalog import assert_local_backend

    with pytest.raises(CatalogBackendMismatch, match="register_artifacts=False"):
        assert_local_backend(
            {BACKEND_ENV_KEY: "aws"}, remedy="pass register_artifacts=False"
        )


def test_a_caller_that_checked_the_backend_once_can_skip_the_check(
    project, monkeypatch
):
    monkeypatch.setenv(BACKEND_ENV_KEY, "aws")
    cat = HostArtifactCatalog(project, check_backend=False)
    assert cat.register(_media(project), kind="video")


def _refuse_links(monkeypatch, code=errno.EXDEV):
    def _link(src, dst):
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(os, "link", _link)


def test_an_unlinkable_destination_refuses_rather_than_copying(project, monkeypatch):
    _refuse_links(monkeypatch)
    cat = HostArtifactCatalog(project)
    with pytest.raises(CrossDeviceCatalog):
        cat.register(_media(project), kind="video")
    assert cat.registered_ids() == frozenset()  # no row without its blob


def test_a_copy_happens_only_when_paid_for(project, monkeypatch):
    _refuse_links(monkeypatch)
    media = _media(project)
    report = CatalogReport()
    aid = HostArtifactCatalog(project, allow_cross_device_copy=True).register(
        media, kind="video", report=report
    )
    assert report.cross_device and report.blobs_copied == 1
    assert report.bytes_copied == media.stat().st_size
    assert HostArtifactCatalog(project).has(aid)


def test_a_copy_that_fails_half_way_leaves_no_blob(project, monkeypatch):
    """Every later run trusts what is under a digest, so a short file must never be."""
    import shutil

    _refuse_links(monkeypatch)
    media = _media(project)

    def _short_copy(src, dst, **kw):
        Path(dst).write_bytes(b"short")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(shutil, "copy2", _short_copy)
    cat = HostArtifactCatalog(project, allow_cross_device_copy=True)
    with pytest.raises(OSError):
        cat.register(media, kind="video")
    assert list(cat.blobs_dir.iterdir()) == []
    assert cat.registered_ids() == frozenset()


def test_a_stored_blob_that_cannot_be_the_bytes_is_refused(project):
    media = _media(project)
    cat = HostArtifactCatalog(project)
    aid = hash_file(media)
    cat.blobs_dir.mkdir(parents=True)
    (cat.blobs_dir / aid).write_bytes(b"truncated")
    with pytest.raises(CorruptBlob):
        cat.register(media, kind="video")
    assert cat.registered_ids() == frozenset()


def test_losing_a_race_to_store_the_same_bytes_is_not_an_error(project, monkeypatch):
    media = _media(project)
    cat = HostArtifactCatalog(project)
    real_link = os.link

    def _racing_link(src, dst):
        real_link(src, dst)  # the other registration got there first
        raise FileExistsError(errno.EEXIST, "File exists")

    monkeypatch.setattr(os, "link", _racing_link)
    report = CatalogReport()
    aid = cat.register(media, kind="video", report=report)
    assert cat.has(aid) and report.blobs_present == 1


def test_media_outside_the_project_is_refused(project, tmp_path):
    outside = tmp_path / "elsewhere.wav"
    outside.write_bytes(b"RIFF" * 32)
    link = project / "media" / "link.wav"
    link.symlink_to(outside)
    cat = HostArtifactCatalog(project)
    for path in (outside, link):
        with pytest.raises(ValueError, match="outside the project"):
            cat.register(path, kind="audio")
    assert not cat.blobs_dir.exists()


def test_a_real_link_error_propagates(project, monkeypatch):
    _refuse_links(monkeypatch, errno.EACCES)
    with pytest.raises(PermissionError):
        HostArtifactCatalog(project, allow_cross_device_copy=True).register(
            _media(project), kind="video"
        )


# --- the wire contract ------------------------------------------------------------


def test_the_row_emits_exactly_the_fields_it_declares():
    """Changing the emitted shape must be a deliberate two-place edit.

    This compares nw against nw, so it cannot see drift in the host's model —
    only the host-model test below can. What it buys: ``filename`` stays OUT
    (the newest field in the host's model; emitting it fails an older host's
    whole index), and the builder cannot grow a key silently.
    """
    assert CatalogRow.FIELDS == (
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
    assert CatalogRow.PROVENANCE_FIELDS == (
        "source",
        "model",
        "request_id",
        "prompt",
        "generated_at",
        "triggered_by",
    )
    row = catalog_row("ab" * 32, kind="image", generated_at="2026-01-01T00:00:00Z")
    assert tuple(sorted(row)) == tuple(sorted(CatalogRow.FIELDS))
    assert tuple(sorted(row["provenance"])) == tuple(
        sorted(CatalogRow.PROVENANCE_FIELDS)
    )
    json.dumps(row)


def _host_importable() -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec("reelee") is not None
    except (ImportError, ValueError):
        return False


@pytest.mark.skipif(
    not _host_importable(), reason="the host (reelee) is not importable"
)
def test_rows_load_through_the_hosts_own_model(tmp_path):
    """The wire contract, checked against the authority rather than a copy.

    nw cannot depend on the host — the dependency runs the other way — so this
    is a dev-machine check by nature, and the only one that can catch drift in
    the host's model. It runs in a subprocess because importing the host
    registers the host's own body schemas and Transforms, which would leak into
    every later test in this process.
    """
    import subprocess
    import sys
    import textwrap

    repo = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(
        f"""
        import pathlib
        import nw.media_catalog
        from reelee.artifacts import Artifact, ArtifactRepository
        from nw.media_catalog import HostArtifactCatalog
        here = pathlib.Path(nw.media_catalog.__file__).resolve()
        assert pathlib.Path({str(repo)!r}) in here.parents, here  # this checkout
        root = pathlib.Path({str(tmp_path)!r})
        cat = HostArtifactCatalog(root)
        ids = []
        for kind in ("image", "video", "audio", "json"):
            media = root / f"m.{{kind}}"
            media.write_bytes(kind.encode() * 64)
            ids.append(cat.register(media, kind=kind, width=1, height=1, note="n"))
        for row in cat.iter_rows():
            record = Artifact.model_validate(row)
            assert record.id == record.content_hash
            assert record.url.startswith("/api/artifacts/")
        # The host's own read path: its index and its blob store, not only its model.
        host = ArtifactRepository.for_project(root, principal="t")
        index = host.index()
        assert set(index) == set(ids), (sorted(index), ids)
        assert all(host.store.has_blob(aid) for aid in ids)
        print(len(index))
        """
    )
    # PYTHONPATH, not cwd: with PYTHONSAFEPATH set, cwd puts nothing on sys.path
    # and the subprocess would silently import whichever nw is installed.
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, BACKEND_ENV_KEY: "", "PYTHONPATH": str(repo)},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "4"
