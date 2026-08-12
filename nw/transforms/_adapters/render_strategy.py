"""Adapter: wrap a legacy ``nw.renderers.Strategy`` as a ``Transform``.

The 5 built-in render strategies (``lipsync``, ``image_to_video``,
``text_to_video``, ``still``, ``composite_lipsync``) predate the Transform
abstraction. They operate on a :class:`nw.workflow.ShotPreparation` and have
a ``plan`` + ``materialize`` shape. A Transform operates on annotations and
has a ``plan`` + ``execute`` shape.

This adapter bridges the two without touching the strategies:

- ``plan`` resolves the shot annotation to a ``ShotPreparation`` (via
  ``nw.workflow.prepare_shot``), delegates to ``strategy.plan``, and builds a
  skeleton ``render-result`` annotation whose provenance points at the shot.
- ``execute`` runs the Plan, re-derives the *local-only* ``ShotPreparation``
  (``upload=False`` — ``materialize`` only needs local paths), delegates to
  ``strategy.materialize`` for the final mp4, and completes the annotation.

Registering these proves the Transform contract is expressive enough for the
existing rendering code. ``nw.workflow.execute_render`` is untouched and keeps
working; new code can drive renders through the Transform registry instead.

Known wart surfaced by this adapter: a render strategy's ``materialize`` step
needs local paths that aren't in the ``falaw.Plan``, so ``execute`` re-runs
``prepare_shot``. ``falaw``-only Transforms don't have this issue (the Plan's
``<from N>`` placeholders are self-contained). Left as-is for now — it's the
honest shape of wrapping pre-Transform code.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from falaw import Plan, execute_plan_isolated
from lacing import Annotation

from .. import (
    BaseTransform,
    FailedOutput,
    OnFailure,
    TransformInputs,
    TransformResult,
    register_transform,
)
from .._provenance import derive_provenance
from ...bodies import (
    RENDER_RESULT_BODY_SCHEMA_URI,
    RENDER_RESULT_TIER,
    RenderResultBodyV1,
    SHOT_BODY_SCHEMA_URI,
)


class RenderStrategyParams(BaseModel):
    """Per-call parameters for a render-strategy Transform."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    quality: str = Field(
        "balanced",
        description='Quality tier passed to the strategy: "draft" | "balanced" | "high".',
    )
    upload: bool = Field(
        True,
        description=(
            "Upload local files to fal-storage during plan so the Plan has "
            "stable URLs. False for offline plan inspection (the resulting "
            "Plan can't be executed)."
        ),
    )
    model_overrides: dict[str, str] = Field(
        default_factory=dict,
        description="Per-step model id overrides, forwarded to the strategy.",
    )


class RenderStrategyTransform(BaseTransform):
    """Wraps one ``nw.renderers.Strategy`` instance as a Transform.

    ``input_kinds`` is just ``(shot,)`` — the character / environment context
    a strategy needs is resolved from the *project* by ``prepare_shot``, not
    passed through ``TransformInputs``.
    """

    input_kinds = (SHOT_BODY_SCHEMA_URI,)
    output_kind = RENDER_RESULT_BODY_SCHEMA_URI
    params_model = RenderStrategyParams

    def __init__(self, strategy) -> None:
        self._strategy = strategy
        self.name = f"shot_to_render_result.fal.{strategy.name}"

    def plan(
        self,
        project,
        inputs: TransformInputs,
        *,
        params: RenderStrategyParams | None = None,
    ) -> tuple[Plan, tuple[Annotation, ...]]:
        from ...workflow import prepare_shot

        params = params or RenderStrategyParams()
        shot_ann = inputs.primary[0]
        shot_id = shot_ann.body["shot_id"]

        prep = prepare_shot(project, shot_id, upload=params.upload)
        plan = self._strategy.plan(
            prep,
            quality=params.quality,
            model_overrides=params.model_overrides or {},
        )

        skeleton = Annotation(
            id=uuid.uuid4(),
            tier=RENDER_RESULT_TIER,
            reference=shot_ann.reference,  # inherit the shot's interval + asset
            body=RenderResultBodyV1(
                shot_id=shot_id,
                strategy=self._strategy.name,
                output_path="",
                artifact_id=None,
                duration_s=prep.duration_s,
                total_estimated_cost_usd=plan.total_cost_usd,
            ).model_dump(),
            body_schema_uri=RENDER_RESULT_BODY_SCHEMA_URI,
            provenance=derive_provenance(
                self.name,
                "nw.renderers",
                inputs,
                attributed_to=f"agent:nw.renderers.{self._strategy.name}",
            ),
        )
        return plan, (skeleton,)

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
        """Render one shot. ``on_failure`` isolates at the *shot* boundary.

        This Transform composes N calls into **one** output, so there is no
        partial output to hand back: a strategy cannot materialize a shot from
        some of its clips. ``"isolate"`` therefore means "report this shot as
        failed instead of raising", which is what lets a caller rendering 40
        shots keep the other 39 — the same guarantee as the 1:1 case, at the
        granularity this Transform actually has.
        """
        from ...workflow import prepare_shot

        shot_id = skeleton[0].body["shot_id"]
        # materialize() needs only local paths — no upload required.
        prep = prepare_shot(project, shot_id, upload=False)
        report = execute_plan_isolated(
            plan, use_cache=use_cache and not force, halt_on_failure=True
        )
        if not report.is_complete:
            if on_failure == "halt":
                report.artifacts_or_raise()
            first = next(iter(report.failed + report.blocked))
            return TransformResult(
                annotations=(),
                artifacts=tuple(report.produced),
                cost_usd_actual=report.estimated_spend_usd,
                cache_hit_savings_usd=report.cache_hit_savings_usd,
                has_unknown_costs=report.has_unknown_costs,
                failed=(
                    FailedOutput(
                        skeleton=skeleton[0],
                        status=first.status,
                        reason=first.reason
                        or f"{self._strategy.name}: a call in the shot's plan failed",
                        error=first.error,
                        blocked_by=tuple(first.blocked_by),
                    ),
                ),
            )
        artifacts = list(report.produced)
        output = self._strategy.materialize(prep, plan, list(artifacts))

        video_artifact = next((a for a in artifacts if a.kind == "video"), None)
        rel_output = (
            str(output.relative_to(project.root))
            if output.is_relative_to(project.root)
            else str(output)
        )
        completed = skeleton[0].model_copy(
            update={
                "body": {
                    **skeleton[0].body,
                    "output_path": rel_output,
                    "artifact_id": video_artifact.asset_id if video_artifact else None,
                }
            }
        )
        project.graph.add_annotation(completed)
        return TransformResult(
            annotations=(completed,),
            artifacts=tuple(artifacts),
            cost_usd_actual=report.estimated_spend_usd,
            cache_hit_savings_usd=report.cache_hit_savings_usd,
            has_unknown_costs=report.has_unknown_costs,
        )


# --- wrap every registered render strategy at import time ------------------

from nw.renderers import strategies as _strategies  # noqa: E402

for _name, _strategy in list(_strategies.items()):
    register_transform(
        f"shot_to_render_result.fal.{_name}",
        RenderStrategyTransform(_strategy),
    )
