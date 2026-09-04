"""A read of the project graph must not take a write lock.

``ProjectGraph``'s own docstring promises that "concurrent reads/writes from
different processes are safe (SqliteStore is file-locked)".
``open_project_graph`` broke that promise for readers: it calls
``_ensure_tiers``, which issues an ``add_tier`` per tier on **every** open, and
that is a write.

Measured with a writer holding ``BEGIN IMMEDIATE`` on one project's graph:
``SqliteStore(path)`` opened in 0.19 ms while ``open_project_graph`` raised
``OperationalError`` after 5.4 s. Through the consumer that motivated this —
listing sibling projects, each of which reads a genre envelope — the same
contention cost **5429 ms** and then silently dropped the contended project's
metadata, because the caller catches per project. Slow *and* lossy, and slow
enough to be mistaken for a hang in some other layer.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from nw import Project
from nw.migrate import (
    open_project_graph,
    open_project_graph_readonly,
    project_graph_db_path,
)


@pytest.fixture
def project(tmp_path):
    p = Project.init(tmp_path / "p", title="Read Test")
    open_project_graph(p.root).close()  # materialize the graph db
    return p


def test_a_read_does_not_wait_for_a_writer(project):
    """The bug, at the level a user feels it.

    Not a tuning problem: before the fix this call waited out the busy timeout
    and *then* failed. The assertion is deliberately generous — anything near
    the multi-second timeout means the read is contending again.
    """
    db = str(project_graph_db_path(project.root))
    blocker = sqlite3.connect(db)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        start = time.perf_counter()
        envelope = project.graph.genre_envelope()
        elapsed = time.perf_counter() - start
    finally:
        blocker.rollback()
        blocker.close()

    assert elapsed < 1.0, f"the read contended with the writer ({elapsed:.2f}s)"
    assert envelope is None or envelope is not None  # it answered at all


def test_every_typed_reader_survives_a_concurrent_writer(project):
    """One reader fixed is not the property; all of them is.

    ``_open_read`` is shared, so this is really asserting that no reader was
    left on the write-locking open — the kind of thing a partial sweep leaves
    behind and nobody notices until that one surface is the slow one.
    """
    db = str(project_graph_db_path(project.root))
    blocker = sqlite3.connect(db)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        start = time.perf_counter()
        readers = (
            project.graph.sections(),
            project.graph.shots(),
            project.graph.character_refs(),
            project.graph.environment_refs(),
            project.graph.decisions(),
        )
        elapsed = time.perf_counter() - start
    finally:
        blocker.rollback()
        blocker.close()

    assert all(r == [] for r in readers)
    assert elapsed < 1.0, f"a typed reader still takes a write lock ({elapsed:.2f}s)"


def test_reading_a_project_with_no_graph_creates_nothing(tmp_path):
    """A read of a project with no store answers empty, and leaves it that way.

    Creating a database to discover it is empty is the write this exists to
    avoid — the same defect shape as minting metadata on a read.
    """
    p = Project.init(tmp_path / "fresh", title="Fresh")
    db = project_graph_db_path(p.root)
    if db.exists():
        db.unlink()

    assert p.graph.sections() == []
    assert p.graph.genre_envelope() is None

    assert not db.exists(), "a read created the graph database"


def test_the_readonly_open_refuses_to_create(tmp_path):
    """The primitive says so plainly rather than quietly making a file."""
    p = Project.init(tmp_path / "fresh", title="Fresh")
    db = project_graph_db_path(p.root)
    if db.exists():
        db.unlink()

    with pytest.raises(FileNotFoundError):
        open_project_graph_readonly(p.root)
    assert not db.exists()


def test_writes_still_work_and_are_still_readable(project):
    """The negative control.

    A change that made every read return empty would pass all of the above.
    """
    from nw import SectionSpec

    project.upsert_section(SectionSpec(id="verse", start_s=0.0, end_s=10.0))

    sections = project.graph.sections()
    assert [s.body.section_id for s in sections] == ["verse"]
