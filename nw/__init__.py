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

from . import inspect  # noqa: F401  — `nw.inspect.shot_report(...)`
from . import storyboard as _storyboard_module  # noqa: F401
from .experiment import apply_to_projects, clone_project, summarize_all
from .inspect import ComposeReport, FrozenSegment, Gap, ShotReport, compose_report, shot_report
from .project import CharacterImage, Project
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
    "CharacterImage",
    "CharacterRef",
    "ComposeReport",
    "EnvironmentRef",
    "FrozenSegment",
    "Gap",
    "Project",
    "ProjectSpec",
    "ProjectSummary",
    "SectionSpec",
    "ShotPreparation",
    "ShotReport",
    "ShotSpec",
    "SongInfo",
    "Strategy",
    "apply_to_projects",
    "clone_project",
    "compose_report",
    "execute_render",
    "execute_render_panel_images",
    "get_strategy",
    "inspect",
    "list_strategies",
    "open_storyboard",
    "plan_render_panel_images",
    "plan_render_shot",
    "prepare_shot",
    "project_asset_id",
    "register_strategy",
    "save_storyboard",
    "shot_report",
    "storyboard_db_path",
    "storyboard_from_shots",
    "strategies",
    "summarize_all",
]
