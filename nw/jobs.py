"""nw.jobs — a project-scoped async **job** facade over ``au``.

A "job" is one long, cancellable unit of render work (a full-auto journey, a
single ``panel.animate``, an ``assemble_animatic`` pass, …). It has a durable
id, a persistent terminal state, live progress + ETA, a cost, and a cancel
entry keyed by that id — everything a task tray needs and none of which a bare
HTTP request provides.

This module is the *only* place async substance lives for the render layer:
``au`` supplies the submit → poll → result skeleton and the durable store;
``nw.jobs`` adds the five render-domain concerns ``au`` has no data model for —
progress %, ETA, human label/kind, idempotency (dedup), and cost — plus the
5-state normalization, context-capture so a job outlives its request, and the
active-jobs index that makes membership meaningful. Consumers (reelee's
``/api/jobs`` closures) stay thin: they call :func:`enqueue` / :func:`estimate`
/ :func:`list_jobs` / :func:`get_job` / :func:`cancel_job` / :func:`to_dict`
and serialize the result.

Design decisions (from the ``nw.jobs``-on-``au`` design report,
``misc/docs/research/async-job-manager-on-au.md``):

- **Backend = ``au.ThreadBackend``**, not ``ProcessBackend``. A fal render is
  I/O-bound (a blocking upstream wait), and the worker must share the live
  in-process ``Project`` graph and write events to the same channel the
  existing SSE tails. Process isolation buys no real fal cancel (fal still
  bills) while breaking the live-progress channel. Where bounded concurrent
  paid renders matter, swap in ``StdLibQueueBackend(use_processes=False)`` —
  a construction detail behind this facade.
- **au store is SSOT for *status* only.** ``ThreadBackend`` overwrites the
  store record with a bare ``ComputationResult`` at start (RUNNING) and end
  (COMPLETED), carrying no metadata — so every job-semantic field lives in a
  per-project **active-jobs index** (a ``dol`` mapping), which ``au`` cannot
  clobber. The index is also the membership authority: ``au.FileSystemStore``
  synthesizes ``PENDING`` for a missing key, so store membership is
  meaningless.
- **Idempotency**: the au store key *is* the idempotency key
  (``sha256(project:kind:plan_hash-or-params)``). A resubmit while a job with
  that key is live returns the existing job instead of launching a duplicate.
- **Cancel is boundary-grained and race-proof.** ``cancel_job`` flips the au
  record terminal *and* sets a durable should-cancel flag; the ``cancel_requested``
  flag is the authority for cancellation intent, so a job reads
  ``cancelling`` → ``cancelled`` regardless of whether the still-running
  ``ThreadBackend`` thread later clobbers the store with COMPLETED.
- **ETA is learned from observed wall-time**, per ``(model, operation, dur_bucket)``
  with back-off, median (not mean), honest "estimating…" before enough
  history, and **cache-hits excluded from learning**.

All tunables are keyword-configurable via :class:`JobsConfig`; defaults live at
the top of this module — no magic numbers below.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import au
from au import (
    ComputationResult,
    ComputationStatus,
    FileSystemStore,
    Middleware,
    ThreadBackend,
)
from au.base import ComputationBackend
from dol import JsonFiles


# ---------------------------------------------------------------------------
# Config — every tunable, defaults here (no magic numbers below)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobsConfig:
    """Tunables for the job manager. All keyword-configurable; sensible defaults.

    ETA knobs (``n_min`` … ``prior_total_s``) mirror the design report §5.7.
    """

    n_min: int = 3
    """Minimum samples for a key before its prediction is ``"learned"`` (not prior)."""
    sample_window_k: int = 20
    """Keep only the most-recent K duration samples per key (robust to drift)."""
    pct_ceil: int = 99
    """Never *compute* 100% — only the → succeeded transition sets 100."""
    overrun_factor: float = 1.5
    """Synthesize ``p90 = p50 * overrun_factor`` when a real p90 isn't available."""
    cache_hit_floor_s: float = 0.5
    """Predicted total for an all-cache-hit plan (``confidence="exact"``)."""
    dur_buckets_s: tuple[float, ...] = (4.0, 8.0, 12.0)
    """Upper edges of the output-duration buckets for ``per_second`` models."""
    prior_total_s: Mapping[str, float] = field(
        default_factory=lambda: {"image": 12.0, "video": 90.0, "audio": 15.0}
    )
    """Cold-start priors by output kind (drives the honest "estimating…" label)."""
    default_prior_total_s: float = 30.0
    """Prior when even the output kind is unknown."""
    stale_running_s: float = 900.0
    """A RUNNING record older than this with no live worker is reaped as
    ``FAILED("worker died — resumable")`` (kills the stuck-toast bug)."""
    approval_threshold_usd: float = 1.0
    """Estimated cost at/above which a render requires explicit approval."""
    jobs_dirname: str = ".nw/jobs"
    """Sub-path under ``project.root`` for the job stores (nw's ``.nw/`` convention)."""


DEFAULT_CONFIG = JobsConfig()

