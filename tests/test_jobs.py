"""Offline tests for :mod:`nw.jobs` — the async render-job facade over ``au``.

Everything here uses a **stub render callable** (no real fal, no network). The
stub emits the same shape of lifecycle events reelee's render will, so the
progress/cost/eta/cancel machinery is exercised without spending money.
"""

import contextlib
from pathlib import Path
import contextvars
import threading
import time

import pytest

from au import ComputationResult, ComputationStatus
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


def _blocking_stub(release: threading.Event, *, calls=None, cached=False, emit_delay=0.0):
    """A stub render that blocks on ``release`` then completes, emitting events.

    ``emit_delay`` sleeps before the first stage event is emitted, widening the
    window in which a job is observably ``running`` with no stage event mirrored
    yet — used to stress the lifecycle test's async-milestone polling.
    """

    def stub(project, params, *, job_id, on_event, should_cancel):
        if calls is not None:
            calls.append(job_id)
        if emit_delay:
            time.sleep(emit_delay)
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
    """The full success lifecycle, asserted milestone-by-milestone.

    Every field is asserted only once it is actually reached (polled with a
    timeout), never snapshot-asserted at the instant status first flips —
    progress/last_event_id are populated by async stage-event mirroring, so
    they are legitimately absent right when a job becomes ``running``. The stub
    adds an ``emit_delay`` to widen that async window and surface any residual
    race locally.
    """
    release = threading.Event()
    dispatch = {"journey.full_auto": _blocking_stub(release, emit_delay=0.03)}
    events = []
    job = jobs.enqueue(
        project, "journey.full_auto", VIDEO_PARAMS,
        on_event=events.append, dispatch=dispatch,
    )

    # Milestone 1 — observably running, with started_at (a real nw.jobs
    # invariant, stamped at the running transition before the callable runs).
    running = _poll_until(project, job.job_id, lambda j: j.status == jobs.RUNNING)
    assert running.status == jobs.RUNNING
    assert running.started_at is not None

    # Milestone 2 — the first stage event has been mirrored (async: poll for it).
    # The mirror writes stage_index/stage_count/current_transform/last_event_id
    # in one atomic index update, so once stage_index is present the rest are too.
    staged = _poll_until(project, job.job_id, lambda j: j.progress.stage_index is not None)
    assert staged.progress.stage_index == 1
    assert staged.progress.stage_count == 3
    assert staged.progress.current_transform == "panel_to_image"
    assert staged.last_event_id == "ev-start"

    # Milestone 3 — terminal success carries artifact + reconciled cost.
    release.set()
    done = _poll_until(project, job.job_id, lambda j: j.status == jobs.SUCCEEDED)
    assert done.status == jobs.SUCCEEDED
    assert done.pct == 100
    assert done.artifact_ref == "artif-123"
    assert done.cost.actual_usd == 0.42
    assert done.cost.cache_hit_savings_usd == 0.10
    assert done.result and done.result["journey_status"] == "completed"
    assert done.finished_at is not None

    # By the terminal milestone the caller's sink saw every emitted event,
    # correlation-stamped. (Poll: the second event is emitted just before the
    # worker writes the terminal record.)
    _poll_until(project, job.job_id, lambda j: len(events) >= 2, timeout=2.0)
    assert len(events) >= 2
    assert all(e.get("job_id") == job.job_id for e in events)


def test_started_at_set_when_observable_as_running(project):
    """Invariant (regression guard for the CI race): a job observed as ``running``
    always has ``started_at`` set. The stub blocks on its very first line, so
    ``started_at`` cannot have been stamped by the callable — it must have been
    set at the running transition (``before_compute``), before the render body
    ran a single statement."""
    entered = threading.Event()
    release = threading.Event()

    def stub(project, params, *, job_id, on_event, should_cancel):
        entered.set()
        release.wait(timeout=5)
        return {}

    job = jobs.enqueue(project, "op", VIDEO_PARAMS, dispatch={"op": stub})
    running = _poll_until(project, job.job_id, lambda j: j.status == jobs.RUNNING)
    assert running.status == jobs.RUNNING
    assert running.started_at is not None
    release.set()
    _poll_until(project, job.job_id, lambda j: j.status in jobs.TERMINAL_STATUSES)


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


