"""Transforms — the unified, swappable "A-annotation → B-annotation" step.

A :class:`Transform` generalizes :class:`nw.renderers.Strategy`: where a
Strategy turns a prepared *shot* into a rendered clip, a Transform turns any
input annotation(s) into any output annotation(s). Every "A → B" arrow in an
audiovisual workflow — screenplay → treatment, beat → storyboard panel, panel
→ image, clips → animatic, shot → rendered clip — is a Transform.

Two-phase, mirroring ``nw.workflow`` (prepare → plan → execute):

1. :meth:`Transform.plan` — *pure data*. Returns a :class:`falaw.Plan` plus
   *skeleton* output annotations. The skeletons already carry provenance
   (``was_derived_from`` points at the inputs), so even a dry-run plan
   inspection shows what will be produced and from what. No billable calls.
2. :meth:`Transform.execute` — runs the Plan via ``falaw.execute_plan``,
   completes the skeleton annotations with real artifact references, writes
   them to the project graph, and returns a :class:`TransformResult` with
   *actual* cost and cache savings.

Transforms are registered with an :class:`xdol.Registry` keyed by name, so
apps (``reelee``, ``muvid``, …) add their own without modifying ``nw``.

Naming convention for ``Transform.name``::

    <from_kind>_to_<to_kind>[.<flavor>[.<variant>]]
    e.g. "beat_to_panel.llm.default", "panel_to_image.fal.flux_kontext",
         "shot_to_render_result.fal.lipsync"

Most Transforms subclass :class:`BaseTransform` (which supplies a default
:meth:`~BaseTransform.execute`) and override only :meth:`~BaseTransform.plan`
plus the class-level ``name`` / ``input_kinds`` / ``output_kind`` /
``params_model`` attributes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel
from xdol import Registry

from falaw import Plan, execute_plan_isolated
from lacing import Annotation, Artifact


# ---------------------------------------------------------------------------
# Input / output bundles  (frozen dataclasses — matches nw.ShotPreparation
# and falaw.Plan; Pydantic is reserved for wire-validated annotation bodies)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TransformInputs:
    """The annotations a Transform consumes.

    ``primary`` is the subject of the operation — a single-element tuple for
    one-to-one Transforms, many for batch Transforms (e.g. ``clips_to_animatic``
    consumes every clip). ``context`` is side material keyed by kind name, so
    a Transform that declares ``input_kinds=(beat, character-ref)`` receives
    the Beat in ``primary`` and the CharacterRefs in ``context["character-ref"]``.
    """

    primary: tuple[Annotation, ...]
    context: dict[str, tuple[Annotation, ...]] = field(default_factory=dict)


DFLT_IMPL_VERSION = "1"
"""The ``impl_version`` every Transform starts at.