REAPED_REASON = "worker died — resumable"

# Job status vocabulary (normalized from au's ComputationStatus + render outcome)
QUEUED = "queued"
RUNNING = "running"
CANCELLING = "cancelling"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
TERMINAL_STATUSES = frozenset({SUCCEEDED, FAILED, CANCELLED})


# ---------------------------------------------------------------------------
# The Job record (single source of truth for every endpoint) + sub-records
# ---------------------------------------------------------------------------


@dataclass
class JobProgress:
    stage_index: int | None = None
    stage_count: int | None = None
    current_transform: str | None = None
    fraction: float | None = None


@dataclass
class JobCost:
    estimated_usd: float | None = None
    actual_usd: float | None = None
    cache_hit_savings_usd: float | None = None


@dataclass
class Job:
    """Projected, JSON-serializable view of one job (see :func:`to_dict`)."""

    job_id: str
    kind: str
    label: str
    status: str
    idempotency_key: str
    params: dict = field(default_factory=dict)

    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    queue_wait_s: float | None = None
    elapsed_s: float | None = None

    progress: JobProgress = field(default_factory=JobProgress)
    predicted_total_s: float | None = None
    remaining_s: float | None = None
    eta_ts: str | None = None
    eta_s: float | None = None
    pct: int | None = None
    confidence: str | None = None
    label_hint: str | None = None
    eta_key: str | None = None

    cost: JobCost = field(default_factory=JobCost)
    cached: bool = False

    artifact_ref: str | None = None
    result: dict | None = None
    error: str | None = None
    run_id: str | None = None
    last_event_id: str | None = None


@dataclass(frozen=True)
class _Prediction:
    p50: float
    p90: float
    confidence: str  # exact | learned | learned_coarse | prior


# ---------------------------------------------------------------------------
# Public facade — the six functions reelee (and any consumer) calls
# ---------------------------------------------------------------------------


def enqueue(
    project,
    kind: str,
    params: dict,
    *,
    on_event: Callable[[Any], None] | None = None,
    dispatch: Mapping[str, Callable] | None = None,
    backend: ComputationBackend | None = None,
    idempotency_key: str | None = None,
    label: str | None = None,
    config: JobsConfig = DEFAULT_CONFIG,
) -> Job:
    """Enqueue a billable render as a background job. Returns a :class:`Job`
    (``status="queued"``) **immediately** — the call never blocks and never
    bills on the calling thread.

    The request context (fal credentials + the resolved ``Project``) is
    captured now and re-established inside the worker so the job outlives its
    request. If a *live* (non-terminal) job with the same ``idempotency_key``
    already exists, that job is returned instead of launching a duplicate.

    Args:
        project: the ``nw.Project`` the render operates on.
        kind: dispatch key selecting the render callable (e.g.
            ``"journey.full_auto"``, ``"panel.animate"``).
        params: render parameters (also the ETA-key + default-idempotency basis).
        on_event: sink for the render's lifecycle events (reelee wires this to
            its ``agent_log`` / SSE tail). Events are stamped with
            ``job_id``/``run_id`` and mirrored into progress/cost/eta.
        dispatch: ``{kind: callable}`` table. Each callable is invoked as
            ``callable(project, params, *, job_id, on_event, should_cancel)``
            (only the kwargs it declares are passed) and should return a
            JSON-serializable result payload.
        backend: advanced override; default is the managed ``ThreadBackend``
            (which carries the duration-learning middleware + liveness map).
        idempotency_key: dedup handle; default derived from ``falaw.plan_hash``
            of ``params["plan"]`` when present, else a stable hash of params.
        label: human tray label; default derived from ``kind``/``params``.
        config: tunables (see :class:`JobsConfig`).

    Raises:
        KeyError: if ``kind`` is not in ``dispatch``.
    """
    rt = _runtime(project, config)
    dispatch = dispatch or {}
    idempotency_key = idempotency_key or _default_idempotency_key(project, kind, params)
    job_id = idempotency_key  # the au store key IS the idempotency key
    label = label or _default_label(kind, params)
    eta_candidates, output_kind = _eta_candidates(params, config)

    with rt.lock:
        existing = _index_get_locked(rt, job_id)
        if existing is not None:
            au_result = rt.au_store[job_id]
            status = _normalize_status(
                au_result.status,
                cancel_requested=existing.get("cancel_requested", False),
            )
            if status not in TERMINAL_STATUSES:
                # Live job with this key already exists → dedup, run once.
                return _project_job(rt, existing, au_result, config)

        callable_ = dispatch.get(kind)
        if callable_ is None:
            raise KeyError(
                f"unknown job kind {kind!r} (dispatch has {sorted(dispatch)})"
            )

        record = {
            "job_id": job_id,
            "kind": kind,
            "label": label,
            "idempotency_key": idempotency_key,
            "params": params,
            "created_at": _now_iso(),
            "started_at": None,
            "finished_at": None,
            "eta_candidates": [list(c) for c in eta_candidates],
            "output_kind": output_kind,
            "expected_cache_hit": bool(params.get("expected_cache_hit", False)),
            "cached": False,
            "cancel_requested": False,
            "pct_floor": None,
            "progress": {},
            "cost": {"estimated_usd": params.get("estimated_usd")},
            "artifact_ref": None,
            "result": None,
            "error": None,
            "last_event_id": None,
        }
        _index_set_locked(rt, job_id, record)

        the_backend = backend if backend is not None else rt.backend
        bound = _bind_worker(
            project,
            kind,
            params,
            callable_,
            job_id=job_id,
            on_event=on_event,
            rt=rt,
            config=config,
        )
        the_backend.launch(bound, (), {}, job_id, rt.au_store)

    return _project_job(rt, record, rt.au_store[job_id], config)