def test_plan_present_dedup_key_is_plan_derived_not_params_blind(project):
    """nw#41: a ``params["plan"]`` must actually drive the dedup key — the
    branch existed but nothing exercised it before this fix (no production
    caller passes a live ``Plan`` through ``enqueue`` yet, which is exactly
    how the silent-fallback bug went unnoticed). Tested directly against
    ``_default_idempotency_key`` rather than through a full ``enqueue()``:
    a raw ``Plan`` object in ``params`` isn't itself JSON-persistable by the
    job record store, a separate, pre-existing gap outside this issue's
    scope (flagged, not fixed, here).

    Two calls with the SAME other params but STRUCTURALLY DIFFERENT plans
    must get different keys — the plan is what makes them different, not
    the params.
    """
    from falaw import CallPlan, Plan

    plan_a = Plan(
        calls=(
            CallPlan(
                tool="generate_image",
                application="fal-ai/flux/dev",
                arguments={"prompt": "a tiger"},
                output_kind="image",
            ),
        )
    )
    plan_b = Plan(
        calls=(
            CallPlan(
                tool="generate_image",
                application="fal-ai/flux/dev",
                arguments={"prompt": "a different tiger"},
                output_kind="image",
            ),
        )
    )
    same_params = {"model": "fal-ai/flux/dev"}  # identical for both

    key_a = jobs._default_idempotency_key(project, "op", {**same_params, "plan": plan_a})
    key_b = jobs._default_idempotency_key(project, "op", {**same_params, "plan": plan_b})
    assert key_a != key_b, (
        "two structurally different plans must not collapse onto one "
        "dedup key just because their other params match"
    )
    # Stable: re-deriving the same plan's key must be deterministic.
    assert key_a == jobs._default_idempotency_key(
        project, "op", {**same_params, "plan": plan_a}
    )


def test_a_plan_that_cannot_be_hashed_is_refused_not_silently_deduped(project):
    """nw#41's failure mode, reproduced directly: before the fix, ANY
    `plan_hash` exception (now routinely `FalNonCanonicalArgument` since
    falaw#17) degraded the key to a plan-blind fallback — collapsing two
    different unhashable plans, or an unhashable plan and a coincidentally
    params-matching different job, onto one key with no error raised
    anywhere. Computing the key must now raise instead of silently
    returning a garbage, plan-blind basis — which is what protects
    `enqueue` (its only caller) from accepting the submission under a key
    that does not actually identify the plan.
    """
    from falaw import CallPlan, FalNonCanonicalArgument, Plan

    class _Unhashable:
        def __str__(self):
            return "looks-fine-but-isnt"

    junk_plan = Plan(
        calls=(
            CallPlan(
                tool="generate_image",
                application="fal-ai/flux/dev",
                arguments={"ref": _Unhashable()},
                output_kind="image",
            ),
        )
    )
    with pytest.raises(FalNonCanonicalArgument):
        jobs._default_idempotency_key(project, "op", {"plan": junk_plan})


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
        "result", "error", "last_event_id", "worker_silent_s", "worker_responsive",
    }
    assert expected_keys.issubset(d.keys())
    # nested records serialize to plain dicts (JSON-safe)
    assert set(d["progress"].keys()) == {
        "stage_index", "stage_count", "current_transform", "fraction"
    }
    assert set(d["cost"].keys()) == {
        "estimated_usd",
        "actual_usd",
        "cache_hit_savings_usd",
        "actual_is_lower_bound",
    }
    import json

    json.dumps(d)  # must be JSON-serializable


# ---------------------------------------------------------------------------
# caller-supplied context capture (BYO credentials that outlive the request)
# ---------------------------------------------------------------------------


def _reader_stub(seen: dict, var: contextvars.ContextVar):
    """A stub render that records the ContextVar value it observes in the worker."""

    def stub(project, params, *, job_id, on_event, should_cancel):
        seen["value"] = var.get()
        return {"journey_status": "completed"}

    return stub


