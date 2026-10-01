"""nw — Narrative Workflow.

Application-orchestration framework for audiovisual projects. A project is
a folder; a **genre** (music video, explainer, podcast clip, slideshow) is a
reusable specialization on top — the first-class successor to what nw
informally called an "app" (see :mod:`nw.genres` and issue #10).

Public surface:

- :class:`Project` — folder facade: read/write spec, character anchors,
  shot upserts, decision log, typed summary, session-resumption brief.
- :class:`ProjectSummary` — typed read view of a project.
- :class:`ResumptionBrief` — "where we left off": decision tail, what the
  last *authored* change reaches downstream, recorded spend, deterministic
  next actions. Its ``caveats`` field carries what those numbers do *not*
  know.
- :func:`clone_project` — replaces ``cp -r`` for sibling experiments.
- :func:`apply_to_projects` — replaces shell for-loops across roots.
- Schema types: :class:`ProjectSpec`, :class:`SectionSpec`, :class:`ShotSpec`,
  :class:`CharacterRef`, :class:`EnvironmentRef`, :class:`SongInfo`.
- ``nw.workflow`` — the ``prepare`` → ``plan`` → ``execute`` render split
  (Plan/Execute over rendering; records render-result provenance).
- ``nw.renderers`` — render strategies.
- ``nw.genres`` — production genres (the reusable project specialization),
  their project factories, and the ops a host serves on a genre's projects
  (:class:`GenreOp`, :func:`register_genre_ops`, :func:`genre_ops_catalogue`).
- ``nw.pricing`` — re-quoting a *persisted* plan at today's rates
  (:func:`current_quote`, :class:`PlanQuote`). Any stored cost figure is an
  as-of-then fact; reporting one as current under-quotes the run once falaw's
  rate tables move, so read it back through here (nw#74).
- ``nw.jobs`` — durable async render jobs over ``au``. Loaded on first touch.
- ``nw.storyboard`` — the storyboard bridge to ``artful`` (``open_storyboard``,
  ``save_storyboard``, ``storyboard_from_shots``, …). Needs the
  ``nw[storyboard]`` extra; loaded on first touch.

nw is a substrate, not an aggregator (nw#96): ``import nw`` loads the contract
— lacing's graph, falaw's plans, and the surfaces written in them — and a
feature with a dependency of its own loads when it is first used. The names
are still ``nw.jobs``, ``nw.open_storyboard`` and ``from nw import …``; only
the moment of import moved. See
``misc/docs/What nw is — a substrate, not an aggregator.md``.

On rendering provenance and partial re-render (why choices, not just content,
are recorded as linked artifacts), see
``misc/docs/Rendering Provenance and Partial Re-render.md``.
"""