def estimate(
    project, kind: str, params: dict, *, config: JobsConfig = DEFAULT_CONFIG
) -> dict:
    """Dry-run cost gate **without enqueueing**.

    Returns ``{estimated_usd, has_unknown_costs, approval_threshold_usd,
    requires_approval}``. Unknown cost always requires approval (preserves the
    one-price-per-clip gate). reelee computes the falaw-``Plan`` cost and either
    passes it via ``params["estimated_usd"]`` or overrides this per its own
    policy.
    """
    estimated = params.get("estimated_usd")
    has_unknown = estimated is None
    threshold = config.approval_threshold_usd
    requires_approval = has_unknown or (
        estimated is not None and estimated >= threshold
    )
    return {
        "estimated_usd": estimated,
        "has_unknown_costs": has_unknown,
        "approval_threshold_usd": threshold,
        "requires_approval": requires_approval,
    }


def list_jobs(
    project,
    *,
    status: str | None = None,
    limit: int = 50,
    config: JobsConfig = DEFAULT_CONFIG,
) -> list[Job]:
    """Jobs for this project, **newest first**, optionally filtered by status.

    Backed by the per-project active-jobs index (not by scanning the au store,
    whose missing-key-returns-PENDING gotcha makes membership meaningless).
    """
    rt = _runtime(project, config)
    with rt.lock:
        records = [r for r in (_index_get_locked(rt, k) for k in list(rt.index)) if r]
    records.sort(key=lambda r: r.get("created_at") or "", reverse=True)

    jobs: list[Job] = []
    for record in records:
        job = _read_job(rt, record, config)
        if status is None or job.status == status:
            jobs.append(job)
        if len(jobs) >= limit:
            break
    return jobs


def get_job(project, job_id: str, *, config: JobsConfig = DEFAULT_CONFIG) -> Job | None:
    """One job (projecting the au status + mirrored index metadata). ``None`` if
    unknown. Reaps a stale-RUNNING record on read."""
    rt = _runtime(project, config)
    with rt.lock:
        record = _index_get_locked(rt, job_id)
    if record is None:
        return None
    return _read_job(rt, record, config)


def cancel_job(
    project, job_id: str, *, config: JobsConfig = DEFAULT_CONFIG
) -> Job | None:
    """Request cancellation. **Idempotent.** ``None`` if unknown.

    Sets a durable should-cancel flag (a running stage stops at its next
    boundary) *and* flips the au record terminal at once via ``au.cancel_task``
    (which builds the handle **with the backend** so ``terminate`` is reached).
    The ``cancel_requested`` flag is authoritative, so the job reads
    ``cancelling`` → ``cancelled`` even if the still-running thread later
    overwrites the store with COMPLETED.
    """
    rt = _runtime(project, config)
    with rt.lock:
        record = _index_get_locked(rt, job_id)
        if record is None:
            return None
        record["cancel_requested"] = True
        _index_set_locked(rt, job_id, record)
    # Flip the durable record terminal immediately (idempotent: no-op if already
    # terminal). Passing the backend lets cancel() reach terminate() (§3.1).
    au.cancel_task(job_id, backend=rt.backend, store=rt.au_store)
    return get_job(project, job_id, config=config)


def to_dict(job: Job) -> dict:
    """Serialize a :class:`Job` to the JSON contract (design report §7.4)."""
    return {
        "job_id": job.job_id,
        "kind": job.kind,
        "label": job.label,
        "status": job.status,
        "cached": job.cached,
        "run_id": job.run_id,
        "idempotency_key": job.idempotency_key,
        "params": job.params,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "queue_wait_s": job.queue_wait_s,
        "elapsed_s": job.elapsed_s,
        "progress": asdict(job.progress),
        "predicted_total_s": job.predicted_total_s,
        "remaining_s": job.remaining_s,
        "eta_ts": job.eta_ts,
        "eta_s": job.eta_s,
        "pct": job.pct,
        "confidence": job.confidence,
        "label_hint": job.label_hint,
        "key": job.eta_key,
        "cost": asdict(job.cost),
        "artifact_ref": job.artifact_ref,
        "result": job.result,
        "error": job.error,
        "last_event_id": job.last_event_id,
    }


# ---------------------------------------------------------------------------
# ETA: duration-learning middleware + predictor (au-5 seeded here, upstream later)
# ---------------------------------------------------------------------------


