"""Offline tests for :mod:`nw.jobs` — the async render-job facade over ``au``.

Everything here uses a **stub render callable** (no real fal, no network). The
stub emits the same shape of lifecycle events reelee's render will, so the
progress/cost/eta/cancel machinery is exercised without spending money.
"""

import threading
import time

import pytest

from nw import Project
import nw.jobs as jobs
from nw.jobs import JobsConfig


# ---------------------------------------------------------------------------
# fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_runtimes():
    jobs._reset_runtimes()
    yield
    jobs._reset_runtimes()


@pytest.fixture
def project(tmp_path):
    return Project.init(tmp_path / "proj", title="Jobs Test")


VIDEO_PARAMS = {"model": "fal-ai/kling", "operation": "image_to_video", "duration_s": 6}


def _poll_until(project, job_id, predicate, *, timeout=5.0, config=jobs.DEFAULT_CONFIG):
    """Poll ``get_job`` until ``predicate(job)`` or timeout; return the last job."""
    deadline = time.time() + timeout
    job = jobs.get_job(project, job_id, config=config)
    while time.time() < deadline:
        if job is not None and predicate(job):
            return job
        time.sleep(0.01)
        job = jobs.get_job(project, job_id, config=config)
    return job


def _blocking_stub(release: threading.Event, *, calls=None, cached=False):
    """A stub render that blocks on ``release`` then completes, emitting events."""

    def stub(project, params, *, job_id, on_event, should_cancel):
        if calls is not None:
            calls.append(job_id)
        on_event(
            {
                "kind": "journey.stage_started",
                "stage_index": 1,
                "stage_count": 3,
                "current_transform": "panel_to_image",
                "event_id": "ev-start",
            }
        )
        released = release.wait(timeout=5)
        if should_cancel():
            # honor a cancel at the boundary (worker will still write COMPLETED,
            # but cancel_requested makes the job read `cancelled`)
            return {"journey_status": "halted"}
        if cached:
            on_event({"kind": "cache_hit"})
        on_event(
            {
                "kind": "journey.stage_completed",
                "stage_index": 3,
                "actual_usd": 0.42,
                "cache_hit_savings_usd": 0.10,
                "animatic_artifact_id": "artif-123",
                "event_id": "ev-done",
            }
        )
        return {
            "journey_status": "completed",
            "animatic_artifact_id": "artif-123",
            "cost_usd_actual": 0.42,
            "cache_hit_savings_usd": 0.10,
            "_released": released,
        }

    return stub


# ---------------------------------------------------------------------------
# enqueue → poll → terminal
# ---------------------------------------------------------------------------


def test_enqueue_returns_queued_immediately(project):
    release = threading.Event()
    dispatch = {"journey.full_auto": _blocking_stub(release)}
    events = []
    job = jobs.enqueue(
        project, "journey.full_auto", VIDEO_PARAMS,
        on_event=events.append, dispatch=dispatch,
    )
    # Immediate: a real id, not yet terminal.
    assert job.job_id and job.status in (jobs.QUEUED, jobs.RUNNING)
    assert job.run_id == job.job_id
    assert job.label  # derived from kind/model
    assert job.eta_key == "fal-ai/kling|image_to_video|5-8s"
    release.set()
    done = _poll_until(project, job.job_id, lambda j: j.status in jobs.TERMINAL_STATUSES)
    assert done.status == jobs.SUCCEEDED


def test_full_lifecycle_success_carries_artifact_and_cost(project):
    release = threading.Event()
    dispatch = {"journey.full_auto": _blocking_stub(release)}
    events = []
    job = jobs.enqueue(
        project, "journey.full_auto", VIDEO_PARAMS,
        on_event=events.append, dispatch=dispatch,
    )
    running = _poll_until(project, job.job_id, lambda j: j.status == jobs.RUNNING)
    assert running.status == jobs.RUNNING
    assert running.started_at is not None
    assert running.progress.stage_index == 1 and running.progress.stage_count == 3
    assert running.last_event_id == "ev-start"

    release.set()
    done = _poll_until(project, job.job_id, lambda j: j.status == jobs.SUCCEEDED)
    assert done.status == jobs.SUCCEEDED
    assert done.pct == 100
    assert done.artifact_ref == "artif-123"
    assert done.cost.actual_usd == 0.42
    assert done.cost.cache_hit_savings_usd == 0.10
    assert done.result and done.result["journey_status"] == "completed"
    assert done.finished_at is not None
    # caller saw every emitted event (stamped with correlation)
    assert len(events) >= 2
    assert all(e.get("job_id") == job.job_id for e in events)