def test_worker_does_not_inherit_contextvars_by_default(project):
    """Documents the gap the capture_context hook closes: ``ThreadBackend``
    runs the render on a bare thread, which does NOT copy ``ContextVars`` — so
    a request-bound var reads its *default* inside the worker."""
    var = contextvars.ContextVar("byo_gap_demo", default="owner-default")
    var.set("byo-from-request")  # bound on the request thread
    seen: dict = {}
    job = jobs.enqueue(
        project,
        "journey.full_auto",
        VIDEO_PARAMS,
        on_event=lambda e: None,
        dispatch={"journey.full_auto": _reader_stub(seen, var)},
    )
    jobs_done = _poll_until(
        project, job.job_id, lambda j: j.status in jobs.TERMINAL_STATUSES
    )
    assert jobs_done.status == jobs.SUCCEEDED
    assert seen["value"] == "owner-default"  # the worker did NOT see the request value


def test_capture_context_rebinds_caller_state_in_the_worker(project):
    """The capture_context hook snapshots request-scoped state on the request
    thread and re-establishes it inside the worker — so BYO credentials (reelee's
    vision + ElevenLabs keys) survive into a background job."""
    var = contextvars.ContextVar("byo_hook_demo", default="owner-default")
    var.set("byo-from-request")
    seen: dict = {}

    def capture():
        captured = var.get()  # snapshot NOW (request thread)

        @contextlib.contextmanager
        def _rebind():
            token = var.set(captured)
            try:
                yield
            finally:
                var.reset(token)

        return _rebind()

    job = jobs.enqueue(
        project,
        "journey.full_auto",
        VIDEO_PARAMS,
        on_event=lambda e: None,
        dispatch={"journey.full_auto": _reader_stub(seen, var)},
        capture_context=capture,
    )
    jobs_done = _poll_until(
        project, job.job_id, lambda j: j.status in jobs.TERMINAL_STATUSES
    )
    assert jobs_done.status == jobs.SUCCEEDED
    assert seen["value"] == "byo-from-request"  # the hook re-bound it in the worker


# ---------------------------------------------------------------------------
# unknown cost — a $0 must stay readable (reelee-studio survey, working rule 7)
# ---------------------------------------------------------------------------


def _unpriceable_stub(*, via_event: bool):
    """A stub whose run billed something nobody could price.

    ``via_event`` picks which of the two paths into the index carries the
    flag: the live event mirror, or the terminal reconcile. Both exist, and a
    fix to only one of them leaves the other reporting an exact ``$0``.
    """

    def stub(project, params, *, job_id, on_event, should_cancel):
        if via_event:
            on_event(
                {
                    "kind": "journey.stage_completed",
                    "actual_usd": 0.0,
                    "has_unknown_costs": True,
                }
            )
            return {"journey_status": "completed"}
        return {
            "journey_status": "completed",
            "cost_usd_actual": 0.0,
            "has_unknown_costs": True,
        }

    return stub


@pytest.mark.parametrize("via_event", [True, False], ids=["event", "terminal"])
def test_a_zero_that_means_unknown_is_flagged_as_a_lower_bound(project, via_event):
    """``actual_usd == 0.0`` with an unpriced billed call reads as a LOWER BOUND.

    This is the distinction a spend surface cannot be allowed to lose: a run
    that cost nothing and a run whose cost we do not know both arrive as
    ``0.0``. Without the flag the second renders as *free*.
    """
    job = jobs.enqueue(
        project,
        "journey.full_auto",
        VIDEO_PARAMS,
        dispatch={"journey.full_auto": _unpriceable_stub(via_event=via_event)},
    )
    done = _poll_until(project, job.job_id, lambda j: j.status == jobs.SUCCEEDED)

    assert done.status == jobs.SUCCEEDED
    assert done.cost.actual_usd == 0.0
    assert done.cost.actual_is_lower_bound is True
    assert jobs.to_dict(done)["cost"]["actual_is_lower_bound"] is True


def test_a_run_that_reports_no_unknown_cost_is_not_flagged(project):
    """The flag is a claim, not a default: a fully-priced run stays unflagged.

    Pins the other direction, so a mutation that hardcodes ``True`` — which
    would make every figure unusable rather than merely one — fails too.
    """
    release = threading.Event()
    release.set()
    job = jobs.enqueue(
        project,
        "journey.full_auto",
        VIDEO_PARAMS,
        dispatch={"journey.full_auto": _blocking_stub(release)},
    )
    done = _poll_until(project, job.job_id, lambda j: j.status == jobs.SUCCEEDED)

    assert done.cost.actual_usd == 0.42
    assert not done.cost.actual_is_lower_bound