class DurationLearningMiddleware(Middleware):
    """Keyed, percentile duration learner — a cousin of ``au.MetricsMiddleware``.

    Records the render wall-time under every ETA-key candidate for the job, so
    a cold specific key backs off to a warmer coarse one. Two deliberate
    departures from ``au``'s built-in metrics:

    - **Self-timed** (``time.monotonic`` in ``before_compute`` → ``after_compute``)
      rather than reading ``result.duration``: ``ThreadBackend`` constructs a
      *fresh* COMPLETED ``ComputationResult`` whose ``created_at`` is the
      completion instant, so ``result.duration`` is ~0 and useless. (Surfaced
      as an ``au`` finding.)
    - **Cache-hits are never learned** — a ~0s cache hit would drag the median
      to zero. The job's ``cached`` flag (mirrored from a ``cache_hit`` event
      during the run) gates recording.

    Its ``_start`` map doubles as the in-process **liveness signal** the stale
    reaper uses (a RUNNING au record whose key is not in ``_start`` and whose
    ``started_at`` is old is a dead worker).
    """

    def __init__(
        self,
        *,
        durations: MutableMapping,
        index: MutableMapping,
        lock: threading.Lock,
        config: JobsConfig = DEFAULT_CONFIG,
    ):
        self._durations = durations
        self._index = index
        self._lock = lock
        self._config = config
        self._start: dict[str, float] = {}

    def before_compute(
        self, func: Callable, args: tuple, kwargs: dict, key: str
    ) -> None:
        # This hook is the running-transition boundary: the ThreadBackend worker
        # runs it *before* it writes the RUNNING record (base.py: middleware-before
        # → store[key]=RUNNING → func()). Stamping ``started_at`` here — in the
        # same lock as the liveness map — guarantees a job can never be observed
        # as ``running`` with ``started_at is None`` (closes a CI race where a slow
        # runner polled the window between the RUNNING write and a later stamp).
        with self._lock:
            self._start[key] = time.monotonic()
            record = self._index.get(key)
            if record is not None and record.get("started_at") is None:
                record["started_at"] = _now_iso()
                self._index[key] = record

    def after_compute(self, key: str, result: ComputationResult) -> None:
        with self._lock:
            start = self._start.pop(key, None)
        if start is None:
            return
        elapsed = time.monotonic() - start
        with self._lock:
            record = self._index.get(key)
            if not record or record.get("cached"):
                return  # cache-hit (or unknown) → do NOT learn
            candidates = record.get("eta_candidates") or []
            for cand in candidates:
                self._record_sample_locked(cand[0], elapsed)

    def on_error(self, key: str, error: Exception) -> None:
        with self._lock:
            self._start.pop(key, None)

    def is_running(self, key: str) -> bool:
        with self._lock:
            return key in self._start

    def _record_sample_locked(self, eta_key: str, elapsed: float) -> None:
        samples = _durations_get(self._durations, eta_key)
        samples.append(elapsed)
        samples = samples[-self._config.sample_window_k :]
        _durations_put(self._durations, eta_key, samples)


def predict_total_s(
    eta_candidates: list,
    output_kind: str | None,
    *,
    durations: MutableMapping,
    expected_cache_hit: bool = False,
    config: JobsConfig = DEFAULT_CONFIG,
) -> _Prediction:
    """Predict the total render seconds for a job as ``(p50, p90, confidence)``.

    Walks the ETA-key candidates most-specific-first; the first key with
    ``>= n_min`` samples wins (median, with a real or synthesized p90). Falls
    back through the coarse key, the output-kind key, then the cold prior. An
    all-cache-hit plan short-circuits to ``cache_hit_floor_s`` (``"exact"``).
    """
    if expected_cache_hit:
        return _Prediction(config.cache_hit_floor_s, config.cache_hit_floor_s, "exact")
    for cand in eta_candidates:
        key, conf = cand[0], cand[1]
        samples = _durations_get(durations, key)
        if len(samples) >= config.n_min:
            p50 = statistics.median(samples)
            p90 = _percentile(samples, 90)
            if p90 is None or p90 < p50:
                p90 = p50 * config.overrun_factor
            return _Prediction(p50, p90, conf)
    prior = config.prior_total_s.get(output_kind, config.default_prior_total_s)
    return _Prediction(prior, prior * config.overrun_factor, "prior")


# ---------------------------------------------------------------------------
# Per-project runtime (memoized): stores + backend + middleware + lock
# ---------------------------------------------------------------------------


@dataclass
class _JobsRuntime:
    root: str
    au_store: FileSystemStore
    index: MutableMapping
    durations: MutableMapping
    backend: ThreadBackend
    middleware: DurationLearningMiddleware
    lock: threading.Lock
    config: JobsConfig


_RUNTIMES: dict[str, _JobsRuntime] = {}
_RUNTIMES_LOCK = threading.Lock()


