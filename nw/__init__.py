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
from .experiment import apply_to_projects, clone_project, summarize_all
from .inspect import ComposeReport, FrozenSegment, Gap, ShotReport, compose_report, shot_report
from .project import CharacterImage, Project
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
    "get_strategy",
    "inspect",
    "list_strategies",
    "plan_render_shot",
    "prepare_shot",
    "register_strategy",
    "shot_report",
    "strategies",
    "summarize_all",
]