from . import bodies  # noqa: F401  — registers lacing body schemas at import
from . import graph  # noqa: F401  — `nw.graph.descendants_of(...)`
from . import freshness  # noqa: F401  — `nw.freshness.stale_verdicts(...)`
from . import inspect  # noqa: F401  — `nw.inspect.shot_report(...)`
from . import migrate  # noqa: F401  — `nw.migrate.migrate_to_graph(...)`
from . import pricing  # noqa: F401  — `nw.pricing.current_quote(...)`
from .experiment import apply_to_projects, clone_project, summarize_all
from .inspect import (
    ComposeReport,
    FrozenSegment,
    Gap,
    ShotReport,
    compose_report,
    shot_report,
)
from .validation import (
    Check,
    CheckResult,
    Finding,
    ValidationError,
    ValidationReport,
    checks,
    menu,
    plan_checks,
    register_check,
    suggest,
    validate,
)
from .checks import register_builtin_checks as _register_builtin_checks
from .pricing import (
    PlanQuote,
    QuoteStatus,
    cost_records,
    current_quote,
    plan_from_cost_records,
    quote_from_cost_records,
    quote_render_decision,
    unquotable,
)
from .project import CharacterImage, Project
from .graph import (
    ProjectGraph,
    StoredUnproducedOutput,
    annotations_at_tier,
    backfill_traces,
    collect_orphan_traces,
    derived_from,
    descendants_of,
    iter_all_annotations,
    open_project_stores,
)
from .bodies import UNPRODUCED_OUTPUT_BODY_SCHEMA_URI, UnproducedOutputBodyV1
from .freshness import (
    FreshnessVerdict,
    all_stale,
    stale_after,
    stale_verdicts,
    stale_verdicts_all,
)
from .migrate import migrate_to_graph, is_migrated
from .script_segmentation import (
    PanelProposal,
    build_prompt,
    segment_script_into_panels,
)
from .renderers import (
    Strategy,
    get_strategy,
    list_strategies,
    register_strategy,
    strategies,
)
from .transforms import (
    DFLT_IMPL_VERSION,
    BaseTransform,
    CacheModeConflict,
    FailedOutput,
    OnFailure,
    Transform,
    TransformInputs,
    TransformResult,
    get_transform,
    list_transforms,
    register_transform,
    stamp_transform_identity,
    transform_catalog,
    transforms,
    # fan-out (nw#26)
    DFLT_GENERATE_WHEN,
    FanOutItemResult,
    FanOutPlan,
    FanOutResult,
    FanOutUnit,
    GenerateWhen,
    UnitStatus,
    WorkItem,
    fan_out_execute,
    # execution secrets — the per-caller credential seam
    FAL_SECRET,
    Secrets,
    as_secrets,
    redact,
    redact_exception,
    using_secrets,
    fan_out_plan,
    work_item_instance_id,
)
from . import delivery  # noqa: F401  — `nw.delivery.Deliverable` (the genre->host seam)
from .delivery import Deliverable, Lister, Resolver, format_ref, parse_ref
from .genres import (
    GENRE_STATUSES,
    Genre,
    Template,
    GenreResolver,
    genres,
    get_genre,
    list_genres,
    register_genre,
    genre_catalog,
    describe_genre,
    recommend_genre,
    resolve_defaults,
    genre_resolvers,
    register_genre_resolver,
    resolve_genre,
    GenreInitializer,
    genre_initializers,
    register_genre_initializer,
    initialize_genre,
    GenreProjectFactory,
    genre_project_factories,
    register_genre_project_factory,
    has_genre_project_factory,
    can_place_genre_project,
    create_genre_project,
    PLACEMENT_ARG,
    GENRE_OP_EFFECTS,
    GENRE_OP_RUNS,
    GenreOp,
    UnknownGenreOpError,
    GenreOpRefused,
    GenreOpCancelled,
    CANCEL_PARAM,
    genre_ops_registry,
    register_genre_ops,
    genre_ops,
    genre_op,
    genre_ops_catalogue,
)
from .schema import (
    SCHEMA_VERSION,
    CharacterRef,
    DecisionEntry,
    EnvironmentRef,
    ProjectSpec,
    ProjectSummary,
    ResumptionBrief,
    SectionSpec,
    ShotSpec,
    SongInfo,
)
from .workflow import (
    ShotPreparation,
    execute_render,
    plan_render_shot,
    prepare_shot,
)