def _runtime(project, config: JobsConfig = DEFAULT_CONFIG) -> _JobsRuntime:
    """Get-or-build the memoized job runtime for ``project`` (keyed by root).

    The first touch of a project fixes its runtime (stores + backend +
    middleware share one lock and one liveness map). ``config`` is honored on
    that first build.
    """
    root = str(Path(project.root).resolve())
    with _RUNTIMES_LOCK:
        rt = _RUNTIMES.get(root)
        if rt is None:
            base = Path(project.root) / config.jobs_dirname
            (base / "au").mkdir(parents=True, exist_ok=True)
            (base / "index").mkdir(parents=True, exist_ok=True)
            (base / "durations").mkdir(parents=True, exist_ok=True)
            au_store = FileSystemStore(str(base / "au"), ttl_seconds=None)
            index = JsonFiles(str(base / "index"))
            durations = JsonFiles(str(base / "durations"))
            lock = threading.Lock()
            middleware = DurationLearningMiddleware(
                durations=durations, index=index, lock=lock, config=config
            )
            backend = ThreadBackend(store=au_store, middleware=[middleware])
            rt = _JobsRuntime(
                root=root,
                au_store=au_store,
                index=index,
                durations=durations,
                backend=backend,
                middleware=middleware,
                lock=lock,
                config=config,
            )
            _RUNTIMES[root] = rt
    return rt


def _reset_runtimes() -> None:
    """Drop all memoized runtimes (test hygiene / re-open after config change)."""
    with _RUNTIMES_LOCK:
        _RUNTIMES.clear()


# ---------------------------------------------------------------------------
# Worker binding + event mirroring (context capture — §6.4)
# ---------------------------------------------------------------------------


def _bind_worker(project, kind, params, callable_, *, job_id, on_event, rt, config):
    """Bind the render callable into a zero-arg worker body that (a) re-establishes
    request context (fal credentials + project), (b) routes events into both the
    caller's sink and the index mirror, (c) exposes a durable ``should_cancel``."""
    captured_fal_key = _capture_fal_credentials()

    def should_cancel() -> bool:
        with rt.lock:
            record = _index_get_locked(rt, job_id)
        if record and record.get("cancel_requested"):
            return True
        try:
            return rt.au_store[job_id].status is ComputationStatus.CANCELLED
        except Exception:
            return False

    def sink(ev) -> None:
        _stamp_correlation(ev, job_id)
        try:
            if on_event is not None:
                on_event(ev)
        finally:
            try:
                _mirror_event_into_index(rt, job_id, ev)
            except Exception:
                pass

    def run():
        # ``started_at`` is stamped by the backend's ``before_compute`` hook, which
        # runs before the RUNNING transition is observable — see
        # :meth:`DurationLearningMiddleware.before_compute`. Nothing to stamp here.
        def call():
            return _call_dispatch(
                callable_,
                project,
                params,
                job_id=job_id,
                on_event=sink,
                should_cancel=should_cancel,
            )

        if captured_fal_key:
            from falaw import using_fal_credentials

            with using_fal_credentials(captured_fal_key):
                result = call()
        else:
            result = call()
        _reconcile_terminal(rt, job_id, result)
        return result

    return run


def _capture_fal_credentials():
    """Snapshot the active fal credential (if any) to re-bind inside the worker."""
    try:
        from falaw import current_fal_key

        return current_fal_key()
    except Exception:
        return None


def _call_dispatch(callable_, project, params, *, job_id, on_event, should_cancel):
    """Invoke the render callable, passing only the render-context kwargs it
    accepts (progressive disclosure: a plain ``f(project, params)`` works too)."""
    import inspect

    extras = {"job_id": job_id, "on_event": on_event, "should_cancel": should_cancel}
    try:
        sig = inspect.signature(callable_)
        if not any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values()):
            extras = {k: v for k, v in extras.items() if k in sig.parameters}
    except (ValueError, TypeError):
        pass
    return callable_(project, params, **extras)


def _mirror_event_into_index(rt, job_id, ev) -> None:
    """Project a render lifecycle event into the index: progress, cost, cached,
    last_event_id, artifact_ref."""
    fields = _event_fields(ev)
    kind = _event_kind(ev)
    with rt.lock:
        record = _index_get_locked(rt, job_id)
        if record is None:
            return
        progress = dict(record.get("progress") or {})
        cost = dict(record.get("cost") or {})
        updates: dict[str, Any] = {}

        for src in ("stage_index", "stage_count", "current_transform", "fraction"):
            if src in fields:
                progress[src] = fields[src]
        cost_map = {
            "estimated_usd": "estimated_usd",
            "actual_usd": "actual_usd",
            "cost_usd": "actual_usd",
            "cache_hit_savings_usd": "cache_hit_savings_usd",
        }
        for src, dst in cost_map.items():
            if src in fields and fields[src] is not None:
                cost[dst] = fields[src]

        updates["progress"] = progress
        updates["cost"] = cost
        if kind == "cache_hit" or fields.get("cache_hit") or fields.get("cached"):
            updates["cached"] = True
        for src in ("last_event_id", "event_id", "id"):
            if fields.get(src) is not None:
                updates["last_event_id"] = fields[src]
                break
        for src in ("animatic_artifact_id", "artifact_id", "artifact_ref"):
            if fields.get(src) is not None:
                updates["artifact_ref"] = fields[src]
                break
        _index_update_locked(rt, job_id, **updates)


