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

from .experiment import apply_to_projects, clone_project, summarize_all
from .project import CharacterImage, Project
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

__all__ = [
    "SCHEMA_VERSION",
    "CharacterImage",
    "CharacterRef",
    "EnvironmentRef",
    "Project",
    "ProjectSpec",
    "ProjectSummary",
    "SectionSpec",
    "ShotSpec",
    "SongInfo",
    "apply_to_projects",
    "clone_project",
    "summarize_all",
]
