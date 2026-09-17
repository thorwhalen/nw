# nw.jobs

nw.jobs — a project-scoped async **job** facade over `au`.

A “job” is one long, cancellable unit of render work (a full-auto journey, a
single `panel.animate`, an `assemble_animatic` pass, …). It has a durable
id, a persistent terminal state, live progress + ETA, a cost, and a cancel
entry keyed by that id — everything a task tray needs and none of which a bare
HTTP request provides.

This module is the *only* place async substance lives for the render layer:
`au` supplies the submit → poll → result skeleton and the durable store;
`nw.jobs` adds the five render-domain concerns `au` has no data model for —
progress %, ETA, human label/kind, idempotency (dedup), and cost — plus the
5-state normalization, context-capture so a job outlives its request, and the
active-jobs index that makes membership meaningful. Consumers (reelee’s
`/api/jobs` closures) stay thin: they call [`enqueue()`](#nw.jobs.enqueue) / [`estimate()`](#nw.jobs.estimate)
/ [`list_jobs()`](#nw.jobs.list_jobs) / [`get_job()`](#nw.jobs.get_job) / [`cancel_job()`](#nw.jobs.cancel_job) / [`to_dict()`](#nw.jobs.to_dict)
and serialize the result.

Design decisions (from the `nw.jobs`-on-`au` design report,
`misc/docs/research/async-job-manager-on-au.md`):

- \*\*Backend = `au.ThreadBackend``**, not ``ProcessBackend`. A fal render is
  I/O-bound (a blocking upstream wait), and the worker must share the live
  in-process `Project` graph and write events to the same channel the
  existing SSE tails. Process isolation buys no real fal cancel (fal still
  bills) while breaking the live-progress channel. Where bounded concurrent
  paid renders matter, swap in `StdLibQueueBackend(use_processes=False)` —
  a construction detail behind this facade.
- **au store is SSOT for \*status\* only.** `ThreadBackend` overwrites the
  store record with a bare `ComputationResult` at start (RUNNING) and end
  (COMPLETED), carrying no metadata — so every job-semantic field lives in a
  per-project **active-jobs index** (a `dol` mapping), which `au` cannot
  clobber. The index is also the membership authority: `au.FileSystemStore`
  synthesizes `PENDING` for a missing key, so store membership is
  meaningless.
- **Idempotency**: the au store key *is* the idempotency key
  (`sha256(project:kind:plan_hash-or-params)`). A resubmit while a job with
  that key is live returns the existing job instead of launching a duplicate.
- **Cancel is boundary-grained and race-proof.** `cancel_job` flips the au
  record terminal *and* sets a durable should-cancel flag; the `cancel_requested`
  flag is the authority for cancellation intent, so a job reads
  `cancelling` → `cancelled` regardless of whether the still-running
  `ThreadBackend` thread later clobbers the store with COMPLETED.
- **ETA is learned from observed wall-time**, per `(model, operation, dur_bucket)`
  with back-off, median (not mean), honest “estimating…” before enough
  history, and **cache-hits excluded from learning**.
- **A cost never travels without its honesty flag.** `JobCost.actual_usd` is
  paired with `actual_is_lower_bound`, because an unpriceable call that
  actually billed contributes `0.0` to the sum — so a bare `$0` means
  *either* “nothing was spent” *or* “we do not know what was spent”, and a
  spend surface that cannot tell them apart shows the second as free.
- **An estimate is re-quoted, never remembered.** When `params["plan"]` is
  supplied, [`estimate()`](#nw.jobs.estimate) and [`enqueue()`](#nw.jobs.enqueue) price it through
  [`nw.pricing.current_quote()`](nw.pricing.html.md#nw.pricing.current_quote) at today’s rates rather than trusting a
  > caller-supplied `estimated_usd` frozen at plan time — falaw’s rate tables
  > move, and a stale figure under-quotes the run (nw#74). Descriptive only:
  > `cost_basis` stays out of `plan_hash`, so the idempotency key above is
  > byte-identical to what it was before repricing existed, and a resumed render
  > still dedups onto work already paid for.

All tunables are keyword-configurable via [`JobsConfig`](#nw.jobs.JobsConfig); defaults live at
the top of this module — no magic numbers below.

### Module Attributes

| [`UNREADABLE_PLAN_REASON`](#nw.jobs.UNREADABLE_PLAN_REASON)   | Why a quote came back unknown when the plan itself was unreadable.   |
|---------------------------------------------------------------------------|----------------------------------------------------------------------|

### Functions

| [`cancel_job`](#nw.jobs.cancel_job)(project, job_id, \*[, config])         | Request cancellation.                                                                                       |
|----------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| [`enqueue`](#nw.jobs.enqueue)(project, kind, params, \*[, ...])         | Enqueue a billable render as a background job.                                                              |
| [`estimate`](#nw.jobs.estimate)(project, kind, params, \*[, config])     | Dry-run cost gate **without enqueueing**.                                                                   |
| [`get_job`](#nw.jobs.get_job)(project, job_id, \*[, config])            | One job (projecting the au status + mirrored index metadata).                                               |
| [`list_jobs`](#nw.jobs.list_jobs)(project, \*[, status, limit, config])   | Jobs for this project, **newest first**, optionally filtered by status.                                     |
| [`predict_total_s`](#nw.jobs.predict_total_s)(eta_candidates, output_kind, ...) | Predict the total render seconds for a job as `(p50, p90, confidence)`.                                     |
| [`summarize`](#nw.jobs.summarize)(project_root, \*[, limit, config])      | This project's jobs **without provisioning it**.                                                            |
| [`to_dict`](#nw.jobs.to_dict)(job)                                      | Serialize a [`Job`](#nw.jobs.Job) to the JSON contract (design report §7.4). |

### Classes

| [`DurationLearningMiddleware`](#nw.jobs.DurationLearningMiddleware)(\*, durations, ...)    | Keyed, percentile duration learner — a cousin of `au.MetricsMiddleware`.                                       |
|----------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------|
| [`Job`](#nw.jobs.Job)(job_id, kind, label, status, idempotency_key) | Projected, JSON-serializable view of one job (see [`to_dict()`](#nw.jobs.to_dict)). |
| [`JobCost`](#nw.jobs.JobCost)([estimated_usd, actual_usd, ...])         |                                                                                                                |
| [`JobProgress`](#nw.jobs.JobProgress)([stage_index, stage_count, ...])      |                                                                                                                |
| [`JobsConfig`](#nw.jobs.JobsConfig)([n_min, sample_window_k, ...])         | Tunables for the job manager.                                                                                  |

### *class* nw.jobs.DurationLearningMiddleware(, durations, index, lock, config=JobsConfig(n_min=3, sample_window_k=20, pct_ceil=99, overrun_factor=1.5, cache_hit_floor_s=0.5, dur_buckets_s=(4.0, 8.0, 12.0), prior_total_s={'image': 12.0, 'video': 90.0, 'audio': 15.0}, default_prior_total_s=30.0, stale_running_s=900.0, heartbeat_interval_s=20.0, heartbeat_stale_s=120.0, approval_threshold_usd=1.0, jobs_dirname='.nw/jobs'))

Bases: `Middleware`

Keyed, percentile duration learner — a cousin of `au.MetricsMiddleware`.

Records the render wall-time under every ETA-key candidate for the job, so
a cold specific key backs off to a warmer coarse one. Two deliberate
departures from `au`’s built-in metrics:

- **Self-timed** (`time.monotonic` in `before_compute` → `after_compute`)
  rather than reading `result.duration`: `ThreadBackend` constructs a
  *fresh* COMPLETED `ComputationResult` whose `created_at` is the
  completion instant, so `result.duration` is ~0 and useless. (Surfaced
  as an `au` finding.)
- **Cache-hits are never learned** — a ~0s cache hit would drag the median
  to zero. The job’s `cached` flag (mirrored from a `cache_hit` event
  during the run) gates recording.

Its `_start` map doubles as the in-process **liveness signal** the stale
reaper uses (a RUNNING au record whose key is not in `_start` and whose
`started_at` is old is a dead worker).

#### after_compute(key, result)

Called after computation completes.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### before_compute(func, args, kwargs, key)

Called before computation starts.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

#### on_error(key, error)

Called when computation fails.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### *class* nw.jobs.Job(job_id, kind, label, status, idempotency_key, params=<factory>, created_at=None, started_at=None, finished_at=None, queue_wait_s=None, elapsed_s=None, progress=<factory>, predicted_total_s=None, remaining_s=None, eta_ts=None, eta_s=None, pct=None, confidence=None, label_hint=None, eta_key=None, cost=<factory>, cached=False, worker_silent_s=None, worker_responsive=None, artifact_ref=None, result=None, error=None, run_id=None, last_event_id=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Projected, JSON-serializable view of one job (see [`to_dict()`](#nw.jobs.to_dict)).

#### worker_responsive *: [bool](https://docs.python.org/3/builtins/functions.html#bool) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

Whether the worker is provably still alive, **by the reaper’s own rule**.

Derived from the same `_heartbeat_is_fresh` predicate `_maybe_reap`
consults, so a UI and the reaper can never disagree about what “alive”
means — a second definition living in a client is how a screen ends up
insisting a job is fine while the server is failing it.

`None` means *unknowable*, not *dead*: a job that is not running, or one
that never beat. `False` is a positive claim that contact has been lost.

#### worker_silent_s *: [float](https://docs.python.org/3/builtins/functions.html#float) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

Seconds since this job’s worker last stamped a heartbeat.

`None` when it has never beaten — a record written before heartbeats
existed, or a worker that died before its first beat. Absence is not a
duration, and rendering `None` as `0` would report the silent case as
the healthiest one.

### *class* nw.jobs.JobCost(estimated_usd=None, actual_usd=None, cache_hit_savings_usd=None, actual_is_lower_bound=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

#### actual_is_lower_bound *: [bool](https://docs.python.org/3/builtins/functions.html#bool) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

True when the run reported `has_unknown_costs` — some call that
actually billed had no price, so `actual_usd` UNDER-states the spend.

Its whole job is to keep a `$0` readable. Without it, `actual_usd`
conflates two answers a spend surface must never merge: “this run cost
nothing” (a cache hit — a known zero) and “we do not know what this run
cost” (an unpriceable call ran). A UI that renders the second as *free*,
or a bound that reads it as *under budget*, is the failure this field
exists to make impossible.

`None` means the render never reported either way — an older caller, or
a job that died before finishing. `None` is not `False`: absence of the
flag is not a claim that the total is exact.

#### estimated_usd *: [float](https://docs.python.org/3/builtins/functions.html#float) | [None](https://docs.python.org/3/builtins/constants.html#None)* *= None*

Predicted spend, re-quoted at today’s rates when a plan was supplied.

`None` means unknown, and unknown always requires approval — it is what
a plan whose calls carry no `falaw.CostBasis` re-prices to. Never
the plan-time figure passed in alongside such a plan: that number was true
when it was written and falaw’s rate tables have moved since (nw#74).

### *class* nw.jobs.JobProgress(stage_index=None, stage_count=None, current_transform=None, fraction=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

### *class* nw.jobs.JobsConfig(n_min=3, sample_window_k=20, pct_ceil=99, overrun_factor=1.5, cache_hit_floor_s=0.5, dur_buckets_s=(4.0, 8.0, 12.0), prior_total_s=<factory>, default_prior_total_s=30.0, stale_running_s=900.0, heartbeat_interval_s=20.0, heartbeat_stale_s=120.0, approval_threshold_usd=1.0, jobs_dirname='.nw/jobs')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Tunables for the job manager. All keyword-configurable; sensible defaults.

ETA knobs (`n_min` … `prior_total_s`) mirror the design report §5.7.

#### approval_threshold_usd *: [float](https://docs.python.org/3/builtins/functions.html#float)* *= 1.0*

Estimated cost at/above which a render requires explicit approval.

#### cache_hit_floor_s *: [float](https://docs.python.org/3/builtins/functions.html#float)* *= 0.5*

Predicted total for an all-cache-hit plan (`confidence="exact"`).

#### default_prior_total_s *: [float](https://docs.python.org/3/builtins/functions.html#float)* *= 30.0*

Prior when even the output kind is unknown.

#### dur_buckets_s *: [tuple](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[float](https://docs.python.org/3/builtins/functions.html#float), ...]* *= (4.0, 8.0, 12.0)*

Upper edges of the output-duration buckets for `per_second` models.

#### heartbeat_interval_s *: [float](https://docs.python.org/3/builtins/functions.html#float)* *= 20.0*

How often a running worker stamps `heartbeat_at` on its index record.

Liveness has to be a fact in the **shared store**, not in one process’s
memory, or a second API replica cannot tell a live job from a dead one.
Cheap: one small atomic file write per job per interval.

#### heartbeat_stale_s *: [float](https://docs.python.org/3/builtins/functions.html#float)* *= 120.0*

A heartbeat younger than this proves the worker is alive **anywhere**.

Generously larger than `heartbeat_interval_s`: the beat is a Python
thread, and a render holding the GIL in a C extension can delay it well
past one interval. The asymmetry is deliberate — a late beat costs a
slower reap, while an eager one destroys a live job’s record.

#### jobs_dirname *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)* *= '.nw/jobs'*

Sub-path under `project.root` for the job stores (nw’s `.nw/` convention).

#### n_min *: [int](https://docs.python.org/3/builtins/functions.html#int)* *= 3*

Minimum samples for a key before its prediction is `"learned"` (not prior).

#### overrun_factor *: [float](https://docs.python.org/3/builtins/functions.html#float)* *= 1.5*

Synthesize `p90 = p50 * overrun_factor` when a real p90 isn’t available.

#### pct_ceil *: [int](https://docs.python.org/3/builtins/functions.html#int)* *= 99*

Never *compute* 100% — only the → succeeded transition sets 100.

#### prior_total_s *: [Mapping](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [float](https://docs.python.org/3/builtins/functions.html#float)]*

Cold-start priors by output kind (drives the honest “estimating…” label).

#### sample_window_k *: [int](https://docs.python.org/3/builtins/functions.html#int)* *= 20*

Keep only the most-recent K duration samples per key (robust to drift).

#### stale_running_s *: [float](https://docs.python.org/3/builtins/functions.html#float)* *= 900.0*

A RUNNING record older than this with no live worker is reaped as
`FAILED("worker died — resumable")` (kills the stuck-toast bug).

### nw.jobs.UNREADABLE_PLAN_REASON *= "params['plan'] could not be read as a falaw Plan, so its cost cannot be quoted; see the server log for what went wrong"*

Why a quote came back unknown when the plan itself was unreadable.

A fixed sentence rather than the exception text: this string reaches a job
surface, and an exception’s message is written for an operator reading a log,
not for whoever is deciding whether to approve a spend.

### nw.jobs.cancel_job(project, job_id, , config=JobsConfig(n_min=3, sample_window_k=20, pct_ceil=99, overrun_factor=1.5, cache_hit_floor_s=0.5, dur_buckets_s=(4.0, 8.0, 12.0), prior_total_s={'image': 12.0, 'video': 90.0, 'audio': 15.0}, default_prior_total_s=30.0, stale_running_s=900.0, heartbeat_interval_s=20.0, heartbeat_stale_s=120.0, approval_threshold_usd=1.0, jobs_dirname='.nw/jobs'))

Request cancellation. **Idempotent.** `None` if unknown.

Sets a durable should-cancel flag (a running stage stops at its next
boundary) *and* flips the au record terminal at once via `au.cancel_task`
(which builds the handle **with the backend** so `terminate` is reached).
The `cancel_requested` flag is authoritative, so the job reads
`cancelling` → `cancelled` even if the still-running thread later
overwrites the store with COMPLETED.

**A job that has already finished is not cancelled — the call is a no-op**
and returns the job unchanged. Because the flag is authoritative for
status, setting it on a terminal record rewrote a SUCCEEDED job to
`cancelled` and dropped its `result` and `pct` (and a FAILED job’s
`error`): work that ran, finished and *billed*, reported as though it had
not. Cancelling is a request about the future, and there is no future left
to change.

* **Return type:**
  [`Job`](#nw.jobs.Job) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### nw.jobs.enqueue(project, kind, params, , on_event=None, dispatch=None, backend=None, idempotency_key=None, label=None, capture_context=None, secrets=None, config=JobsConfig(n_min=3, sample_window_k=20, pct_ceil=99, overrun_factor=1.5, cache_hit_floor_s=0.5, dur_buckets_s=(4.0, 8.0, 12.0), prior_total_s={'image': 12.0, 'video': 90.0, 'audio': 15.0}, default_prior_total_s=30.0, stale_running_s=900.0, heartbeat_interval_s=20.0, heartbeat_stale_s=120.0, approval_threshold_usd=1.0, jobs_dirname='.nw/jobs'))

Enqueue a billable render as a background job. Returns a [`Job`](#nw.jobs.Job)
(`status="queued"`) **immediately** — the call never blocks and never
bills on the calling thread.

The request context (fal credentials + the resolved `Project`) is
captured now and re-established inside the worker so the job outlives its
request. If a *live* (non-terminal) job with the same `idempotency_key`
already exists, that job is returned instead of launching a duplicate.

* **Parameters:**
  * **project** – the `nw.Project` the render operates on.
  * **kind** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – dispatch key selecting the render callable (e.g.
    `"journey.full_auto"`, `"panel.animate"`).
  * **params** ([`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)) – render parameters (also the ETA-key + default-idempotency
    basis). A `"plan"` entry must be a **\`\`falaw.plan_to_dict\`\`
    dict, not a live** `falaw.Plan`: the whole `params`
    mapping is JSON-serialized into the job index, so a `Plan`
    object raises there. The dict hashes to the identical
    `plan_hash` (`_plan_for_identity()`) and re-quotes the same
    way, so nothing is lost by serializing it — [`estimate()`](#nw.jobs.estimate),
    which never writes a record, accepts either.
  * **on_event** ([`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`Any`](https://docs.python.org/3/library/typing.html#typing.Any)], [`None`](https://docs.python.org/3/builtins/constants.html#None)] | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – sink for the render’s lifecycle events (reelee wires this to
    its `agent_log` / SSE tail). Events are stamped with
    `job_id`/`run_id` and mirrored into progress/cost/eta.
  * **dispatch** ([`Mapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)] | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – `{kind: callable}` table. Each callable is invoked as
    `callable(project, params, *, job_id, on_event, should_cancel)`
    (only the kwargs it declares are passed) and should return a
    JSON-serializable result payload.
  * **backend** (`ComputationBackend` | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – advanced override; default is the managed `ThreadBackend`
    (which carries the duration-learning middleware + liveness map).
  * **idempotency_key** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – dedup handle; default derived from `falaw.plan_hash`
    of `params["plan"]` when present, else a stable hash of params.
  * **label** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – human tray label; default derived from `kind`/`params`.
  * **capture_context** ([`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[], [`AbstractContextManager`](https://docs.python.org/3/library/contextlib.html#contextlib.AbstractContextManager)[[`Any`](https://docs.python.org/3/library/typing.html#typing.Any)]] | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – optional caller-supplied context hook. Called **now**
    (on the request thread) to snapshot any request-scoped state the
    caller needs re-established inside the worker, returning a context
    manager entered around the render on the worker thread. `nw.jobs`
    handles fal credentials itself (`falaw` is its dependency); this
    hook is how a caller re-binds credentials it owns without `nw`
    importing them — e.g. reelee’s BYO vision (aix) + ElevenLabs keys,
    which otherwise fall back to owner/env in a background job because
    `ThreadBackend` does not copy `ContextVars` into the worker.
  * **secrets** ([`Mapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)] | [`None`](https://docs.python.org/3/builtins/constants.html#None)) – the caller’s per-call credentials ([`nw.Secrets`](nw.html.md#nw.Secrets); any
    mapping is coerced), for a render that spends a bring-your-own
    key. Held **in memory only**: never written to the job index
    (`params` is — never put a key there), never logged, and it
    reaches the render callable only when that callable declares a
    `secrets` keyword — the same accepts-it-or-not rule as
    `job_id` / `on_event` / `should_cancel`. A `"fal"` secret
    is also bound as the worker’s fal credential
    ([`nw.secrets.using_secrets()`](nw.secrets.html.md#nw.secrets.using_secrets)), innermost, so an explicit key
    wins over an ambient one. The explicit counterpart of
    `capture_context`: what that hook re-binds ambiently, this
    threads by hand.
  * **config** ([`JobsConfig`](#nw.jobs.JobsConfig)) – tunables (see [`JobsConfig`](#nw.jobs.JobsConfig)).
* **Raises:**
  [**KeyError**](https://docs.python.org/3/builtins/exceptions.html#KeyError) – if `kind` is not in `dispatch`.
* **Return type:**
  [`Job`](#nw.jobs.Job)

### nw.jobs.estimate(project, kind, params, , config=JobsConfig(n_min=3, sample_window_k=20, pct_ceil=99, overrun_factor=1.5, cache_hit_floor_s=0.5, dur_buckets_s=(4.0, 8.0, 12.0), prior_total_s={'image': 12.0, 'video': 90.0, 'audio': 15.0}, default_prior_total_s=30.0, stale_running_s=900.0, heartbeat_interval_s=20.0, heartbeat_stale_s=120.0, approval_threshold_usd=1.0, jobs_dirname='.nw/jobs'))

Dry-run cost gate **without enqueueing**.

Returns `{estimated_usd, has_unknown_costs, approval_threshold_usd,
requires_approval, quote}`. Unknown cost always requires approval
(preserves the one-price-per-clip gate).

**The gate quotes, it does not remember.** When `params["plan"]` is
present its cost is re-quoted at today’s rates through
[`nw.pricing.current_quote()`](nw.pricing.html.md#nw.pricing.current_quote), and a caller-supplied
`params["estimated_usd"]` is ignored — exactly as
`_default_idempotency_key()` ignores it for the dedup basis. A
persisted plan’s figure is frozen at plan time, and falaw’s rate tables
move (0.0.46 re-quoted premium LLM calls tenfold upward), so gating on
the stored number under-quotes the run: the one direction a spend
decision must never err in (nw#74).

A supplied plan that cannot be re-quoted — no `cost_basis` on its calls,
an unparseable payload, a model that has left the catalogue — yields
`estimated_usd=None`, which requires approval. Refusing to name a price
is the safe answer; repeating yesterday’s is not.

Without a plan the gate falls back to `params["estimated_usd"]`, whose
provenance nw cannot see; `quote` is then `None` to say so. When there
*is* a quote it carries `caller_estimated_usd` — what the caller passed,
reported beside today’s number rather than discarded, so a surface can
show the movement.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### nw.jobs.get_job(project, job_id, , config=JobsConfig(n_min=3, sample_window_k=20, pct_ceil=99, overrun_factor=1.5, cache_hit_floor_s=0.5, dur_buckets_s=(4.0, 8.0, 12.0), prior_total_s={'image': 12.0, 'video': 90.0, 'audio': 15.0}, default_prior_total_s=30.0, stale_running_s=900.0, heartbeat_interval_s=20.0, heartbeat_stale_s=120.0, approval_threshold_usd=1.0, jobs_dirname='.nw/jobs'))

One job (projecting the au status + mirrored index metadata). `None` if
unknown. Reaps a stale-RUNNING record on read.

* **Return type:**
  [`Job`](#nw.jobs.Job) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### nw.jobs.list_jobs(project, , status=None, limit=50, config=JobsConfig(n_min=3, sample_window_k=20, pct_ceil=99, overrun_factor=1.5, cache_hit_floor_s=0.5, dur_buckets_s=(4.0, 8.0, 12.0), prior_total_s={'image': 12.0, 'video': 90.0, 'audio': 15.0}, default_prior_total_s=30.0, stale_running_s=900.0, heartbeat_interval_s=20.0, heartbeat_stale_s=120.0, approval_threshold_usd=1.0, jobs_dirname='.nw/jobs'))

Jobs for this project, **newest first**, optionally filtered by status.

Backed by the per-project active-jobs index (not by scanning the au store,
whose missing-key-returns-PENDING gotcha makes membership meaningless).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Job`](#nw.jobs.Job)]

### nw.jobs.predict_total_s(eta_candidates, output_kind, , durations, expected_cache_hit=False, config=JobsConfig(n_min=3, sample_window_k=20, pct_ceil=99, overrun_factor=1.5, cache_hit_floor_s=0.5, dur_buckets_s=(4.0, 8.0, 12.0), prior_total_s={'image': 12.0, 'video': 90.0, 'audio': 15.0}, default_prior_total_s=30.0, stale_running_s=900.0, heartbeat_interval_s=20.0, heartbeat_stale_s=120.0, approval_threshold_usd=1.0, jobs_dirname='.nw/jobs'))

Predict the total render seconds for a job as `(p50, p90, confidence)`.

Walks the ETA-key candidates most-specific-first; the first key with
`>= n_min` samples wins (median, with a real or synthesized p90). Falls
back through the coarse key, the output-kind key, then the cold prior. An
all-cache-hit plan short-circuits to `cache_hit_floor_s` (`"exact"`).

* **Return type:**
  `_Prediction`

### nw.jobs.summarize(project_root, , limit=50, config=JobsConfig(n_min=3, sample_window_k=20, pct_ceil=99, overrun_factor=1.5, cache_hit_floor_s=0.5, dur_buckets_s=(4.0, 8.0, 12.0), prior_total_s={'image': 12.0, 'video': 90.0, 'audio': 15.0}, default_prior_total_s=30.0, stale_running_s=900.0, heartbeat_interval_s=20.0, heartbeat_stale_s=120.0, approval_threshold_usd=1.0, jobs_dirname='.nw/jobs'))

This project’s jobs **without provisioning it**. Newest first.

[`list_jobs()`](#nw.jobs.list_jobs) looks like a read and is not. It calls `_runtime()`,
which creates `.nw/jobs/{au,index,durations}` and memoizes a
`_JobsRuntime` — \*which owns a render `ThreadBackend``* — into the
unbounded module-global ``_RUNTIMES`. Measured on a never-rendered
project:

```default
before: .nw/jobs exists? False   _RUNTIMES size: 0
list_jobs(project)  ->  0 rows
after : .nw/jobs exists? True ['au', 'durations', 'index']
        _RUNTIMES size: 1, runtime owns a ThreadBackend
```

A cross-project dashboard polling N projects is exactly the caller that
turns that into render machinery for every project a user has ever glanced
at, held for the life of the process. So this reads what is there and
creates nothing: \*\*no directory, no backend, no `_RUNTIMES` entry\*\*, and
`[]` for a project that has never run a job.

It also performs neither write `_read_job()` does:

- **No reaping.** Marking someone else’s job failed is a write, and a
  read-only fan-out across projects has no business doing it. A job that
  needs reaping will be reaped by a caller that is actually looking at it.
- \*\*No `pct_floor` persistence.\*\* The monotonic floor is a write too.
  Progress may therefore appear to tick backwards *within this view* if a
  prediction lengthens; that is the honest cost of not writing, and the
  per-project view still holds the floor.

Status still comes from the au store, never from the index record’s own
`status` field: `FileSystemStore` synthesizes `PENDING` for a missing
key, so membership lives in the index while *outcome* lives in the store,
and trusting the index’s copy reports finished jobs as queued.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Job`](#nw.jobs.Job)]

### nw.jobs.to_dict(job)

Serialize a [`Job`](#nw.jobs.Job) to the JSON contract (design report §7.4).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)