def _reconcile_terminal(rt, job_id, result) -> None:
    """Capture terminal payload fields (artifact, actual cost) the render returns
    but may not have emitted as events."""
    with rt.lock:
        record = _index_get_locked(rt, job_id)
        if record is None:
            return
        updates: dict[str, Any] = {"finished_at": _now_iso()}
        if isinstance(result, Mapping):
            cost = dict(record.get("cost") or {})
            if result.get("cost_usd_actual") is not None:
                cost["actual_usd"] = result["cost_usd_actual"]
            if result.get("cache_hit_savings_usd") is not None:
                cost["cache_hit_savings_usd"] = result["cache_hit_savings_usd"]
            updates["cost"] = cost
            for src in ("animatic_artifact_id", "artifact_id", "artifact_ref"):
                if result.get(src) is not None:
                    updates["artifact_ref"] = result[src]
                    break
        _index_update_locked(rt, job_id, **updates)


# ---------------------------------------------------------------------------
# Projection: index record + au status → Job (+ stale reaper, monotonic pct)
# ---------------------------------------------------------------------------


def _read_job(rt, record, config) -> Job:
    """Read one job: reap-if-stale, project, and persist a grown pct floor.

    Terminal-read consistency: a caller may hand us an index ``record`` snapshot
    taken *before* the worker mirrored the render's terminal fields (cost,
    artifact, finished_at), yet read the au store *after* the COMPLETED write.
    Because the worker commits those index writes strictly before writing the
    terminal au record, once we observe a terminal au status a fresh index
    re-read is guaranteed to include them — so a job observed ``succeeded`` never
    reports a stale/empty cost or a missing artifact_ref.
    """
    job_id = record["job_id"]
    au_result = rt.au_store[job_id]
    au_result = _maybe_reap(rt, record, au_result, config)
    if au_result.status in (
        ComputationStatus.COMPLETED,
        ComputationStatus.FAILED,
        ComputationStatus.CANCELLED,
    ):
        with rt.lock:
            record = _index_get_locked(rt, job_id) or record
    job = _project_job(rt, record, au_result, config)
    if job.status in (RUNNING, CANCELLING) and job.pct is not None:
        if record.get("pct_floor") is None or job.pct > record["pct_floor"]:
            with rt.lock:
                _index_update_locked(rt, job_id, pct_floor=job.pct)
    return job


def _maybe_reap(rt, record, au_result, config) -> ComputationResult:
    """Reap a RUNNING record whose worker is gone (no live thread + stale start)."""
    if au_result.status is not ComputationStatus.RUNNING:
        return au_result
    job_id = record["job_id"]
    if rt.middleware.is_running(job_id):
        return au_result  # a live worker in this process is on it
    started = _parse_iso(record.get("started_at") or record.get("created_at"))
    if (
        started is not None
        and (_utcnow() - started).total_seconds() < config.stale_running_s
    ):
        return au_result
    reaped = ComputationResult(
        None,
        ComputationStatus.FAILED,
        error=Exception(REAPED_REASON),
        terminal_reason=REAPED_REASON,
    )
    rt.au_store[job_id] = reaped
    with rt.lock:
        _index_update_locked(rt, job_id, finished_at=_now_iso(), error=REAPED_REASON)
    return rt.au_store[job_id]