def test_an_unreported_flag_is_none_not_false(project):
    """Absence is not a claim of exactness.

    A render that never says either way — an older caller, or one that died
    before finishing — leaves ``None``. Reading that as ``False`` would assert
    a precision nobody offered.
    """

    def silent(project, params, *, job_id, on_event, should_cancel):
        return {"journey_status": "completed"}

    job = jobs.enqueue(
        project,
        "journey.full_auto",
        VIDEO_PARAMS,
        dispatch={"journey.full_auto": silent},
    )
    done = _poll_until(project, job.job_id, lambda j: j.status == jobs.SUCCEEDED)

    assert done.cost.actual_is_lower_bound is None


# ---------------------------------------------------------------------------
# the job record must not lie about what happened
# ---------------------------------------------------------------------------


def test_a_late_cancel_does_not_rewrite_a_finished_job(project):
    """A cancel that arrives after the work finished is a no-op.

    ``cancel_requested`` is authoritative for status, so setting it on a
    terminal record turned a SUCCEEDED job into ``cancelled`` and dropped its
    ``result`` and ``pct``. The work had run, finished, and — for a paid render
    — billed. The window is the client's poll gap, and a multi-minute job
    behind a prominent "stop" button is the shape that produces the late click.
    """
    release = threading.Event()
    release.set()
    job = jobs.enqueue(
        project,
        "journey.full_auto",
        VIDEO_PARAMS,
        dispatch={"journey.full_auto": _blocking_stub(release)},
    )
    done = _poll_until(project, job.job_id, lambda j: j.status == jobs.SUCCEEDED)
    assert done.status == jobs.SUCCEEDED and done.result is not None

    after = jobs.cancel_job(project, job.job_id)

    assert after.status == jobs.SUCCEEDED, "a finished job was rewritten to cancelled"
    assert after.result == done.result, "the finished job's result was dropped"
    assert after.pct == 100
    assert after.artifact_ref == done.artifact_ref
    # …and it stays that way on the next read, i.e. nothing was persisted.
    assert jobs.get_job(project, job.job_id).status == jobs.SUCCEEDED


def test_cancelling_a_job_that_is_still_running_still_works(project):
    """The guard must not disarm cancel — the negative control.

    A guard that refused every cancel would pass the test above and silently
    remove the feature, so this pins the direction that still has a future to
    change.
    """
    release = threading.Event()
    job = jobs.enqueue(
        project,
        "journey.full_auto",
        VIDEO_PARAMS,
        dispatch={"journey.full_auto": _blocking_stub(release)},
    )
    _poll_until(project, job.job_id, lambda j: j.status == jobs.RUNNING)

    after = jobs.cancel_job(project, job.job_id)
    assert after.status in (jobs.CANCELLING, jobs.CANCELLED)
    release.set()
    assert _poll_until(
        project, job.job_id, lambda j: j.status in jobs.TERMINAL_STATUSES
    ).status == jobs.CANCELLED


@pytest.mark.parametrize(
    "fields, expect_index, expect_transform",
    [
        ({"stage_index": 2, "current_transform": "panel_to_clip"}, 2, "panel_to_clip"),
        ({"index": 2, "transform": "panel_to_clip"}, 2, "panel_to_clip"),
    ],
    ids=["canonical", "alias"],
)
def test_stage_progress_mirrors_under_either_field_name(
    project, fields, expect_index, expect_transform
):
    """The emitter owns its vocabulary; the translation belongs at this boundary.

    reelee's journey emits ``index`` / ``transform``. Without the aliases its
    entire stage breakdown mirrored as nulls while every event was being
    delivered correctly — which reads from outside as "no events are emitted"
    and sends the reader looking in the wrong place.
    """

    def stub(project, params, *, job_id, on_event, should_cancel):
        on_event({"kind": "journey.stage_started", **fields})
        return {"journey_status": "completed"}

    job = jobs.enqueue(
        project,
        "journey.full_auto",
        VIDEO_PARAMS,
        dispatch={"journey.full_auto": stub},
    )
    done = _poll_until(project, job.job_id, lambda j: j.status == jobs.SUCCEEDED)
    assert done.progress.stage_index == expect_index
    assert done.progress.current_transform == expect_transform


