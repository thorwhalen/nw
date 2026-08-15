"""Job records are written atomically — a reader never sees a partial file.

reelee#298: the job index is rewritten by the worker thread on every mirrored
event (and on reads, for the pct floor) while the API polls ``get_job``
concurrently. With a truncate-then-write store a read could land between
``open(..., "w")`` and the bytes — ``json.loads("")`` — surfacing as an
intermittent 500 exactly during a cancel, when clients poll hardest.
``nw.jobs`` now writes its two durable JSON stores (index, durations) via
``_AtomicJsonFiles``: serialize fully, write a sibling temp file,
``os.replace`` it over the destination.

The tests pin the three properties the fix consists of: a concurrent reader
observes only complete records; a failed write changes nothing on disk; the
on-disk layout is byte-compatible with the ``dol.JsonFiles`` it replaced, so
existing project job stores keep working.
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from dol import JsonFiles

import nw.jobs as jobs
from nw import Project
from nw.jobs import _AtomicJsonFiles


@pytest.fixture(autouse=True)
def _fresh_runtimes():
    jobs._reset_runtimes()
    yield
    jobs._reset_runtimes()


# --- basic mapping contract + on-disk compatibility ---------------------------


def test_round_trips_and_stays_layout_compatible_with_json_files(tmp_path):
    """Same layout as the dol.JsonFiles it replaced: existing stores keep working."""
    atomic = _AtomicJsonFiles(tmp_path / "store")
    legacy = JsonFiles(str(tmp_path / "store"))

    atomic["written-atomically"] = {"n": 1}
    legacy["written-by-dol"] = {"n": 2}

    assert legacy["written-atomically"] == {"n": 1}  # dol reads ours
    assert atomic["written-by-dol"] == {"n": 2}  # we read dol's
    assert sorted(atomic) == ["written-atomically", "written-by-dol"]
    assert len(atomic) == 2
    assert "written-atomically" in atomic and "absent" not in atomic
    assert atomic.get("absent") is None

    del atomic["written-by-dol"]
    assert sorted(atomic) == ["written-atomically"]
    with pytest.raises(KeyError):
        atomic["written-by-dol"]
    with pytest.raises(KeyError):
        del atomic["written-by-dol"]


def test_unsafe_keys_are_refused_not_written_elsewhere(tmp_path):
    store = _AtomicJsonFiles(tmp_path / "store")
    for bad in ("", "a/b", "..", ".hidden"):
        with pytest.raises(ValueError):
            store[bad] = {"n": 1}


# --- atomicity: the reelee#298 property ---------------------------------------


def test_a_failed_write_leaves_the_old_record_and_no_temp_files(tmp_path):
    """Serialization happens before any disk IO, so a bad value changes nothing."""
    store = _AtomicJsonFiles(tmp_path / "store")
    store["job"] = {"status": "running"}

    with pytest.raises(TypeError):
        store["job"] = {"status": object()}  # not JSON-serializable

    assert store["job"] == {"status": "running"}
    leftovers = [p.name for p in (tmp_path / "store").iterdir() if p.name != "job"]
    assert leftovers == [], "a failed write must not leave temp files behind"


def test_a_concurrent_reader_never_observes_a_partial_record(tmp_path):
    """The race from reelee#298, run hot: reads see old-or-new, never truncated.

    With the truncate-then-write store this replaced, this test fails within a
    few hundred iterations (``json.loads("")``); with ``os.replace`` it cannot.
    """
    store = _AtomicJsonFiles(tmp_path / "store")
    # A payload big enough that a truncate-then-write would give readers a
    # wide window of partial content.
    payloads = [{"rev": rev, "pad": "x" * 20_000} for rev in range(2)]
    store["job"] = payloads[0]

    stop = threading.Event()
    problems: list[str] = []

    def writer():
        rev = 0
        while not stop.is_set():
            rev += 1
            store["job"] = payloads[rev % 2]

    def reader():
        while not stop.is_set():
            try:
                record = store["job"]
            except (json.JSONDecodeError, KeyError) as exc:
                problems.append(repr(exc))
                return
            if record not in payloads:
                problems.append(f"partial record observed: keys={sorted(record)}")
                return

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    time.sleep(0.35)
    stop.set()
    for t in threads:
        t.join(timeout=5)

    assert problems == []
    # And the writer's churn left no temp debris for iteration to list as keys.
    assert list(store) == ["job"]


# --- the runtime actually uses it ---------------------------------------------


def test_job_runtime_stores_are_atomic(tmp_path):
    """The wiring is the fix: both durable JSON stores go through the atomic path."""
    project = Project.init(tmp_path / "proj", title="Atomic")
    rt = jobs._runtime(project)
    assert isinstance(rt.index, _AtomicJsonFiles)
    assert isinstance(rt.durations, _AtomicJsonFiles)