__all__ = [
    "Deliverable",
    "Resolver",
    "Lister",
    "parse_ref",
    "format_ref",
    "SCHEMA_VERSION",
    "BaseTransform",
    "CacheModeConflict",
    "CharacterImage",
    "CharacterRef",
    "ComposeReport",
    "FreshnessVerdict",
    "DecisionEntry",
    "EnvironmentRef",
    "FrozenSegment",
    "Gap",
    "GENRE_STATUSES",
    "Genre",
    "Template",
    "GenreResolver",
    "genre_catalog",
    "describe_genre",
    "recommend_genre",
    "resolve_defaults",
    "genre_resolvers",
    "register_genre_resolver",
    "resolve_genre",
    "GenreInitializer",
    "genre_initializers",
    "register_genre_initializer",
    "initialize_genre",
    "GenreProjectFactory",
    "genre_project_factories",
    "register_genre_project_factory",
    "has_genre_project_factory",
    "can_place_genre_project",
    "create_genre_project",
    "PLACEMENT_ARG",
    "GENRE_OP_EFFECTS",
    "GENRE_OP_RUNS",
    "GenreOp",
    "UnknownGenreOpError",
    "GenreOpRefused",
    "GenreOpCancelled",
    "CANCEL_PARAM",
    "genre_ops_registry",
    "register_genre_ops",
    "genre_ops",
    "genre_op",
    "genre_ops_catalogue",
    "Project",
    "ProjectGraph",
    "StoredUnproducedOutput",
    "UNPRODUCED_OUTPUT_BODY_SCHEMA_URI",
    "UnproducedOutputBodyV1",
    "ProjectSpec",
    "ProjectSummary",
    "ResumptionBrief",
    "SectionSpec",
    "ShotPreparation",
    "ShotReport",
    "ShotSpec",
    "SongInfo",
    "Strategy",
    "Transform",
    "TransformInputs",
    "TransformResult",
    "FailedOutput",
    "OnFailure",
    "annotations_at_tier",
    "apply_to_projects",
    "clone_project",
    "backfill_traces",
    "collect_orphan_traces",
    "compose_report",
    "derived_from",
    "descendants_of",
    "freshness",
    "execute_render",
    "genres",
    "get_genre",
    "get_strategy",
    "get_transform",
    "inspect",
    "is_migrated",
    "iter_all_annotations",
    "list_genres",
    "list_strategies",
    "list_transforms",
    "migrate_to_graph",
    "open_project_stores",
    "plan_render_shot",
    # re-quoting persisted plan costs (nw#74)
    "PlanQuote",
    "QuoteStatus",
    "cost_records",
    "current_quote",
    "plan_from_cost_records",
    "quote_from_cost_records",
    "quote_render_decision",
    "unquotable",
    "prepare_shot",
    "register_genre",
    "register_strategy",
    "register_transform",
    "transform_catalog",
    "stamp_transform_identity",
    "DFLT_IMPL_VERSION",
    # fan-out (nw#26)
    "GenerateWhen",
    "DFLT_GENERATE_WHEN",
    "WorkItem",
    "work_item_instance_id",
    "FanOutUnit",
    "FanOutPlan",
    "fan_out_plan",
    "UnitStatus",
    "FanOutItemResult",
    "FanOutResult",
    "fan_out_execute",
    # execution secrets
    "FAL_SECRET",
    "Secrets",
    "as_secrets",
    "redact",
    "redact_exception",
    "using_secrets",
    "shot_report",
    # --- validation: the seam, its menu, and nw's own checks ---------------
    "Check",
    "CheckResult",
    "Finding",
    "ValidationError",
    "ValidationReport",
    "checks",
    "menu",
    "plan_checks",
    "register_check",
    "suggest",
    "validate",
    "all_stale",
    "stale_after",
    "stale_verdicts",
    "stale_verdicts_all",
    "strategies",
    "summarize_all",
    "transforms",
]

# nw's own checks go on the menu at import — a menu is only useful if it has
# something on it. Nothing runs them; see `nw.validation` on why validation is
# placed by a caller and never assumed.
_register_builtin_checks()


# --- feature modules: loaded on first touch (nw#96) ---------------------------
#
# A feature whose dependency the substrate's contract does not need loads when a
# caller first reaches for it, so `import nw` costs the contract and no more.
# `au` alone made `nw.jobs` the largest share of `import nw`: its package import
# pulls its HTTP surface (fastapi, flask) whenever those are installed. Every
# spelling a caller already uses keeps working; `dir(nw)` lists the names
# without loading them. The lazy names stay out of `__all__`, so `from nw import
# *` never reaches for an extra that is not installed.
#
# A feature whose extra is NOT installed answers an attribute lookup with an
# AttributeError that names the extra (chained to the MissingExtra), never with
# the ImportError itself: `hasattr`, `getattr(nw, name, default)`, `help(nw)` and
# `inspect.getmembers(nw)` all rely on that protocol. `import nw.storyboard`
# still raises the MissingExtra. Values are full module paths, so a module that
# moves out of nw can leave a forwarding entry here and its callers unchanged.

_LAZY_SUBMODULES = {
    "jobs": "nw.jobs",
    "storyboard": "nw.storyboard",
}
_LAZY_ATTRS = {
    name: "nw.storyboard"
    for name in (
        "execute_render_panel_images",
        "open_storyboard",
        "plan_render_panel_images",
        "project_asset_id",
        "save_storyboard",
        "storyboard_db_path",
        "storyboard_from_shots",
    )
}


def __getattr__(name: str):
    import importlib

    from ._extras import MissingExtra

    module_path = _LAZY_SUBMODULES.get(name) or _LAZY_ATTRS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        module = importlib.import_module(module_path)
    except MissingExtra as e:
        raise AttributeError(f"nw.{name} is unavailable: {e}", name=name) from e
    value = module if name in _LAZY_SUBMODULES else getattr(module, name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY_SUBMODULES) | set(_LAZY_ATTRS))