def test_the_canonical_field_wins_over_its_alias(project):
    """Precedence is explicit, not an accident of dict-literal order.

    A ``{source: dest}`` map reads better and resolves this case by whichever
    row was written last. Both names in one event is unlikely — and that is
    exactly why nobody would notice it resolving the wrong way.
    """

    def stub(project, params, *, job_id, on_event, should_cancel):
        on_event(
            {
                "kind": "journey.stage_started",
                "stage_index": 7,
                "index": 1,
                "current_transform": "canonical",
                "transform": "alias",
            }
        )
        return {"journey_status": "completed"}

    job = jobs.enqueue(
        project,
        "journey.full_auto",
        VIDEO_PARAMS,
        dispatch={"journey.full_auto": stub},
    )
    done = _poll_until(project, job.job_id, lambda j: j.status == jobs.SUCCEEDED)
    assert done.progress.stage_index == 7
    assert done.progress.current_transform == "canonical"


# ---------------------------------------------------------------------------
# liveness must be a fact in the shared store, not in one process's memory
# ---------------------------------------------------------------------------


def _running_record(project, job_id):
    rt = jobs._runtime(project)
    with rt.lock:
        return jobs._index_get_locked(rt, job_id)


def test_a_running_worker_stamps_a_heartbeat(project):
    """The beat has to actually reach the durable record, not just exist.

    It is what a *different process* reads to tell a live job from a dead one,
    so a heartbeat that never lands is the same as no heartbeat at all.
    """
    release = threading.Event()
    job = jobs.enqueue(
        project,
        "journey.full_auto",
        VIDEO_PARAMS,
        dispatch={"journey.full_auto": _blocking_stub(release)},
        config=JobsConfig(heartbeat_interval_s=0.01),
    )
    _poll_until(project, job.job_id, lambda j: j.status == jobs.RUNNING)
    beat = _poll_until(
        project,
        job.job_id,
        lambda j: (_running_record(project, j.job_id) or {}).get("heartbeat_at"),
    )
    assert (_running_record(project, beat.job_id) or {}).get("heartbeat_at")
    release.set()
    _poll_until(project, job.job_id, lambda j: j.status in jobs.TERMINAL_STATUSES)


def test_a_fresh_heartbeat_saves_a_job_this_process_cannot_see(project):
    """The multi-process case, simulated by clearing the process-local map.

    ``is_running`` answers "is a worker on this in MY process". A second API
    replica gets ``False`` for a perfectly healthy job and, past
    ``stale_running_s``, rewrites it to FAILED in the store they share. Here
    the record is old enough to reap and the heartbeat is fresh: the shared
    fact must win over the local absence.
    """
    rt = jobs._runtime(project)
    config = JobsConfig(stale_running_s=0.0, heartbeat_stale_s=600.0)
    record = {
        "job_id": "j1",
        "kind": "panel.animate",
        "started_at": jobs._iso(jobs._utcnow_plus(-3600)),
        "heartbeat_at": jobs._now_iso(),
    }
    running = ComputationResult(None, ComputationStatus.RUNNING)

    out = jobs._maybe_reap(rt, record, running, config)

    assert out.status is ComputationStatus.RUNNING, (
        "a live job in another process was reaped as dead"
    )


def test_a_stale_heartbeat_still_reaps(project):
    """The negative control: the reaper must still do its job.

    Without this, a predicate that always returned True would pass the test
    above and silently retire the stuck-toast fix the reaper exists to be.
    """
    rt = jobs._runtime(project)
    config = JobsConfig(stale_running_s=0.0, heartbeat_stale_s=1.0)
    record = {
        "job_id": "j2",
        "kind": "panel.animate",
        "started_at": jobs._iso(jobs._utcnow_plus(-3600)),
        "heartbeat_at": jobs._iso(jobs._utcnow_plus(-3600)),
    }

    out = jobs._maybe_reap(rt, record, ComputationResult(None, ComputationStatus.RUNNING), config)

    assert out.status is ComputationStatus.FAILED
    assert jobs.REAPED_REASON in str(out.error)