def test_failed_job_surfaces_error(project):
    def boom(project, params, **kw):
        raise RuntimeError("kaboom")

    job = jobs.enqueue(
        project, "op", {"model": "m", "operation": "image"},
        dispatch={"op": boom},
    )
    done = _poll_until(project, job.job_id, lambda j: j.status in jobs.TERMINAL_STATUSES)
    assert done.status == jobs.FAILED
    assert "kaboom" in (done.error or "")


def test_unknown_kind_raises_keyerror(project):
    with pytest.raises(KeyError):
        jobs.enqueue(project, "nope.unknown", {}, dispatch={"other": lambda *a, **k: {}})


# ---------------------------------------------------------------------------
# dedup — the same idempotency_key runs the work once
# ---------------------------------------------------------------------------


def test_dedup_same_key_runs_once(project):
    release = threading.Event()
    calls = []
    dispatch = {"journey.full_auto": _blocking_stub(release, calls=calls)}

    job1 = jobs.enqueue(project, "journey.full_auto", VIDEO_PARAMS, dispatch=dispatch)
    _poll_until(project, job1.job_id, lambda j: j.status == jobs.RUNNING)

    # Identical request while the first is in flight → same job, no new worker.
    job2 = jobs.enqueue(project, "journey.full_auto", VIDEO_PARAMS, dispatch=dispatch)
    assert job2.job_id == job1.job_id
    assert job2.idempotency_key == job1.idempotency_key

    release.set()
    _poll_until(project, job1.job_id, lambda j: j.status in jobs.TERMINAL_STATUSES)
    time.sleep(0.05)
    assert calls == [job1.job_id]  # the stub ran exactly once
    # exactly one job in the index
    assert len(jobs.list_jobs(project)) == 1


def test_explicit_idempotency_key_dedups(project):
    release = threading.Event()
    calls = []
    dispatch = {"op": _blocking_stub(release, calls=calls)}
    a = jobs.enqueue(project, "op", {"a": 1}, dispatch=dispatch, idempotency_key="fixed-key")
    _poll_until(project, a.job_id, lambda j: j.status == jobs.RUNNING)
    b = jobs.enqueue(project, "op", {"b": 2}, dispatch=dispatch, idempotency_key="fixed-key")
    assert a.job_id == b.job_id == "fixed-key"
    release.set()
    _poll_until(project, a.job_id, lambda j: j.status in jobs.TERMINAL_STATUSES)
    assert calls == ["fixed-key"]


def test_terminal_key_reruns(project):
    """Dedup only holds while a job is live; a completed key re-runs."""
    release = threading.Event()
    release.set()  # never blocks
    calls = []
    dispatch = {"op": _blocking_stub(release, calls=calls)}
    a = jobs.enqueue(project, "op", VIDEO_PARAMS, dispatch=dispatch)
    _poll_until(project, a.job_id, lambda j: j.status in jobs.TERMINAL_STATUSES)
    assert len(calls) == 1
    b = jobs.enqueue(project, "op", VIDEO_PARAMS, dispatch=dispatch)
    assert a.job_id == b.job_id
    # a rerun of a terminal key re-launches; wait for the second invocation
    deadline = time.time() + 5
    while len(calls) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert len(calls) == 2  # ran again after the first went terminal


# ---------------------------------------------------------------------------
# cancel — flips to cancelled (not failed), idempotent, race-proof
# ---------------------------------------------------------------------------