Doubles as the omit-if-default sentinel for the cache salt: at this value
:func:`stamp_transform_identity` stamps nothing, so every falaw cache key
and cassette ever issued stays byte-identical. The first real bump is the
first salt (nw#27)."""

OnFailure = Literal["halt", "isolate"]
"""What a Transform does when one of its calls fails.

``"halt"`` — the default and the historical behaviour: stop at the first
failure and re-raise it, writing nothing to the graph.

``"isolate"`` — run what can be run, write every success to the graph, and
report the rest. The policy to use for a **fan-out**: with 200 panels, one
rate-limited call discarding 199 paid renders is the failure mode this exists
to prevent.
"""


@dataclass(frozen=True, slots=True)
class FailedOutput:
    """An output annotation that was planned but never produced.

    Carries the *skeleton* rather than an id because the skeleton is what the
    caller planned and what a retry would re-submit — and because a UI needs its
    body to say which panel is missing, not just that something is.
    """

    skeleton: Annotation
    """The annotation that would have been completed."""

    status: str
    """``"failed"`` (its own call failed) or ``"blocked"`` (an upstream one did)."""

    reason: str = ""
    """Human-readable cause, from falaw. Renders as *"skipped: upstream panel 47
    was filtered"* rather than an unexplained hole."""

    error: Optional[BaseException] = None
    """The original exception, for a caller that classifies on falaw's typed
    hierarchy (``FalRateLimited`` is worth retrying; ``FalAccountLocked`` is not)."""

    blocked_by: tuple[int, ...] = ()
    """Indices of the calls whose failure blocked this one."""


@dataclass(frozen=True, slots=True)
class TransformResult:
    """The outputs of a Transform's :meth:`~Transform.execute`."""

    annotations: tuple[Annotation, ...]
    """The completed output annotation(s), written to the project graph."""

    artifacts: tuple[Artifact, ...] = ()
    """The ``lacing.Artifact``\\ s produced (images, videos, audio, json …).
    Annotations reference these by ``artifact_id`` in their bodies."""

    cost_usd_actual: float = 0.0
    """USD billed during execution, over the calls that **succeeded and were not
    cache hits** — falaw's observed :attr:`ExecutionReport.estimated_spend_usd`.
    Since falaw#26 the per-``Artifact`` ``cost_usd`` is *also* stamped from the
    observed outcome, so the two now agree; the report stays the source here
    because it is the run-level truth (and carries ``has_unknown_costs``),
    not because the artifacts lie anymore.

    **A lower bound, despite the name.** falaw runs its converter *inside* the
    unit of work, after the billed call, so a call fal charged for can still end
    as ``status="failed"`` — and a failed call is excluded here, because falaw
    cannot know whether the vendor billed it and inventing a number would be
    worse. Under ``"halt"`` the run aborted anyway; under ``"isolate"`` it
    continues, so a caller accumulating this across a fan-out with failures will
    under-count. Read ``failed`` alongside it."""

    cache_hit_savings_usd: float = 0.0
    """USD not spent because a call was served from cache.

    Also observed rather than predicted — this changed source at the same time
    as ``cost_usd_actual``, from ``Plan.cache_hit_savings_usd`` (what planning
    guessed would hit) to what actually hit."""

    has_unknown_costs: bool = False
    """Whether any executed call had no price. Carried so ``$0.00`` stays
    distinguishable from "we do not know", which is the distinction every cost
    gate in the federation is required to read."""

    failed: tuple[FailedOutput, ...] = ()
    """Outputs whose own call failed. Empty unless ``on_failure="isolate"``."""

    blocked: tuple[FailedOutput, ...] = ()
    """Outputs never attempted because an upstream call failed."""

    @property
    def is_complete(self) -> bool:
        """Whether every planned output was produced."""
        return not self.failed and not self.blocked


# ---------------------------------------------------------------------------
# The Transform contract
# ---------------------------------------------------------------------------


@runtime_checkable
class Transform(Protocol):
    """A swappable, costed function from A-annotations to B-annotations.

    Implementations usually subclass :class:`BaseTransform` rather than
    satisfying this Protocol directly, but the Protocol is the contract the
    registry and orchestrator depend on.
    """

    name: str
    """Globally-unique identifier in :data:`transforms`."""

    input_kinds: tuple[str, ...]
    """Body-schema URIs this Transform reads. The first is the *primary*
    kind; the rest are context kinds."""

    output_kind: str
    """The body-schema URI this Transform produces."""

    is_batch: bool
    """How :meth:`plan` consumes ``inputs.primary``.

    ``False`` (one-to-one): :meth:`plan` operates on a *single* primary
    annotation (``inputs.primary[0]``) — e.g. ``beat_to_panel``, one beat in,
    one panel out. A caller wanting to apply it across many annotations calls
    :meth:`plan` once per annotation and composes the Plans.

    ``True`` (batch): :meth:`plan` consumes *all* of ``inputs.primary`` at
    once — e.g. ``extract_characters`` (every beat → an LLM call) or
    ``clips_to_animatic`` (every clip → one animatic). A caller passes the
    whole set in a single :meth:`plan` call.

    This is the property an orchestrator needs to fan a Transform across a
    project's annotations correctly — it can't be inferred from
    ``input_kinds``."""

    impl_version: str
    """Behaviour version of this implementation (nw#27).

    "Same interface, changed behaviour" — a prompt-template edit, a
    post-processing change — bumps this **without renaming the registry
    key** (the name denotes the capability; a different capability gets a
    different name). It is a lock, not a receipt: it enters provenance
    (``transform:<name>@<impl_version>``) and, when it is not
    :data:`DFLT_IMPL_VERSION`, the falaw cache identity of every call
    executed through :meth:`BaseTransform.execute` — so a behaviour change
    cannot keep serving results minted by the old behaviour. A Transform
    that overrides ``execute`` must apply
    :func:`stamp_transform_identity` itself; the lock only locks what
    passes through it."""

    params_model: type
    """Pydantic model class for this Transform's per-call params;
    ``type(None)`` means no params. On the Protocol — not just
    :class:`BaseTransform` — so anything reading a Transform through the
    contract (the capability catalogue, an MCP tool builder, the CLI
    dispatcher) can rely on it (nw#27)."""

    def plan(
        self,
        project,  # nw.Project — annotated loosely to avoid an import cycle
        inputs: TransformInputs,
        *,
        params: Optional[BaseModel] = None,
    ) -> tuple[Plan, tuple[Annotation, ...]]:
        """Build a :class:`falaw.Plan` + skeleton output annotations.

        Pure data. No billable calls. The skeleton annotations have provenance
        filled in; their bodies' artifact references are placeholders that
        :meth:`execute` replaces.
        """
        ...

    def execute(
        self,
        project,
        plan: Plan,
        skeleton: tuple[Annotation, ...],
        *,
        use_cache: bool = True,
        force: bool = False,
        on_failure: OnFailure = "halt",
    ) -> TransformResult:
        """Run ``plan``, complete ``skeleton``, write to the graph, return result.

        ``force=True`` bypasses the cache (the "regenerate this" affordance).

        ``on_failure`` selects the failure policy — see :data:`OnFailure`.
        ``"halt"`` is the default so no existing caller changes behaviour.

        .. warning::

           ``on_failure`` is **newer than some implementations**. A Transform
           that overrides :meth:`execute` and predates nw#25 does not accept the
           keyword, and this Protocol is ``runtime_checkable``, which compares
           *method names* and not signatures — so ``isinstance`` still passes
           and the ``TypeError`` arrives at call time. reelee has ~18 such
           overrides (thorwhalen/reelee#299 tracks the migration).

           Until they are migrated, a caller iterating over arbitrary registered
           Transforms should pass ``on_failure`` only to ones it knows accept
           it, or catch ``TypeError``. Everything inheriting
           :class:`BaseTransform`'s ``execute`` — the common case — already
           does.
        """
        ...


# ---------------------------------------------------------------------------
# Default implementation most Transforms inherit from
# ---------------------------------------------------------------------------


class BaseTransform:
    """Default :class:`Transform` implementation.

    Subclasses set the class attributes (``name``, ``input_kinds``,
    ``output_kind``, optionally ``params_model``) and implement :meth:`plan`.
    The default :meth:`execute` runs the Plan, maps artifacts onto skeletons
    1:1 via :meth:`_complete_annotation`, writes to the project graph, and
    reports cost. Transforms whose artifact→annotation mapping is not 1:1
    (e.g. ``clips_to_animatic``: N inputs → 1 output) override :meth:`execute`.

    That 1:1 mapping is an **invariant, checked before anything is spent**:
    :meth:`execute` raises when ``len(skeleton) != len(plan.calls)``, the
    same guard ``nw.storyboard.execute_render_panel_images`` already applies
    to its own plan/id pairing. The zip below would otherwise stop at the
    shorter sequence and drop the surplus with no error and no record —
    harmless only for as long as the executor returns exactly one artifact
    per call, which is precisely what per-call failure isolation changes.
    """

    name: str = ""
    input_kinds: tuple[str, ...] = ()
    output_kind: str = ""
    params_model: type = type(None)
    """Pydantic model class for this Transform's per-call params. Exposed as
    JSON Schema to the MCP server and the CLI. ``type(None)`` means no params."""
    impl_version: str = DFLT_IMPL_VERSION
    """Behaviour version — bump on "same interface, changed behaviour", never
    rename the registry key for it. See the :class:`Transform` Protocol for
    the full contract. At the default, no cache salt is applied, so every
    key ever issued stays byte-identical; the first real bump is the first
    salt."""
    is_batch: bool = False
    """Whether :meth:`plan` consumes all of ``inputs.primary`` at once (batch)
    or a single primary annotation (one-to-one — the default). See the
    :class:`Transform` Protocol for the full contract. Batch Transforms
    (``extract_*``, ``clips_to_animatic``) set this to ``True``."""

    def plan(
        self,
        project,
        inputs: TransformInputs,
        *,
        params: Optional[BaseModel] = None,
    ) -> tuple[Plan, tuple[Annotation, ...]]:
        raise NotImplementedError(f"{type(self).__name__} must implement plan().")

    def execute(
        self,
        project,
        plan: Plan,
        skeleton: tuple[Annotation, ...],
        *,
        use_cache: bool = True,
        force: bool = False,
        on_failure: OnFailure = "halt",
    ) -> TransformResult:
        if len(skeleton) != len(plan.calls):
            raise ValueError(
                f"{type(self).__name__}.execute: plan has {len(plan.calls)} "
                f"calls but skeleton has {len(skeleton)} annotations. The "
                "default execute maps artifacts onto skeletons 1:1 and zips "
                "them, so a mismatch would silently drop the surplus — pass "
                "the plan and skeleton that plan() returned together, or "
                "override execute() if this Transform's mapping is not 1:1."
            )
        if on_failure not in ("halt", "isolate"):
            raise ValueError(
                f"{type(self).__name__}.execute: on_failure must be 'halt' or "
                f"'isolate', got {on_failure!r}."
            )
        plan = stamp_transform_identity(plan, self)
        # One engine, two policies. `halt` is `execute_plan` — falaw defines the
        # latter as exactly this call plus `artifacts_or_raise()` — so it keeps
        # re-raising the *original* typed exception, unwrapped, which is what
        # every existing caller classifies on.
        report = execute_plan_isolated(
            plan,
            use_cache=use_cache and not force,
            halt_on_failure=on_failure == "halt",
        )
        if on_failure == "halt":
            report.artifacts_or_raise()

        # Zip against `report.outcomes`, never against the artifacts: outcomes
        # is full-length in plan order **by construction**, while the artifact
        # list is short exactly when something failed — so zipping that would
        # pair panel 48's artifact onto panel 47's skeleton the moment one call
        # dropped out, silently, which is the defect this issue named.
        completed: list[Annotation] = []
        failed: list[FailedOutput] = []
        blocked: list[FailedOutput] = []
        for skel, outcome in zip(skeleton, report.outcomes, strict=True):
            if outcome.ok:
                try:
                    completed.append(self._complete_annotation(skel, outcome.artifact))
                except Exception as e:  # noqa: BLE001 — see below
                    # A call that **succeeded and was billed** can still fail to
                    # become an annotation: falaw degrades an unreadable asset to
                    # a `json` artifact with `path=None`, a `text` artifact has no
                    # obvious target field, and an LLM that prefaces its JSON with
                    # prose makes `json.loads` raise. Letting that propagate is
                    # this issue's own defect one layer up — it would discard
                    # every paid sibling in the run. So a completion failure is
                    # an *unproduced output*, exactly like an execution failure.
                    if on_failure == "halt":
                        raise
                    failed.append(
                        FailedOutput(
                            skeleton=skel,
                            status="failed",
                            reason=(
                                f"the call succeeded but its result could not be "
                                f"turned into a {self.output_kind or 'output'} "
                                f"annotation: {type(e).__name__}: {e}"
                            ),
                            error=e,
                        )
                    )
                continue
            unproduced = FailedOutput(
                skeleton=skel,
                status=outcome.status,
                reason=outcome.reason,
                error=outcome.error,
                blocked_by=tuple(outcome.blocked_by),
            )
            (blocked if outcome.status == "blocked" else failed).append(unproduced)

        # Successes reach the graph before the failure is reported. They are
        # paid for; discarding them because a sibling call failed is the waste
        # falaw#20 removed one layer down, and it would be reintroduced here by
        # returning early.
        for ann in completed:
            project.graph.add_annotation(ann)
        return TransformResult(
            annotations=tuple(completed),
            artifacts=tuple(report.produced),
            cost_usd_actual=report.estimated_spend_usd,
            cache_hit_savings_usd=report.cache_hit_savings_usd,
            has_unknown_costs=report.has_unknown_costs,
            failed=tuple(failed),
            blocked=tuple(blocked),
        )

    def _complete_annotation(
        self, skeleton: Annotation, artifact: Artifact
    ) -> Annotation:
        """Map one executed ``artifact`` onto its ``skeleton`` annotation.

        Two default behaviours, by artifact kind:

        - **media** (``image`` / ``video`` / ``audio`` / ``binary``) — reference
          the artifact by content id: ``body["artifact_id"] = artifact.asset_id``.
        - **``json``** — the LLM-backed-Transform case: read the materialized
          JSON file (``artifact.path``, written by ``falaw``'s converter),
          shallow-merge its keys into the skeleton body. The skeleton's
          placeholder fields get overwritten by the LLM's values.

        ``text`` artifacts still raise — a bare string has no obvious target
        field, so a Transform producing ``text`` must override this (or
        ``execute``) to say where the text goes.
        """
        if artifact.kind == "json":
            if not artifact.path:
                raise ValueError(
                    f"{type(self).__name__}: json artifact has no `path` to read; "
                    "expected falaw's converter to have materialized it."
                )
            payload = json.loads(Path(artifact.path).read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(
                    f"{type(self).__name__}: json artifact is not an object "
                    f"({type(payload).__name__}); override _complete_annotation()."
                )
            new_body = {**skeleton.body, **payload}
            return skeleton.model_copy(update={"body": new_body})
        if artifact.kind == "text":
            raise NotImplementedError(
                f"{type(self).__name__} produces 'text' artifacts; override "
                "_complete_annotation() (or execute()) to say where the text goes."
            )
        new_body = {**skeleton.body, "artifact_id": artifact.asset_id}
        return skeleton.model_copy(update={"body": new_body})


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

transforms: Registry = Registry(name="nw.transforms", on_conflict="error")
"""Public registry of Transform instances. ``on_conflict="error"`` so a
misconfigured plugin fails loudly instead of silently shadowing a built-in."""


def stamp_transform_identity(plan: Plan, transform: Transform) -> Plan:
    """Fold ``transform.impl_version`` into every call's cache identity.

    The reader that makes ``impl_version`` a lock instead of a receipt
    (nw#27): a bumped version lands in each call's falaw ``key_extra``, so
    a cached result minted by the old behaviour cannot be reused. At
    :data:`DFLT_IMPL_VERSION` nothing is stamped — every key ever issued
    stays byte-identical, and the first real bump is the first salt.

    Each stamped call's ``cache_status`` is reset to ``"unknown"``: the
    plan-time peek keyed without the salt, so its prediction (typically
    "hit" — the old-behaviour result is cached, invalidating it is the
    point) would make cost gates quote $0.00 for a full re-bill.

    :meth:`BaseTransform.execute` applies this automatically. **A Transform
    that overrides** :meth:`~BaseTransform.execute` **must apply it
    itself** — the lock only locks calls that pass through it (a
    registry-wide conformance test is the honest guard). An orchestrator
    that hashes or caches plans *before* execution (e.g. a job idempotency
    key over ``falaw.plan_hash``) should apply it at plan time so those
    keys see the version too. Idempotent — stamping twice writes the same
    value.
    """
    version = str(getattr(transform, "impl_version", DFLT_IMPL_VERSION))
    if version == DFLT_IMPL_VERSION:
        return plan
    return Plan(
        calls=tuple(
            replace(
                call,
                key_extra={**call.key_extra, "transform_impl": version},
                # The plan-time cache peek keyed WITHOUT the salt, so its
                # prediction is void: a "hit" here would make the gates
                # quote $0.00 for what the salted execution re-bills in
                # full. "unknown" makes the quote the full price - the
                # honest, conservative number for a deliberate
                # invalidation.
                cache_status="unknown",
            )
            for call in plan.calls
        )
    )


def cache_key(transform: Transform, *parts) -> str:
    """A stable identity for a non-fal Transform's output (nw#54).

    falaw's content-addressed cache covers fal calls; a Transform that spends
    money or CPU WITHOUT going through fal (ElevenLabs TTS, an ffmpeg
    extraction) has to carry its own compare-and-skip identity. Before this
    existed, each such Transform hand-rolled one — braidio's two paid
    Transforms imported the digest from a *private* module of another
    package, where a reshaping would have silently changed cache keys on
    paid calls.

    The digest: SHA-256 over ``parts``, ``\\0``-delimited (``None`` → empty,
    ``str`` → utf-8, ``bytes`` verbatim). Give ``parts`` a distinguishing
    leading tag (``"narration"``, ``"segment"``) so two Transforms hashing
    similar inputs cannot collide.

    ``impl_version`` reaches the key with the SAME omit-if-default rule as
    :func:`stamp_transform_identity`, and for the same two reasons. A key
    that ignored ``impl_version`` would reintroduce the exact bug the field
    exists to prevent — "same interface, changed behaviour" serving a stale
    artifact forever (invariant 3). And at :data:`DFLT_IMPL_VERSION` nothing
    extra is folded, so every key ever issued by the hand-rolled
    predecessors stays byte-identical: adopting this helper re-bills
    nothing, and the first real bump is the first salt (which folds
    ``transform.name`` too, scoping the invalidation to the bumped
    Transform).

    >>> class _T:  # a stand-in transform
    ...     name = "narration_render.tts"
    ...     impl_version = "1"
    >>> k1 = cache_key(_T(), "narration", "hello", None)
    >>> k1 == cache_key(_T(), "narration", "hello", None)   # stable
    True
    >>> _T.impl_version = "2"
    >>> cache_key(_T(), "narration", "hello", None) == k1   # the bump salts
    False
    """
    import hashlib

    h = hashlib.sha256()

    def _feed(part) -> None:
        if part is None:
            part = b""
        elif isinstance(part, str):
            part = part.encode()
        elif not isinstance(part, bytes):
            part = str(part).encode()
        h.update(part)
        h.update(b"\0")

    for part in parts:
        _feed(part)
    version = str(getattr(transform, "impl_version", DFLT_IMPL_VERSION))
    if version != DFLT_IMPL_VERSION:
        _feed(f"transform:{getattr(transform, 'name', '')}@{version}")
    return h.hexdigest()


def cached_output(project_root, tier: str, key: str):
    """An existing completed ``tier`` annotation with this ``cache_key``, or None.

    The lookup half of :func:`cache_key`: a Transform checks here before
    doing billable/expensive work, and skips when a completed node (one with
    a non-null ``artifact_id``) already carries the key in its body.

    A linear scan of the tier — honest about scale: fine at project size
    (tens to hundreds of nodes), and the day a project outgrows it the fix
    is an index behind this same signature, not a second lookup convention
    per genre.
    """
    from nw.graph import annotations_at_tier

    for ann in annotations_at_tier(project_root, tier):
        body = ann.body or {}
        if body.get("cache_key") == key and body.get("artifact_id"):
            return ann
    return None


def register_transform(
    name: str, impl: Optional[Transform] = None
) -> Transform | Callable[[type], type]:
    """Register a Transform under ``name``. Two forms:

    Direct — pass an instance::

        register_transform("clips_to_animatic.ffmpeg", ClipsToAnimatic())

    Decorator — decorate a class; it is instantiated and the *instance* is
    registered (so :func:`get_transform` always returns something callable),
    and the class is returned unchanged::

        @register_transform("beat_to_panel.llm.default")
        class BeatToPanelLLM(BaseTransform):
            ...

    Registration validates the contract the registry's consumers depend on:
    an empty ``output_kind`` is refused loudly, in the same spirit as the
    registry's ``on_conflict="error"`` — an agent's unit of work must have a
    declared output type, or "the job runs successfully but produces
    nothing retrievable" becomes invisible to every layer that reports
    success (nw#27).
    """

    def _checked(instance: Transform) -> Transform:
        version = getattr(instance, "impl_version", DFLT_IMPL_VERSION)
        if not isinstance(version, str):
            raise ValueError(
                f"register_transform({name!r}): {type(instance).__name__}."
                f"impl_version must be a str, got {version!r} "
                f"({type(version).__name__}). A non-str version defeats the "
                "omit-if-default cache sentinel while rendering identically "
                "in provenance."
            )
        if not getattr(instance, "output_kind", ""):
            raise ValueError(
                f"register_transform({name!r}): {type(instance).__name__} "
                "declares no output_kind. Every Transform must declare the "
                "body-schema URI it produces — an undeclared output type is "
                "a unit of work whose success is unverifiable."
            )
        return instance

    if impl is None:

        def _decorator(cls: type) -> type:
            transforms.register(name, _checked(cls()))
            return cls

        return _decorator
    return transforms.register(name, _checked(impl))


def get_transform(name: str) -> Transform:
    """Look up a Transform instance by name; raises with the known names."""
    if name not in transforms:
        known = sorted(transforms.keys())
        raise KeyError(
            f"No Transform {name!r}; registered: {known}. Apps register "
            "custom Transforms via `nw.register_transform(name, impl)` or the "
            "`@nw.register_transform('name')` class decorator."
        )
    return transforms[name]


def list_transforms() -> list[str]:
    """Return all registered Transform names (sorted)."""
    return sorted(transforms.keys())


def transform_catalog() -> list[dict]:
    """Every registered Transform as a JSON-able capability entry (sorted by name).

    The typed-capability surface an HTTP route / MCP tool builder / agent
    serves or selects from, mirroring :func:`nw.genre_catalog` (nw#28): a
    consumer needs no registry-internal knowledge to render or compose.
    Entry shape::

        {name, input_kinds, output_kind, is_batch, impl_version, params_schema}

    ``name`` is the registry key (the addressable name). ``params_schema``
    is the params model's JSON Schema — ``{}`` for a Transform with no
    params — and is what an MCP tool definition is built from. The whole
    list is JSON-serializable as returned.
    """
    entries = []
    for name in list_transforms():
        transform = transforms[name]
        params_model = getattr(transform, "params_model", type(None))
        entries.append(
            {
                "name": name,
                "input_kinds": list(transform.input_kinds),
                "output_kind": transform.output_kind,
                "is_batch": transform.is_batch,
                "impl_version": getattr(transform, "impl_version", DFLT_IMPL_VERSION),
                "params_schema": (
                    {}
                    if params_model is type(None)
                    else params_model.model_json_schema()
                ),
            }
        )
    return entries


# --- import adapters so the built-in render-strategy Transforms self-register
from . import _adapters as _adapters  # noqa: E402,F401


__all__ = [
    "Transform",
    "TransformInputs",
    "TransformResult",
    "BaseTransform",
    "DFLT_IMPL_VERSION",
    "transforms",
    "register_transform",
    "get_transform",
    "list_transforms",
    "transform_catalog",
    "stamp_transform_identity",
    "cache_key",
    "cached_output",
]