def test_a_record_with_no_heartbeat_keeps_the_old_behaviour(project):
    """Absence is not liveness.

    Records written before heartbeats existed, and workers that died before
    their first beat, must both stay reapable — otherwise this change turns a
    stuck job into a permanently stuck one.
    """
    rt = jobs._runtime(project)
    config = JobsConfig(stale_running_s=0.0)
    record = {
        "job_id": "j3",
        "kind": "panel.animate",
        "started_at": jobs._iso(jobs._utcnow_plus(-3600)),
    }

    out = jobs._maybe_reap(rt, record, ComputationResult(None, ComputationStatus.RUNNING), config)

    assert out.status is ComputationStatus.FAILED


def test_the_beat_stops_when_the_render_raises(project):
    """A dead job must stop claiming to be alive.

    If the beat outlived a raising render, the heartbeat would veto the reap
    forever and the job would hang RUNNING — this change would have replaced
    one stuck-job bug with a worse one.
    """
    def boom(project, params, *, job_id, on_event, should_cancel):
        raise RuntimeError("render exploded")

    job = jobs.enqueue(
        project,
        "journey.full_auto",
        VIDEO_PARAMS,
        dispatch={"journey.full_auto": boom},
        config=JobsConfig(heartbeat_interval_s=0.01),
    )
    _poll_until(project, job.job_id, lambda j: j.status in jobs.TERMINAL_STATUSES)

    first = (_running_record(project, job.job_id) or {}).get("heartbeat_at")
    time.sleep(0.1)  # several beat intervals
    assert (_running_record(project, job.job_id) or {}).get("heartbeat_at") == first, (
        "the heartbeat thread outlived the render that raised"
    )


def test_liveness_is_surfaced_with_the_reapers_own_definition(project):
    """A UI and the reaper must never disagree about what "alive" means.

    ``worker_responsive`` is derived from the same ``_heartbeat_is_fresh``
    predicate ``_maybe_reap`` consults. A second definition living in a client
    is how a screen ends up insisting a job is fine while the server is
    failing it — and the client's copy is the one the user believes.
    """
    config = JobsConfig(heartbeat_stale_s=120.0)
    rt = jobs._runtime(project, config)
    fresh = {
        "job_id": "live",
        "kind": "panel.animate",
        "started_at": jobs._iso(jobs._utcnow_plus(-300)),
        "heartbeat_at": jobs._iso(jobs._utcnow_plus(-5)),
    }
    silent = {**fresh, "job_id": "lost",
              "heartbeat_at": jobs._iso(jobs._utcnow_plus(-600))}
    running = ComputationResult(None, ComputationStatus.RUNNING)

    alive = jobs._project_job(rt, fresh, running, config)
    lost = jobs._project_job(rt, silent, running, config)

    assert alive.worker_responsive is True
    assert alive.worker_silent_s < 30
    assert lost.worker_responsive is False, "contact was lost and nothing said so"
    assert lost.worker_silent_s > 500
    # The UI's verdict and the reaper's verdict come from one predicate.
    assert lost.worker_responsive is jobs._heartbeat_is_fresh(silent, config)


def test_a_job_that_never_beat_reports_unknowable_not_dead(project):
    """``None`` is not ``False``.

    A record written before heartbeats existed has no liveness evidence either
    way. Reporting that as "contact lost" would mark every pre-existing job
    broken; reporting it as ``0`` seconds silent would mark it healthiest.
    """
    rt = jobs._runtime(project)
    record = {
        "job_id": "old",
        "kind": "panel.animate",
        "started_at": jobs._iso(jobs._utcnow_plus(-60)),
    }

    job = jobs._project_job(
        rt, record, ComputationResult(None, ComputationStatus.RUNNING), jobs.DEFAULT_CONFIG
    )

    assert job.worker_silent_s is None
    assert job.worker_responsive is None


