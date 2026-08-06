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
- ``nw.genres`` — production genres (the reusable project specialization).

On rendering provenance and partial re-render (why choices, not just content,
are recorded as linked artifacts), see
``misc/docs/Rendering Provenance and Partial Re-render.md``.
"""

from . import bodies  # noqa: F401  — registers lacing body schemas at import
from . import graph  # noqa: F401  — `nw.graph.descendants_of(...)`
from . import freshness  # noqa: F401  — `nw.freshness.stale_verdicts(...)`
from . import inspect  # noqa: F401  — `nw.inspect.shot_report(...)`
from . import migrate  # noqa: F401  — `nw.migrate.migrate_to_graph(...)`
from . import storyboard as _storyboard_module  # noqa: F401
from . import jobs  # noqa: F401  — `nw.jobs.enqueue(...)` async render-job facade over au
from .experiment import apply_to_projects, clone_project, summarize_all
from .inspect import (
    ComposeReport,
    FrozenSegment,
    Gap,
    ShotReport,
    compose_report,
    shot_report,
)
from .project import CharacterImage, Project
from .graph import (
    ProjectGraph,
    annotations_at_tier,
    derived_from,
    descendants_of,
    iter_all_annotations,
    open_project_stores,
)
from .freshness import FreshnessVerdict, stale_after, stale_verdicts
from .migrate import migrate_to_graph, is_migrated
from .script_segmentation import (
    PanelProposal,
    build_prompt,
    segment_script_into_panels,
)
from .storyboard import (
    execute_render_panel_images,
    open_storyboard,
    plan_render_panel_images,
    project_asset_id,
    save_storyboard,
    storyboard_db_path,
    storyboard_from_shots,
)
from .renderers import (
    Strategy,
    get_strategy,
    list_strategies,
    register_strategy,
    strategies,
)
from .transforms import (
    BaseTransform,
    Transform,
    TransformInputs,
    TransformResult,
    get_transform,
    list_transforms,
    register_transform,
    transforms,
)
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
    create_genre_project,
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
    "SCHEMA_VERSION",
    "BaseTransform",
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
    "create_genre_project",
    "Project",
    "ProjectGraph",
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
    "annotations_at_tier",
    "apply_to_projects",
    "clone_project",
    "compose_report",
    "derived_from",
    "descendants_of",
    "freshness",
    "execute_render",
    "execute_render_panel_images",
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
    "open_storyboard",
    "plan_render_panel_images",
    "plan_render_shot",
    "prepare_shot",
    "project_asset_id",
    "register_genre",
    "register_strategy",
    "register_transform",
    "save_storyboard",
    "shot_report",
    "stale_after",
    "stale_verdicts",
    "storyboard_db_path",
    "storyboard_from_shots",
    "strategies",
    "summarize_all",
    "transforms",
]