def test_cancel_running_flips_to_cancelled_not_failed(project):
    release = threading.Event()
    dispatch = {"journey.full_auto": _blocking_stub(release)}
    job = jobs.enqueue(project, "journey.full_auto", VIDEO_PARAMS, dispatch=dispatch)
    _poll_until(project, job.job_id, lambda j: j.status == jobs.RUNNING)

    cancelled = jobs.cancel_job(project, job.job_id)
    assert cancelled.status in (jobs.CANCELLING, jobs.CANCELLED)

    # Let the worker finish (it will try to write COMPLETED) — cancel_requested
    # is authoritative, so the job must still read `cancelled`, never succeeded.
    release.set()
    final = _poll_until(
        project, job.job_id, lambda j: j.status in (jobs.CANCELLED, jobs.SUCCEEDED, jobs.FAILED)
    )
    assert final.status == jobs.CANCELLED
    assert final.error is None  # not a failure


def test_cancel_is_idempotent(project):
    release = threading.Event()
    dispatch = {"op": _blocking_stub(release)}
    job = jobs.enqueue(project, "op", VIDEO_PARAMS, dispatch=dispatch)
    _poll_until(project, job.job_id, lambda j: j.status == jobs.RUNNING)
    jobs.cancel_job(project, job.job_id)
    again = jobs.cancel_job(project, job.job_id)  # second cancel: no error
    assert again.status in (jobs.CANCELLING, jobs.CANCELLED)
    release.set()
    _poll_until(project, job.job_id, lambda j: j.status == jobs.CANCELLED)


def test_cancel_unknown_job_returns_none(project):
    assert jobs.cancel_job(project, "does-not-exist") is None


# ---------------------------------------------------------------------------
# ETA branches: prior (estimating…), learned p50, cache-hit not learned
# ---------------------------------------------------------------------------


def test_eta_prior_reports_estimating_and_no_pct(project):
    """Cold key → confidence='prior', pct is None, label_hint='estimating…'."""
    release = threading.Event()
    dispatch = {"journey.full_auto": _blocking_stub(release)}
    job = jobs.enqueue(project, "journey.full_auto", VIDEO_PARAMS, dispatch=dispatch)
    running = _poll_until(project, job.job_id, lambda j: j.status == jobs.RUNNING)
    assert running.confidence == "prior"
    assert running.pct is None
    assert running.label_hint == "estimating…"
    assert running.predicted_total_s == jobs.DEFAULT_CONFIG.prior_total_s["video"]
    release.set()
    _poll_until(project, job.job_id, lambda j: j.status in jobs.TERMINAL_STATUSES)


def test_predict_learned_p50_from_seeded_history(project):
    cfg = JobsConfig(n_min=3)
    rt = jobs._runtime(project, cfg)
    candidates, okind = jobs._eta_candidates(VIDEO_PARAMS, cfg)
    key = candidates[0][0]
    jobs._durations_put(rt.durations, key, [40.0, 50.0, 60.0])  # >= n_min samples

    pred = jobs.predict_total_s(candidates, okind, durations=rt.durations, config=cfg)
    assert pred.confidence == "learned"
    assert pred.p50 == 50.0  # median
    assert pred.p90 >= pred.p50


def test_predict_backoff_to_output_kind_is_coarse(project):
    cfg = JobsConfig(n_min=2)
    rt = jobs._runtime(project, cfg)
    candidates, okind = jobs._eta_candidates(VIDEO_PARAMS, cfg)
    # Seed ONLY the output-kind bucket, not the specific keys.
    jobs._durations_put(rt.durations, okind, [80.0, 100.0])
    pred = jobs.predict_total_s(candidates, okind, durations=rt.durations, config=cfg)
    assert pred.confidence == "learned_coarse"
    assert pred.p50 == 90.0


def test_predict_expected_cache_hit_is_exact_floor(project):
    cfg = JobsConfig()
    rt = jobs._runtime(project, cfg)
    candidates, okind = jobs._eta_candidates(VIDEO_PARAMS, cfg)
    pred = jobs.predict_total_s(
        candidates, okind, durations=rt.durations, expected_cache_hit=True, config=cfg
    )
    assert pred.confidence == "exact"
    assert pred.p50 == cfg.cache_hit_floor_s