def test_a_finished_job_is_not_reported_as_having_lost_contact(project):
    """A terminal worker is *supposed* to be silent.

    Without this guard every completed job would eventually read
    ``worker_responsive: false`` as its last heartbeat aged, and a finished
    shot would render as a broken one.
    """
    release = threading.Event()
    release.set()
    job = jobs.enqueue(
        project,
        "journey.full_auto",
        VIDEO_PARAMS,
        dispatch={"journey.full_auto": _blocking_stub(release)},
        config=JobsConfig(heartbeat_interval_s=0.01),
    )
    done = _poll_until(project, job.job_id, lambda j: j.status == jobs.SUCCEEDED)

    assert done.status == jobs.SUCCEEDED
    assert done.worker_responsive is None, "a finished job read as unresponsive"


# ---------------------------------------------------------------------------
# summarize — a read that reads, and does nothing else
# ---------------------------------------------------------------------------


def test_summarize_provisions_nothing(project):
    """``list_jobs`` looks like a read and is not.

    It calls ``_runtime``, which creates ``.nw/jobs/{au,index,durations}`` and
    memoizes a ``_JobsRuntime`` — owning a render ``ThreadBackend`` — into the
    unbounded module-global ``_RUNTIMES``. A cross-project dashboard polling N
    projects is exactly the caller that turns that into render machinery for
    every project a user has ever glanced at, held for the life of the process.
    """
    jobs_dir = Path(project.root) / jobs.DEFAULT_CONFIG.jobs_dirname
    jobs._reset_runtimes()

    assert jobs.summarize(project.root) == []

    assert not jobs_dir.exists(), "a read created the job directories"
    assert not jobs._RUNTIMES, "a read built a runtime (and a ThreadBackend)"


def test_list_jobs_still_provisions_and_that_is_the_contrast(project):
    """Negative control, and the measurement that motivated ``summarize``.

    Pinning the old behaviour keeps the two functions honestly different: if
    ``list_jobs`` ever stops provisioning, ``summarize`` has no reason to exist
    and this test says so by failing.
    """
    jobs_dir = Path(project.root) / jobs.DEFAULT_CONFIG.jobs_dirname
    jobs._reset_runtimes()

    jobs.list_jobs(project)

    assert jobs_dir.exists()
    assert len(jobs._RUNTIMES) == 1


def test_summarize_reads_real_jobs_without_a_runtime(project):
    """It has to actually answer, not just decline to write."""
    release = threading.Event()
    release.set()
    job = jobs.enqueue(
        project,
        "journey.full_auto",
        VIDEO_PARAMS,
        dispatch={"journey.full_auto": _blocking_stub(release)},
    )
    _poll_until(project, job.job_id, lambda j: j.status == jobs.SUCCEEDED)
    jobs._reset_runtimes()

    rows = jobs.summarize(project.root)

    assert [r.job_id for r in rows] == [job.job_id]
    assert rows[0].status == jobs.SUCCEEDED, (
        "status must come from the au store — the index's own status field is "
        "not authoritative and reports finished jobs as queued"
    )
    assert rows[0].cost.actual_usd == 0.42
    assert not jobs._RUNTIMES


def test_summarize_does_not_reap_someone_elses_job(project):
    """Reaping is a write, and a read-only fan-out must not fail a job.

    A stale RUNNING record read through ``get_job`` is reaped to FAILED — the
    right call for a caller looking at that job, and the wrong one for a
    dashboard glancing across every project.
    """
    release = threading.Event()
    job = jobs.enqueue(
        project,
        "journey.full_auto",
        VIDEO_PARAMS,
        dispatch={"journey.full_auto": _blocking_stub(release)},
    )
    _poll_until(project, job.job_id, lambda j: j.status == jobs.RUNNING)
    # Backdate it past the reaper's threshold.
    rt = jobs._runtime(project)
    with rt.lock:
        record = jobs._index_get_locked(rt, job.job_id)
        record["started_at"] = jobs._iso(jobs._utcnow_plus(-99999))
        record.pop("heartbeat_at", None)
        jobs._index_set_locked(rt, job.job_id, record)
    jobs._reset_runtimes()

    rows = jobs.summarize(project.root, config=JobsConfig(stale_running_s=0.0))

    assert rows[0].status == jobs.RUNNING, "a read-only summary reaped a job"
    release.set()
