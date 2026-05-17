"""nw — Narrative Workflow.

Application-orchestration framework for audiovisual projects. A project is
a folder; an "app" (music video, explainer, podcast clip, slideshow) is a
small specialization on top.

Public surface (Phase 1b.1):

- :class:`Project` — folder facade: read/write spec, character anchors,
  shot upserts, decision log, typed summary.
- :class:`ProjectSummary` — typed read view of a project.
- :func:`clone_project` — replaces ``cp -r`` for sibling experiments.
- :func:`apply_to_projects` — replaces shell for-loops across roots.
- Schema types: :class:`ProjectSpec`, :class:`SectionSpec`, :class:`ShotSpec`,
  :class:`CharacterRef`, :class:`EnvironmentRef`, :class:`SongInfo`.

Phase 1b.2 will add ``nw.workflow`` (Plan/Execute over rendering).
Phase 1b.3 will move muvid's renderers into ``nw.renderers``.
"""

from . import bodies  # noqa: F401  — registers lacing body schemas at import
from . import graph  # noqa: F401  — `nw.graph.descendants_of(...)`
from . import inspect  # noqa: F401  — `nw.inspect.shot_report(...)`
from . import migrate  # noqa: F401  — `nw.migrate.migrate_to_graph(...)`
from . import storyboard as _storyboard_module  # noqa: F401
from .experiment import apply_to_projects, clone_project, summarize_all
from .inspect import ComposeReport, FrozenSegment, Gap, ShotReport, compose_report, shot_report
from .project import CharacterImage, Project
from .graph import (
    ProjectGraph,
    annotations_at_tier,
    derived_from,
    descendants_of,
    iter_all_annotations,
    stale_after,
)
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
from .schema import (
    SCHEMA_VERSION,
    CharacterRef,
    EnvironmentRef,
    ProjectSpec,
    ProjectSummary,
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
    "EnvironmentRef",
    "FrozenSegment",
    "Gap",
    "Project",
    "ProjectGraph",
    "ProjectSpec",
    "ProjectSummary",
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
    "execute_render",
    "execute_render_panel_images",
    "get_strategy",
    "get_transform",
    "inspect",
    "is_migrated",
    "iter_all_annotations",
    "list_strategies",
    "list_transforms",
    "migrate_to_graph",
    "open_storyboard",
    "plan_render_panel_images",
    "plan_render_shot",
    "prepare_shot",
    "project_asset_id",
    "register_strategy",
    "register_transform",
    "save_storyboard",
    "shot_report",
    "stale_after",
    "storyboard_db_path",
    "storyboard_from_shots",
    "strategies",
    "summarize_all",
    "transforms",
]
