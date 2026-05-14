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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol, runtime_checkable

from pydantic import BaseModel
from xdol import Registry

from falaw import Plan, execute_plan
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


@dataclass(frozen=True, slots=True)
class TransformResult:
    """The outputs of a Transform's :meth:`~Transform.execute`."""

    annotations: tuple[Annotation, ...]
    """The completed output annotation(s), written to the project graph."""

    artifacts: tuple[Artifact, ...] = ()
    """The ``lacing.Artifact``\\ s produced (images, videos, audio, json …).
    Annotations reference these by ``artifact_id`` in their bodies."""

    cost_usd_actual: float = 0.0
    """Actual USD billed during execution (cache hits cost nothing)."""

    cache_hit_savings_usd: float = 0.0
    """USD that would have been spent had nothing been cached."""


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
    ) -> TransformResult:
        """Run ``plan``, complete ``skeleton``, write to the graph, return result.

        ``force=True`` bypasses the cache (the "regenerate this" affordance).
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
    """

    name: str = ""
    input_kinds: tuple[str, ...] = ()
    output_kind: str = ""
    params_model: type = type(None)
    """Pydantic model class for this Transform's per-call params. Exposed as
    JSON Schema to the MCP server and the CLI. ``type(None)`` means no params."""

    def plan(
        self,
        project,
        inputs: TransformInputs,
        *,
        params: Optional[BaseModel] = None,
    ) -> tuple[Plan, tuple[Annotation, ...]]:
        raise NotImplementedError(
            f"{type(self).__name__} must implement plan()."
        )

    def execute(
        self,
        project,
        plan: Plan,
        skeleton: tuple[Annotation, ...],
        *,
        use_cache: bool = True,
        force: bool = False,
    ) -> TransformResult:
        artifacts = execute_plan(plan, use_cache=use_cache and not force)
        completed = tuple(
            self._complete_annotation(skel, art)
            for skel, art in zip(skeleton, artifacts)
        )
        for ann in completed:
            project.graph.add_annotation(ann)
        return TransformResult(
            annotations=completed,
            artifacts=tuple(artifacts),
            cost_usd_actual=sum((a.cost_usd or 0.0) for a in artifacts),
            cache_hit_savings_usd=plan.cache_hit_savings_usd,
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
    """
    if impl is None:
        def _decorator(cls: type) -> type:
            transforms.register(name, cls())
            return cls
        return _decorator
    return transforms.register(name, impl)


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


# --- import adapters so the built-in render-strategy Transforms self-register
from . import _adapters as _adapters  # noqa: E402,F401


__all__ = [
    "Transform",
    "TransformInputs",
    "TransformResult",
    "BaseTransform",
    "transforms",
    "register_transform",
    "get_transform",
    "list_transforms",
]