def test_learning_accumulates_for_real_renders(project):
    """Running N distinct (but same-eta-key) real renders warms the key."""
    cfg = JobsConfig(n_min=3)
    release = threading.Event()
    release.set()
    dispatch = {"op": _blocking_stub(release)}
    rt = jobs._runtime(project, cfg)
    candidates, _ = jobs._eta_candidates(VIDEO_PARAMS, cfg)
    key = candidates[0][0]

    for i in range(3):
        # vary a non-eta field so job_ids differ but the eta key is identical
        params = dict(VIDEO_PARAMS, nonce=i)
        job = jobs.enqueue(project, "op", params, dispatch=dispatch, config=cfg)
        _poll_until(project, job.job_id, lambda j: j.status in jobs.TERMINAL_STATUSES, config=cfg)

    samples = jobs._durations_get(rt.durations, key)
    assert len(samples) == 3  # every real render was learned
    assert all(s >= 0 for s in samples)


def test_cache_hit_is_not_learned(project):
    """A cache-hit run must NOT drop a ~0s sample into the learning store."""
    cfg = JobsConfig(n_min=1)
    rt = jobs._runtime(project, cfg)
    candidates, _ = jobs._eta_candidates(VIDEO_PARAMS, cfg)
    key = candidates[0][0]

    # cached run
    rel_c = threading.Event(); rel_c.set()
    job_c = jobs.enqueue(
        project, "op", dict(VIDEO_PARAMS, nonce="c"),
        dispatch={"op": _blocking_stub(rel_c, cached=True)}, config=cfg,
    )
    _poll_until(project, job_c.job_id, lambda j: j.status in jobs.TERMINAL_STATUSES, config=cfg)
    assert jobs.get_job(project, job_c.job_id, config=cfg).cached is True
    assert jobs._durations_get(rt.durations, key) == []  # nothing learned from a cache hit

    # real (non-cached) twin → exactly one sample
    rel_r = threading.Event(); rel_r.set()
    job_r = jobs.enqueue(
        project, "op", dict(VIDEO_PARAMS, nonce="r"),
        dispatch={"op": _blocking_stub(rel_r, cached=False)}, config=cfg,
    )
    _poll_until(project, job_r.job_id, lambda j: j.status in jobs.TERMINAL_STATUSES, config=cfg)
    assert len(jobs._durations_get(rt.durations, key)) == 1


# ---------------------------------------------------------------------------
# list / get semantics
# ---------------------------------------------------------------------------


def test_list_newest_first_and_status_filter(project):
    release = threading.Event()
    release.set()
    dispatch = {"op": _blocking_stub(release)}
    ids = []
    for i in range(3):
        j = jobs.enqueue(project, "op", dict(VIDEO_PARAMS, nonce=i), dispatch=dispatch)
        _poll_until(project, j.job_id, lambda x: x.status in jobs.TERMINAL_STATUSES)
        ids.append(j.job_id)
        time.sleep(0.01)  # ensure distinct created_at ordering

    listed = jobs.list_jobs(project)
    assert len(listed) == 3
    # newest first
    created = [x.created_at for x in listed]
    assert created == sorted(created, reverse=True)

    succeeded = jobs.list_jobs(project, status=jobs.SUCCEEDED)
    assert len(succeeded) == 3
    assert jobs.list_jobs(project, status=jobs.RUNNING) == []


def test_list_respects_limit(project):
    release = threading.Event(); release.set()
    dispatch = {"op": _blocking_stub(release)}
    for i in range(5):
        j = jobs.enqueue(project, "op", dict(VIDEO_PARAMS, nonce=i), dispatch=dispatch)
        _poll_until(project, j.job_id, lambda x: x.status in jobs.TERMINAL_STATUSES)
    assert len(jobs.list_jobs(project, limit=2)) == 2


def test_get_unknown_returns_none(project):
    assert jobs.get_job(project, "no-such-job") is None


# ---------------------------------------------------------------------------
# stale-RUNNING reaper
# ---------------------------------------------------------------------------