def _project_job(rt, record, au_result, config) -> Job:
    """Pure projection of an index record + au result into a :class:`Job`."""
    cancel_requested = bool(record.get("cancel_requested"))
    status = _normalize_status(au_result.status, cancel_requested=cancel_requested)

    created_at = record.get("created_at")
    started_at = record.get("started_at")
    # Invariant (defense in depth): a job observable as running/cancelling/terminal
    # always has a started_at. The primary stamp is `before_compute`; here we
    # derive it from au's RUNNING-record `created_at` for any backend that might
    # write RUNNING without the index having caught up.
    if started_at is None and status != QUEUED and au_result.created_at:
        started_at = _iso(au_result.created_at)
    finished_at = record.get("finished_at")
    if finished_at is None and status in TERMINAL_STATUSES and au_result.completed_at:
        finished_at = _iso(au_result.completed_at)

    queue_wait_s = _duration_s(created_at, started_at)
    elapsed_s = _elapsed_running_s(started_at, finished_at)

    progress = _progress_from(record)
    cost = _cost_from(record)

    predicted_total_s = remaining_s = eta_s = None
    eta_ts = None
    pct = None
    confidence = label_hint = None
    eta_candidates = record.get("eta_candidates") or []
    eta_key = eta_candidates[0][0] if eta_candidates else None

    if status in (RUNNING, CANCELLING):
        prediction = predict_total_s(
            eta_candidates,
            record.get("output_kind"),
            durations=rt.durations,
            expected_cache_hit=record.get("expected_cache_hit", False),
            config=config,
        )
        predicted_total_s = prediction.p50
        confidence = prediction.confidence
        pct, remaining_s, label_hint = _compute_pct_eta(
            elapsed_s or 0.0, prediction, record, config
        )
        eta_s = remaining_s
        if remaining_s is not None:
            eta_ts = _iso(_utcnow_plus(remaining_s))
    elif status == SUCCEEDED:
        pct = 100

    result_payload = None
    error = None
    artifact_ref = record.get("artifact_ref")
    if status == SUCCEEDED:
        if isinstance(au_result.value, Mapping):
            result_payload = dict(au_result.value)
            if artifact_ref is None:
                artifact_ref = result_payload.get(
                    "animatic_artifact_id"
                ) or result_payload.get("artifact_id")
    elif status == FAILED:
        error = str(au_result.error) if au_result.error else record.get("error")
    elif status == CANCELLED:
        error = None

    return Job(
        job_id=record["job_id"],
        kind=record.get("kind", ""),
        label=record.get("label", ""),
        status=status,
        idempotency_key=record.get("idempotency_key", record["job_id"]),
        params=record.get("params", {}),
        created_at=created_at,
        started_at=started_at,
        finished_at=finished_at,
        queue_wait_s=queue_wait_s,
        elapsed_s=elapsed_s,
        progress=progress,
        predicted_total_s=predicted_total_s,
        remaining_s=remaining_s,
        eta_ts=eta_ts,
        eta_s=eta_s,
        pct=pct,
        confidence=confidence,
        label_hint=label_hint,
        eta_key=eta_key,
        cost=cost,
        cached=bool(record.get("cached")),
        artifact_ref=artifact_ref,
        result=result_payload,
        error=error,
        run_id=record["job_id"],
        last_event_id=record.get("last_event_id"),
    )


def _normalize_status(au_status: ComputationStatus, *, cancel_requested: bool) -> str:
    """au status ⊕ cancel intent → the 6-value job status.

    ``cancel_requested`` is authoritative: once the user cancels, the job reads
    ``cancelling`` (still in flight) → ``cancelled`` (terminal) regardless of
    whether the ``ThreadBackend`` thread later clobbers the au record with
    COMPLETED. A missing au key synthesizes PENDING → ``queued`` (membership is
    the index's job, not the au store's).
    """
    if cancel_requested:
        if au_status in (
            ComputationStatus.COMPLETED,
            ComputationStatus.FAILED,
            ComputationStatus.CANCELLED,
        ):
            return CANCELLED
        return CANCELLING
    if au_status is ComputationStatus.COMPLETED:
        return SUCCEEDED
    if au_status is ComputationStatus.CANCELLED:
        return CANCELLED
    if au_status is ComputationStatus.FAILED:
        return FAILED
    if au_status is ComputationStatus.RUNNING:
        return RUNNING
    return QUEUED


def _compute_pct_eta(elapsed_s, prediction: _Prediction, record, config):
    """The §5.4 progress-% + remaining-seconds derivation (honest + monotonic)."""
    p50, p90, confidence = prediction.p50, prediction.p90, prediction.confidence
    label_hint = None

    if confidence == "prior":
        remaining = max(0.0, p50 - elapsed_s)
        return None, remaining, "estimating…"
    if confidence == "exact":
        remaining = max(0.0, p50 - elapsed_s)
        return None, remaining, "cached · instant"

    if elapsed_s <= p50:
        pct = (
            min(100.0 * elapsed_s / p50, config.pct_ceil)
            if p50 > 0
            else config.pct_ceil
        )
        remaining = max(0.0, p50 - elapsed_s)
    else:
        pct = min(90.0 + 9.0 * (elapsed_s - p50) / max(p90 - p50, 1.0), config.pct_ceil)
        remaining = max(0.0, p90 - elapsed_s)
        if elapsed_s > p90:
            pct, label_hint, remaining = float(config.pct_ceil), "finishing…", None

    floor = record.get("pct_floor")
    if floor is not None:
        pct = max(pct, floor)
    return int(pct), remaining, label_hint


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _index_get_locked(rt, job_id):
    return rt.index.get(job_id)


def _index_set_locked(rt, job_id, record) -> None:
    rt.index[job_id] = record


def _index_update_locked(rt, job_id, **fields) -> None:
    record = rt.index.get(job_id)
    if record is None:
        return
    record.update(fields)
    rt.index[job_id] = record


def _default_idempotency_key(project, kind, params) -> str:
    """``sha256(project:kind:basis)`` where basis is ``falaw.plan_hash`` of a
    supplied ``params["plan"]`` (plan-scoped dedup) or a stable hash of params."""
    basis = None
    plan = params.get("plan") if isinstance(params, Mapping) else None
    if plan is not None:
        try:
            from falaw import plan_hash

            basis = plan_hash(plan)
        except Exception:
            basis = None
    if basis is None:
        basis = json.dumps(_jsonable(params), sort_keys=True, default=str)
    blob = f"{Path(project.root).resolve()}:{kind}:{basis}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _jsonable(params):
    """Drop a non-serializable ``plan`` object from the params-hash basis (its
    identity is already captured via ``plan_hash``)."""
    if isinstance(params, Mapping):
        return {k: v for k, v in params.items() if k != "plan"}
    return params


