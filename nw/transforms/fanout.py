"""Fan-out — PDG-shaped work items with mandatory semantic identity (nw#26).

nw's fan-out primitive: apply one Transform across N units of work, where each
unit carries a **deterministic, semantic identity** and fails on its own.
The design is Houdini PDG's work-item model with Dagster's mandatory mapping
key; ComfyUI-style node expansion was surveyed and rejected (silent cache
death on per-item data, no per-instance retry, no shape declaration — the
issue records the evidence).

The pieces, and the rule each one carries:

- :class:`WorkItem` — the unit. Its ``mapping_key`` must be deterministic
  AND semantic: a ``uuid4`` silently disables the cache, and an ordinal
  shifts when the source material is edited (insert one scene early in a
  200-shot fan-out and every later ordinal misses — up to 199 spurious
  re-renders). Bare integers and UUIDs are therefore **refused at
  validation**, not discouraged in prose.
- :func:`work_item_instance_id` — instance identity is a **pure function**
  of ``(transform_name, mapping_key)`` (UUIDv5), never allocated from
  ambient state. Pure means async-safe by construction and stable under
  insertion; ComfyUI's mutable class-level prefix state is the
  counterexample (their own in-source TODO admits the coroutine race).
- ``generate_when`` on the Transform declaration — Houdini PDG's
  static/dynamic split, the one mechanism both research briefs nominated
  independently. ``"static"``: the item list is derivable before the run,
  so a fan-out pre-quote is a *real* number. ``"dynamic"``: cardinality is
  known only after an upstream call returns (segment-this-screenplay), so
  the only honest pre-quote is "unknown" — which the federation's cost
  rule already forces into approval. Undeclared defaults to ``"dynamic"``:
  fail expensive-looking.
- :func:`fan_out_plan` / :func:`fan_out_execute` — plan each unit as an
  **ordinary Transform invocation** (ordinary :class:`falaw.Plan`, ordinary
  ``lacing.Annotation`` skeletons — never a special "expanded" record
  type; the one thing ComfyUI gets right), then execute with per-unit
  isolation on top of falaw#20's per-call isolation.
- :class:`FanOutResult` — work items live in the **run record**
  (:meth:`FanOutResult.to_record`), never in the graph document.
  Materialising instances into the document would mutate it on execution
  and break its digest.

Deliberately NOT here (recorded in nw#26 so nobody re-litigates): a general
re-entrant scheduler (a fan-out of independent plans needs bounded
concurrency plus isolation, not a worklist — falaw bounds concurrency
*within* a unit already; cross-unit scheduling is future work this API does
not foreclose), and lazy inputs / ``required_inputs`` (separate issue when a
Transform actually needs them).

Import spelling: use the top-level ``nw`` exports or
``from nw.transforms.fanout import ...``. The attribute chain
``nw.transforms.fanout`` does NOT resolve — ``nw.transforms`` as an
*attribute of the nw package* is the transform Registry (a deliberate,
pre-existing shadowing), not this module's parent.
"""

from __future__ import annotations

import inspect
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from falaw import Plan
from lacing import Annotation, TimeInterval


GenerateWhen = Literal["static", "dynamic"]
"""When a Transform's fan-out cardinality is knowable.

``"static"`` — the work-item list is derivable from the graph before the run
("one image per panel": count the panels). A pre-flight estimate over a
static fan-out is a real number.

``"dynamic"`` — cardinality is known only after an upstream call returns
("segment this screenplay into beats"). The only honest pre-flight estimate
is *unknown*, which forces approval rather than under-quoting.
"""

DFLT_GENERATE_WHEN: GenerateWhen = "dynamic"
"""The default when a Transform declares nothing: fail expensive-looking.

An undeclared shape treated as static would let a cost gate quote a number
for a cardinality nobody knows yet — the exact under-quote the
``estimate() -> None`` rule exists to prevent."""

WORK_ITEM_NAMESPACE = uuid.UUID("0805b55c-002d-40c2-9ede-3dc06f4d636f")
"""UUIDv5 namespace for :func:`work_item_instance_id`. Frozen forever:
changing it changes every instance id ever issued."""