def test_stale_running_is_reaped(project):
    from datetime import datetime, timedelta, timezone
    import au

    cfg = JobsConfig(stale_running_s=0.0)  # anything older than "now" is stale
    rt = jobs._runtime(project, cfg)
    job_id = "orphan-job"
    old = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat().replace(
        "+00:00", "Z"
    )
    # A job left RUNNING by a worker that died (no live thread in this process).
    rt.index[job_id] = {
        "job_id": job_id,
        "kind": "op",
        "label": "orphan",
        "idempotency_key": job_id,
        "params": {},
        "created_at": old,
        "started_at": old,
        "finished_at": None,
        "eta_candidates": [],
        "output_kind": None,
        "expected_cache_hit": False,
        "cached": False,
        "cancel_requested": False,
        "pct_floor": None,
        "progress": {},
        "cost": {},
        "artifact_ref": None,
        "result": None,
        "error": None,
        "last_event_id": None,
    }
    rt.au_store[job_id] = au.ComputationResult(None, au.ComputationStatus.RUNNING)

    reaped = jobs.get_job(project, job_id, config=cfg)
    assert reaped.status == jobs.FAILED
    assert jobs.REAPED_REASON in (reaped.error or "")


def test_live_worker_is_not_reaped(project):
    """A genuinely running job (live thread) must NOT be reaped even under a
    zero stale threshold."""
    cfg = JobsConfig(stale_running_s=0.0)
    release = threading.Event()
    dispatch = {"op": _blocking_stub(release)}
    job = jobs.enqueue(project, "op", VIDEO_PARAMS, dispatch=dispatch, config=cfg)
    running = _poll_until(project, job.job_id, lambda j: j.status == jobs.RUNNING, config=cfg)
    assert running.status == jobs.RUNNING  # liveness map protects it
    release.set()
    _poll_until(project, job.job_id, lambda j: j.status in jobs.TERMINAL_STATUSES, config=cfg)


# ---------------------------------------------------------------------------
# estimate (dry-run cost gate) + to_dict contract
# ---------------------------------------------------------------------------


def test_estimate_known_cost_below_threshold(project):
    out = jobs.estimate(project, "op", {"estimated_usd": 0.20})
    assert out["estimated_usd"] == 0.20
    assert out["has_unknown_costs"] is False
    assert out["requires_approval"] is False
    assert out["approval_threshold_usd"] == jobs.DEFAULT_CONFIG.approval_threshold_usd


def test_estimate_unknown_cost_requires_approval(project):
    out = jobs.estimate(project, "op", {})
    assert out["has_unknown_costs"] is True
    assert out["requires_approval"] is True


def test_estimate_expensive_requires_approval(project):
    out = jobs.estimate(project, "op", {"estimated_usd": 5.0})
    assert out["requires_approval"] is True


def test_to_dict_matches_json_contract(project):
    release = threading.Event(); release.set()
    dispatch = {"journey.full_auto": _blocking_stub(release)}
    job = jobs.enqueue(project, "journey.full_auto", VIDEO_PARAMS, dispatch=dispatch)
    done = _poll_until(project, job.job_id, lambda j: j.status in jobs.TERMINAL_STATUSES)
    d = jobs.to_dict(done)
    expected_keys = {
        "job_id", "kind", "label", "status", "cached", "run_id", "idempotency_key",
        "params", "created_at", "started_at", "finished_at", "queue_wait_s",
        "elapsed_s", "progress", "predicted_total_s", "remaining_s", "eta_ts",
        "eta_s", "pct", "confidence", "label_hint", "key", "cost", "artifact_ref",
        "result", "error", "last_event_id",
    }
    assert expected_keys.issubset(d.keys())
    # nested records serialize to plain dicts (JSON-safe)
    assert set(d["progress"].keys()) == {
        "stage_index", "stage_count", "current_transform", "fraction"
    }
    assert set(d["cost"].keys()) == {"estimated_usd", "actual_usd", "cache_hit_savings_usd"}
    import json

    json.dumps(d)  # must be JSON-serializable