def _default_label(kind, params) -> str:
    model = params.get("model") or params.get("application")
    pretty = kind.replace("_", " ").replace(".", " · ")
    if model:
        return f"{pretty} · {str(model).rsplit('/', 1)[-1]}"
    return pretty


def _eta_candidates(params, config):
    """Ordered ``[(key, confidence), …]`` ETA-key candidates (most specific first)
    plus the inferred output kind."""
    app = params.get("model") or params.get("application")
    tool = params.get("tool") or params.get("operation")
    output_kind = params.get("output_kind") or _infer_output_kind(params, app, tool)
    dur_bucket = _dur_bucket(params.get("duration_s"), config)

    candidates: list[tuple[str, str]] = []
    if app and tool and dur_bucket is not None:
        candidates.append((f"{app}|{tool}|{dur_bucket}", "learned"))
    if app and tool:
        candidates.append((f"{app}|{tool}", "learned"))
    if output_kind:
        candidates.append((output_kind, "learned_coarse"))
    return candidates, output_kind


def _infer_output_kind(params, app, tool) -> str | None:
    hay = " ".join(str(x) for x in (tool, app, params.get("kind")) if x).lower()
    if any(w in hay for w in ("video", "animate", "clip", "motion", "lipsync")):
        return "video"
    if any(w in hay for w in ("voice", "audio", "tts", "narration", "speech", "music")):
        return "audio"
    if any(w in hay for w in ("image", "panel", "still", "frame", "picture")):
        return "image"
    return None


def _dur_bucket(duration_s, config) -> str | None:
    if duration_s is None:
        return None
    edges = config.dur_buckets_s
    if not edges:
        return None
    if duration_s <= edges[0]:
        return f"<={int(edges[0])}s"
    for i in range(1, len(edges)):
        if duration_s <= edges[i]:
            return f"{int(edges[i - 1]) + 1}-{int(edges[i])}s"
    return f">{int(edges[-1])}s"


def _durations_get(durations: MutableMapping, eta_key: str) -> list:
    # Reads can race a concurrent (non-atomic) sample write from the learning
    # middleware; a half-written JSON file is transient — treat it as no history.
    try:
        record = durations.get(_safe_key(eta_key))
    except Exception:
        return []
    if not record:
        return []
    return list(record.get("samples", []))


def _durations_put(durations: MutableMapping, eta_key: str, samples: list) -> None:
    durations[_safe_key(eta_key)] = {"eta_key": eta_key, "samples": samples}


def _safe_key(eta_key: str) -> str:
    return urllib.parse.quote(eta_key, safe="")


def _percentile(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def _progress_from(record) -> JobProgress:
    p = record.get("progress") or {}
    return JobProgress(
        stage_index=p.get("stage_index"),
        stage_count=p.get("stage_count"),
        current_transform=p.get("current_transform"),
        fraction=p.get("fraction"),
    )


def _cost_from(record) -> JobCost:
    c = record.get("cost") or {}
    return JobCost(
        estimated_usd=c.get("estimated_usd"),
        actual_usd=c.get("actual_usd"),
        cache_hit_savings_usd=c.get("cache_hit_savings_usd"),
    )


def _stamp_correlation(ev, job_id) -> None:
    try:
        fields = getattr(ev, "fields", None)
        if isinstance(fields, MutableMapping):
            fields.setdefault("job_id", job_id)
            fields.setdefault("run_id", job_id)
        elif isinstance(ev, MutableMapping):
            ev.setdefault("job_id", job_id)
            ev.setdefault("run_id", job_id)
    except Exception:
        pass


def _event_fields(ev) -> Mapping:
    fields = getattr(ev, "fields", None)
    if isinstance(fields, Mapping):
        return fields
    if isinstance(ev, Mapping):
        return ev
    return {}


def _event_kind(ev):
    for attr in ("kind", "name", "type"):
        v = getattr(ev, attr, None)
        if isinstance(v, str):
            return v
    if isinstance(ev, Mapping):
        for k in ("kind", "name", "type"):
            if isinstance(ev.get(k), str):
                return ev[k]
    return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_plus(seconds: float) -> datetime:
    from datetime import timedelta

    return _utcnow() + timedelta(seconds=seconds)


def _now_iso() -> str:
    return _iso(_utcnow())


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _duration_s(a_iso, b_iso):
    a, b = _parse_iso(a_iso), _parse_iso(b_iso)
    if a is None or b is None:
        return None
    return max(0.0, (b - a).total_seconds())


def _elapsed_running_s(started_at, finished_at):
    start = _parse_iso(started_at)
    if start is None:
        return None
    end = _parse_iso(finished_at) or _utcnow()
    return max(0.0, (end - start).total_seconds())