def _check_mapping_key(value: str, *, label: str = "mapping_key") -> str:
    """Refuse a key that is not deterministic-AND-semantic. Returns ``value``.

    Both halves of the requirement have a concrete failure mode, so both are
    validated rather than documented:

    - a **UUID** (any spelling ``uuid.UUID`` accepts — dashed, bare hex,
      braced, URN) is deterministic only if minted once and remembered,
      which in practice means ``uuid4()`` per run: the cache never hits.
    - a **bare integer** (``"12"``, ``"-3"``) is an ordinal: editing the
      source material shifts every subsequent id, so downstream cache
      entries miss from the insertion point on.

    ``"scene_12/shot_04"`` is the shape to aim for: stable under insertion,
    meaningful in a log, and the basis of "regenerate shot 4 of scene 12,
    leave the rest alone".
    """
    if not isinstance(value, str):
        raise ValueError(
            f"{label} must be a str, got {value!r} ({type(value).__name__})."
        )
    v = value.strip()
    if not v or v != value:
        raise ValueError(
            f"{label} must be a non-empty string without leading/trailing "
            f"whitespace, got {value!r}."
        )
    if "\x00" in v:
        raise ValueError(f"{label} may not contain NUL, got {value!r}.")
    stripped = v[1:] if v[0] in "+-" else v
    if stripped.isdigit():
        raise ValueError(
            f"{label} {value!r} is a bare integer — an ordinal. Ordinals "
            "shift when the source material is edited, invalidating every "
            "downstream cache entry after the insertion point. Use a "
            "semantic key like 'scene_12/shot_04'."
        )
    try:
        # Lowercased first: stdlib uuid.UUID's `urn:` handling is
        # case-sensitive, so "URN:UUID:..." would otherwise slip past a
        # probe the lowercase spelling fails.
        uuid.UUID(v.lower())
    except (ValueError, AttributeError, TypeError):
        return value
    raise ValueError(
        f"{label} {value!r} is a UUID. A per-run UUID silently disables "
        "the cache (a new key every run never hits), and a remembered one "
        "identifies nothing a human can act on. Use a semantic key like "
        "'scene_12/shot_04'."
    )


