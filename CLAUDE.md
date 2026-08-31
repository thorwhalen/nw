# nw — agent entry point

`nw` (Narrative Workflow) is the **orchestration substrate** of the video_gen
stack: the layer where lacing's annotation graph meets falaw's costed
execution. reelee, muvid, and braidio build audiovisual production apps on
it. Layering: `lacing → nw → falaw.Plan → backends` — nothing above
`falaw.Plan` may know which backend runs.

## The four surfaces (each module's docstring is the spec)

- **Transforms** (`nw/transforms/`) — the swappable, costed
  "A-annotation → B-annotation" step. Contract: `name` (registry key, denotes
  the *capability*), `input_kinds`/`output_kind` (body-schema URIs;
  registration refuses an empty `output_kind`), `is_batch`, `params_model`,
  and `impl_version` — a **lock, not a receipt**: "same interface, changed
  behaviour" bumps it *without renaming*, it enters provenance
  (`transform:<name>@<impl_version>`) and salts the falaw cache key
  (`stamp_transform_identity`; omit-if-default, so keys only change on a
  real bump). A Transform overriding `execute` must stamp itself.
  `transform_catalog()` is the JSON-able capability surface for agent/MCP
  builders. Fan-out (nw#26, `nw/transforms/fanout.py` — its docstring is the
  spec): PDG-shaped `WorkItem`s whose `mapping_key` must be deterministic AND
  semantic (bare integers and UUIDs are *refused at validation*), instance
  ids a pure function of `(transform_name, mapping_key)` (UUIDv5 — async-safe,
  stable under insertion), `generate_when: static|dynamic` on the declaration
  (default `dynamic`: fail expensive-looking; it is what lets a cost gate tell
  a real pre-quote from an honest unknown), per-unit isolation in
  `fan_out_execute` on top of falaw's per-call isolation, and work items in
  the **run record** (`FanOutResult.to_record()`), never the graph document.
- **Freshness** (`nw/freshness.py`) — verifying-trace rebuild analysis with
  early cutoff (Salsa-style backdating). `stale_verdicts`/`stale_after`
  answer "what did *this* change invalidate"; `stale_verdicts_all`/`all_stale`
  answer "what is stale *right now*". Invariants: unverifiable means stale;
  parentless annotations are never stale; the module docstring states the
  exact rule and the four cases it deliberately does not catch.
- **Graph** (`nw/graph.py`) — `ProjectGraph` over lacing stores.
  `add_annotation` is **the single choke point every derived annotation
  passes through** and records the verifying trace at persist time; bypass
  it (raw `store.add`) and freshness classifies the result stale-forever.
  Deletion routes collect the annotation's traces; `collect_orphan_traces`
  is the GC backstop.
- **Genres & jobs** (`nw/genres.py`, `nw/jobs.py`) — the genre/template
  registry (`genre_catalog()`, resolved envelopes persisted on the project)
  and durable async jobs (idempotency by `falaw.plan_hash`; store writes are
  atomic via temp-file + `os.replace`).

## Invariants that are easy to violate

1. **Plans are pure data.** No network, no billing, in any `plan()`.
2. **Costs: `None` means unknown, never free.** Read
   `ExecutionReport.estimated_spend_usd` (observed), not per-artifact sums.
3. **Cache identity is explicit.** A behaviour change must reach the cache
   key (`impl_version` → `key_extra`), or a stale artifact is served forever.
4. **Provenance edges are load-bearing.** `was_derived_from` is what
   freshness walks; write derived annotations through `ProjectGraph`.
5. **Tests never spend.** The suite is hermetic (falaw.testing transport +
   no-outbound guard); nw has no live-API tests.

## Where to look

- `README.md` — user-facing tour; genre quickstart; the freshness story.
- Module docstrings are maintained as specs — `freshness.py`'s and
  `transforms/__init__.py`'s are the authoritative statements of their
  contracts.
- `misc/docs/` — design notes (execution semantics, fan-out).
- Issue tracker — issues carry implementation sketches and are kept honest;
  read the issue before re-deriving a design.