class WorkItem(BaseModel):
    """One unit of a fan-out — the PDG-shaped work item (nw#26).

    ``scope_interval`` puts *time in the demand, not the graph* (Nuke's
    model): a pipeline that stores frame ranges in nodes must edit the graph
    to change a range; one that stores them in the request does not. It is
    an interval rather than a point because lacing's ``TimeInterval`` admits
    ``start == end`` as a valid point annotation — the point-demand case is
    already representable, no second demand type needed.
    """

    model_config = ConfigDict(frozen=True)

    mapping_key: str = Field(
        ...,
        description=(
            "Deterministic, semantic identity for this unit, e.g. "
            "'scene_12/shot_04'. Bare integers and UUIDs are refused — "
            "see the module docstring for why both halves are mandatory."
        ),
    )
    parent_key: Optional[str] = Field(
        default=None,
        description=(
            "The mapping_key of the item this one was expanded from, when a "
            "fan-out is itself the product of a fan-out (screenplay → scenes "
            "→ shots)."
        ),
    )
    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Per-item data handed to the unit (seed, prompt override, …). "
            "Must be JSON-serializable if the run record is to be persisted."
        ),
    )
    scope_interval: Optional[TimeInterval] = Field(
        default=None,
        description=(
            "The stretch of the master timeline this unit is *for* — the "
            "demand, riding on the request rather than stored in the graph."
        ),
    )

    @field_validator("mapping_key")
    @classmethod
    def _valid_mapping_key(cls, v: str) -> str:
        return _check_mapping_key(v)

    @field_validator("parent_key")
    @classmethod
    def _valid_parent_key(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _check_mapping_key(v, label="parent_key")

    @property
    def instance_id(self) -> uuid.UUID:
        """This item's instance id is only defined *for a transform* — use
        :func:`work_item_instance_id`. This property exists to raise a
        helpful error instead of letting ``item.instance_id`` look like it
        could mean something transform-free."""
        raise AttributeError(
            "a WorkItem has no instance_id of its own — identity is "
            "work_item_instance_id(transform_name, item.mapping_key), a pure "
            "function of both."
        )


def work_item_instance_id(transform_name: str, mapping_key: str) -> uuid.UUID:
    """The instance id of one fan-out unit: UUIDv5 of ``(transform_name, mapping_key)``.

    A **pure function**, deliberately: pure is async-safe by construction
    (no ambient counter for a suspended coroutine to corrupt — the ComfyUI
    ``GraphBuilder`` race) and stable under insertion (adding an item never
    changes any other item's id). The same (transform, key) pair yields the
    same id on every machine, every run, forever — which is what makes
    per-instance retry, cost attribution, and "regenerate just this one"
    addressable across runs.

    >>> a = work_item_instance_id("panel_to_image.fal", "scene_1/panel_2")
    >>> a == work_item_instance_id("panel_to_image.fal", "scene_1/panel_2")
    True
    >>> a != work_item_instance_id("panel_to_voiceover", "scene_1/panel_2")
    True
    """
    if not transform_name or "\x00" in transform_name:
        raise ValueError(
            f"transform_name must be a non-empty, NUL-free string, got "
            f"{transform_name!r} — a fan-out over an unnamed Transform has "
            "no identity to give its instances."
        )
    _check_mapping_key(mapping_key)
    return uuid.uuid5(WORK_ITEM_NAMESPACE, f"{transform_name}\x00{mapping_key}")


# ---------------------------------------------------------------------------
# Plan side
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FanOutUnit:
    """One planned unit: a work item plus its ordinary Transform plan."""

    item: WorkItem
    instance_id: uuid.UUID
    plan: Plan
    skeleton: tuple[Annotation, ...]


@dataclass(frozen=True, slots=True)
class FanOutPlan:
    """The planned fan-out — pure data, like every plan in this federation.

    Cost arithmetic follows falaw#18's honest form exactly (same names, same
    semantics): :attr:`known_cost_usd` is the priced part, and a correct
    gate reads it **together with** :attr:`unknown_call_count` — the true
    cost is the known sum *plus an unknown amount* over that many calls,
    and the gate refuses when the count is nonzero rather than pretending
    the unknown part is free.
    """

    transform_name: str
    units: tuple[FanOutUnit, ...]

    @property
    def known_cost_usd(self) -> float:
        """Sum of every unit plan's priced, non-cache-hit calls."""
        return sum((u.plan.known_cost_usd for u in self.units), 0.0)

    @property
    def has_unknown_costs(self) -> bool:
        """True if any unit has a billable call with no price."""
        return any(u.plan.has_unknown_costs for u in self.units)

    @property
    def unknown_call_count(self) -> int:
        """How many billable calls across all units carry no price."""
        return sum(u.plan.unknown_call_count for u in self.units)


def fan_out_plan(
    transform,
    project,
    items: tuple[WorkItem, ...] | list[WorkItem],
    *,
    inputs_for: Callable[[WorkItem], "TransformInputs"],  # noqa: F821
    params: Optional[BaseModel] = None,
) -> FanOutPlan:
    """Plan one Transform across ``items`` — each unit an ordinary ``plan()`` call.

    ``inputs_for`` maps a work item to the :class:`~nw.transforms.TransformInputs`
    its unit consumes; the item's ``attributes`` carry any per-unit data it
    needs to build them. No billable calls; pure data out.

    Duplicate ``mapping_key``\\ s are refused: two units sharing a key share
    an instance id, which destroys exactly the per-instance identity the key
    exists to provide (retry, cost attribution, regenerate-just-this-one all
    become ambiguous).

    ``stamp_transform_identity`` is applied to each unit plan **here, at plan
    time** — its own docstring asks orchestrators that hash or persist plans
    before execution to do so, and a fan-out's run record is such a
    persistence. Idempotent, so :meth:`BaseTransform.execute` re-stamping at
    execute time changes nothing.
    """
    from nw.transforms import stamp_transform_identity

    # A one-shot iterable (a generator) would be consumed by the duplicate
    # scan below, leaving the planning loop zero units — a 200-item fan-out
    # reporting *complete having done nothing*. Refused-at-validation is
    # this module's whole philosophy, and materialising is the validation.
    items = tuple(items)
    name = getattr(transform, "name", "") or ""
    if not name:
        raise ValueError(
            "fan_out_plan: the transform declares no `name` — instance ids "
            "are a pure function of (transform_name, mapping_key), so a "
            "nameless Transform has no identity to give its units."
        )
    seen: dict[str, int] = {}
    for i, item in enumerate(items):
        if item.mapping_key in seen:
            raise ValueError(
                f"fan_out_plan: duplicate mapping_key {item.mapping_key!r} "
                f"(items {seen[item.mapping_key]} and {i}). Two units sharing "
                "a key share an instance id — per-instance retry, cost "
                "attribution and regeneration all become ambiguous."
            )
        seen[item.mapping_key] = i

    units = []
    for item in items:
        # Enforce the attributes contract BEFORE anything can spend: a
        # non-JSON value would otherwise surface only at to_record(),
        # after a possibly-large run, stranding that run's record.
        try:
            item.model_dump(mode="json")
        except Exception as e:
            raise ValueError(
                f"fan_out_plan: item {item.mapping_key!r} has "
                "non-JSON-serializable `attributes` — the WorkItem contract "
                "requires JSON-serializable attributes so the run record "
                "can be persisted."
            ) from e
        # Snapshot the item: `attributes` is a dict reachable through the
        # frozen model, so a caller mutating it post-plan would rewrite
        # what to_record() reports about a run that already happened.
        # (Deep copy cannot fail here: everything JSON-able is copyable.)
        item = item.model_copy(deep=True)
        try:
            plan, skeleton = transform.plan(project, inputs_for(item), params=params)
        except Exception as e:
            # Atomicity-by-raise is right at plan time (plans are cheap and
            # nothing partial escapes), but the traceback should name the
            # item it died on. add_note is 3.11+; on 3.10 the frame in the
            # traceback still points here.
            if hasattr(e, "add_note"):
                e.add_note(f"fan_out_plan: while planning item {item.mapping_key!r}")
            raise
        units.append(
            FanOutUnit(
                item=item,
                instance_id=work_item_instance_id(name, item.mapping_key),
                plan=stamp_transform_identity(plan, transform),
                skeleton=tuple(skeleton),
            )
        )
    return FanOutPlan(transform_name=name, units=tuple(units))


# ---------------------------------------------------------------------------
# Execute side
# ---------------------------------------------------------------------------

UnitStatus = Literal["succeeded", "partial", "failed", "blocked"]
"""Per-unit outcome. ``partial``: the unit's execute returned, but with some
of its own outputs failed/blocked (only reachable under ``"isolate"``).
``blocked``: never attempted, because an earlier unit failed under
``"halt"`` — falaw's three-states rationale one level up: a failed unit can
be retried verbatim; a blocked one was simply never run."""


@dataclass(frozen=True, slots=True)
class FanOutItemResult:
    """One unit's outcome, aligned 1:1 with the plan's units."""

    item: WorkItem
    instance_id: uuid.UUID
    status: UnitStatus
    result: Optional["TransformResult"] = None  # noqa: F821
    error: Optional[BaseException] = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class FanOutResult:
    """A fan-out run: one :class:`FanOutItemResult` per planned unit, in order.

    ``len(result.items) == len(fan_out.units)`` always — the same alignment
    guarantee falaw's ``ExecutionReport`` gives one level down.
    """

    transform_name: str
    items: tuple[FanOutItemResult, ...]

    @property
    def is_complete(self) -> bool:
        return all(r.status == "succeeded" for r in self.items)

    @property
    def cost_usd_actual(self) -> float:
        """Observed spend over the units that ran. A lower bound, like
        :attr:`TransformResult.cost_usd_actual` (whose caveat about billed-
        but-failed calls applies per unit)."""
        return sum(
            (r.result.cost_usd_actual for r in self.items if r.result is not None),
            0.0,
        )

    @property
    def cache_hit_savings_usd(self) -> float:
        return sum(
            (
                r.result.cache_hit_savings_usd
                for r in self.items
                if r.result is not None
            ),
            0.0,
        )

    @property
    def has_unknown_costs(self) -> bool:
        """True when the run's true spend is not fully known.

        Two sources, both counted: a surviving unit whose own report says
        so, and any **failed** unit — a unit that raised mid-execute may
        have been billed for calls its (discarded) report would have
        carried, so its spend is unknown by construction. Without the
        second clause, a failed run could read "all costs known, $0.00
        spent" — the exact under-report the federation's unknown-cost rule
        exists to prevent. ``blocked`` units never ran and are known-$0.
        """
        return any(
            (r.result.has_unknown_costs if r.result is not None else False)
            or r.status == "failed"
            for r in self.items
        )

    def to_record(self) -> dict:
        """The run record — where work items live (never the graph document).

        JSON-serializable as returned, provided every item's ``attributes``
        is (their contract; a violation raises here, naming the item).
        Annotations and artifacts are referenced by id; the annotations
        themselves were already written to the graph by each unit's ordinary
        ``execute``, and duplicating their bodies here would make the record
        a second, driftable copy.

        ``failed_count`` / ``blocked_count`` count outputs **within** a unit
        (zero when the unit itself failed — its result is ``None``); the
        unit-level outcome is ``status``. A consumer counting failed *units*
        counts statuses, not these fields.
        """
        rows = []
        for r in self.items:
            try:
                rows.append(
                    {
                        "item": r.item.model_dump(mode="json"),
                        "instance_id": str(r.instance_id),
                        "status": r.status,
                        "reason": r.reason,
                        "annotation_ids": (
                            [str(a.id) for a in r.result.annotations]
                            if r.result is not None
                            else []
                        ),
                        "artifact_ids": (
                            [a.asset_id for a in r.result.artifacts]
                            if r.result is not None
                            else []
                        ),
                        "cost_usd_actual": (
                            r.result.cost_usd_actual if r.result is not None else 0.0
                        ),
                        "failed_count": (
                            len(r.result.failed) if r.result is not None else 0
                        ),
                        "blocked_count": (
                            len(r.result.blocked) if r.result is not None else 0
                        ),
                    }
                )
            except Exception as e:
                # Almost always a non-JSON-serializable `attributes` value.
                # The error must name WHICH item broke the record — the run
                # already spent its money, and an unattributable failure
                # here strands the whole record.
                if hasattr(e, "add_note"):
                    e.add_note(
                        f"to_record: while serializing item "
                        f"{r.item.mapping_key!r} — its `attributes` must be "
                        "JSON-serializable (the WorkItem contract)"
                    )
                raise
        return {
            "transform_name": self.transform_name,
            "complete": self.is_complete,
            "cost_usd_actual": self.cost_usd_actual,
            "has_unknown_costs": self.has_unknown_costs,
            "items": rows,
        }


def _accepts_on_failure(execute: Callable) -> bool:
    """Whether ``execute`` accepts the ``on_failure`` keyword.

    The Protocol's own warning: ``runtime_checkable`` compares method names,
    not signatures, and ~18 pre-nw#25 overrides in the federation take no
    ``on_failure`` — passing it would raise ``TypeError`` at call time. The
    documented guidance for "a caller iterating over arbitrary registered
    Transforms" is to pass it only where accepted; this is that check.
    """
    try:
        sig = inspect.signature(execute)
    except (TypeError, ValueError):
        return False
    params = sig.parameters
    return "on_failure" in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


def fan_out_execute(
    transform,
    project,
    fan_out: FanOutPlan,
    *,
    use_cache: bool = True,
    force: bool = False,
    on_failure: "OnFailure" = "isolate",  # noqa: F821
) -> FanOutResult:
    """Execute a planned fan-out, one ordinary ``transform.execute`` per unit.

    ``on_failure`` governs **both levels symmetrically**:

    - within a unit, it is passed to the Transform's ``execute`` (when the
      implementation accepts it — a pre-nw#25 override runs with its own
      halt-like behaviour inside the unit; cross-unit isolation still
      applies);
    - across units, ``"isolate"`` (the default — it is the point of a
      fan-out) runs every unit and reports per-unit outcomes, while
      ``"halt"`` stops *submitting* units after the first raising unit and
      marks the rest ``blocked``.

    A unit whose ``execute`` **returns** is never a halt trigger, even when
    its result is partial — the Transform already decided those failures
    were survivable; only a raising unit halts.

    Two protocol-violation shapes degrade rather than crash, deliberately:
    an ``execute`` that rejects ``use_cache``/``force`` (or returns a
    non-``TransformResult``) shows up as per-unit ``failed`` rows carrying
    the ``TypeError``/``AttributeError`` — N identical rows for one
    programming error reads worse than one loud raise, but the alternative
    discards the run record for units that already spent. And a ``**kwargs``
    override that accepts-but-ignores ``on_failure`` runs its internal
    default within the unit — undetectable by signature inspection in
    principle; cross-unit policy is still honoured.

    Units run **sequentially**. Concurrency *within* a unit is falaw's
    (``execute_plan_isolated`` bounds it); concurrency *across* units is the
    deferred-scheduler work nw#26 explicitly scopes out, and nothing here
    forecloses it — units are planned independently and the result is
    order-aligned, not order-dependent.
    """
    if on_failure not in ("halt", "isolate"):
        raise ValueError(
            f"fan_out_execute: on_failure must be 'halt' or 'isolate', "
            f"got {on_failure!r}."
        )
    executing_name = getattr(transform, "name", "") or ""
    if executing_name != fan_out.transform_name:
        raise ValueError(
            f"fan_out_execute: this FanOutPlan was planned for "
            f"{fan_out.transform_name!r} but the transform passed is "
            f"{executing_name!r}. Executing under the wrong identity would "
            "misattribute every instance id in the run record (and skip the "
            "executing transform's own impl_version stamp)."
        )
    pass_on_failure = _accepts_on_failure(transform.execute)

    results: list[FanOutItemResult] = []
    halted_by: Optional[str] = None
    for unit in fan_out.units:
        if halted_by is not None:
            results.append(
                FanOutItemResult(
                    item=unit.item,
                    instance_id=unit.instance_id,
                    status="blocked",
                    reason=f"not attempted: unit {halted_by!r} failed under 'halt'",
                )
            )
            continue
        kwargs: dict[str, Any] = {"use_cache": use_cache, "force": force}
        if pass_on_failure:
            kwargs["on_failure"] = on_failure
        try:
            result = transform.execute(project, unit.plan, unit.skeleton, **kwargs)
            # Result interpretation stays INSIDE the try: an execute that
            # returns None (or a result whose properties raise) is a
            # protocol violation, but letting it escape mid-loop would
            # discard the whole run record — cost attribution for money
            # already spent — which is the loss isolate exists to prevent.
            status: UnitStatus = "succeeded" if result.is_complete else "partial"
            reason = (
                ""
                if result.is_complete
                else (
                    f"{len(result.failed)} failed, "
                    f"{len(result.blocked)} blocked of "
                    f"{len(result.failed) + len(result.blocked) + len(result.annotations)} outputs"
                )
            )
        except Exception as e:  # noqa: BLE001 — per-unit isolation is the feature
            results.append(
                FanOutItemResult(
                    item=unit.item,
                    instance_id=unit.instance_id,
                    status="failed",
                    error=e,
                    reason=f"{type(e).__name__}: {e}",
                )
            )
            if on_failure == "halt":
                halted_by = unit.item.mapping_key
            continue
        results.append(
            FanOutItemResult(
                item=unit.item,
                instance_id=unit.instance_id,
                status=status,
                result=result,
                reason=reason,
            )
        )
    return FanOutResult(transform_name=fan_out.transform_name, items=tuple(results))


__all__ = [
    "GenerateWhen",
    "DFLT_GENERATE_WHEN",
    "WORK_ITEM_NAMESPACE",
    "WorkItem",
    "work_item_instance_id",
    "FanOutUnit",
    "FanOutPlan",
    "fan_out_plan",
    "UnitStatus",
    "FanOutItemResult",
    "FanOutResult",
    "fan_out_execute",
]
