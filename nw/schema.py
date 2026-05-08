"""Schema for an nw project — narrative-workflow SSOT data shapes.

A project is a folder with a ``project.json`` at its root. The shape is
deliberately compatible with the layout muvid established for music-video
projects, so the_bells_v* fixtures load directly into nw without
migration. nw generalizes muvid's IR by:

- making :class:`RenderStrategy` open (a string), so apps can register
  their own strategies (composite_lipsync, slideshow, panel, …) without
  touching nw,
- adding :class:`ProjectSummary` as a typed read view returned by
  :meth:`Project.read_summary`,
- promoting setters that muvid expressed via ``python -c`` glue
  (``set_title``, ``set_global_style``, ``set_character_anchor``).

Pydantic is used (instead of frozen dataclasses) for two reasons:

1. lacing already uses Pydantic — sharing the conventions keeps the
   ecosystem coherent.
2. nw will eventually round-trip schemas through HTTP/MCP; Pydantic gives
   JSON-Schema export and validation for free.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


SCHEMA_VERSION = 1


class SongInfo(BaseModel):
    """Metadata for the master audio file.

    Compatible with muvid's SongInfo by field name and type.
    """

    model_config = {"extra": "ignore"}

    audio_path: str = Field(..., description="Path relative to project root.")
    duration_s: float = Field(..., ge=0)
    sample_rate: int = Field(0, ge=0)
    bitrate: int = Field(0, ge=0)
    bpm: Optional[float] = Field(None, ge=0)


class SectionSpec(BaseModel):
    """A non-overlapping span of the project's master timeline.

    ``label`` is free-form ("intro", "verse", "chorus", "scene-1", "act-2",
    …) so different apps (music-video, explainer, podcast-clip) can use
    their own taxonomy.
    """

    model_config = {"extra": "ignore"}

    id: str
    start_s: float = Field(..., ge=0)
    end_s: float = Field(..., ge=0)
    label: str = ""
    energy: str = ""
    mood: str = ""

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


class ShotSpec(BaseModel):
    """A timeline-locked visual unit.

    ``[start_s, end_s)`` is half-open. ``render_strategy`` is an open string
    rather than a closed Literal, so apps can register their own strategies
    via :func:`nw.renderers.register_strategy` (Phase 1b.3) without modifying
    the schema.
    """

    model_config = {"extra": "ignore"}

    id: str
    start_s: float = Field(..., ge=0)
    end_s: float = Field(..., ge=0)
    section_id: str = ""
    render_strategy: str = "image_to_video"
    environment: str = ""
    characters: tuple[str, ...] = ()
    description: str = ""
    camera: str = ""
    framing: str = "medium"
    notes: str = ""

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


class CharacterRef(BaseModel):
    """Pointer to a character folder under ``characters/<name>/``."""

    model_config = {"extra": "ignore"}

    name: str
    description: str = ""


class EnvironmentRef(BaseModel):
    """Pointer to an environment folder under ``environments/<name>/``."""

    model_config = {"extra": "ignore"}

    name: str
    description: str = ""


class ProjectSpec(BaseModel):
    """The top-level project SSOT, persisted as ``project.json``.

    Field names and order are chosen to round-trip identically with muvid's
    ProjectSpec for ``schema_version=1``, so the_bells_v* fixtures (and any
    other muvid-shaped project) load and re-save without churn.
    """

    model_config = {"extra": "ignore"}

    schema_version: int = SCHEMA_VERSION
    title: str = ""
    song: Optional[SongInfo] = None
    characters: tuple[CharacterRef, ...] = ()
    environments: tuple[EnvironmentRef, ...] = ()
    sections: tuple[SectionSpec, ...] = ()
    shots: tuple[ShotSpec, ...] = ()
    global_style: str = ""
    notes: str = ""

    # -- queries -----------------------------------------------------------

    def section(self, section_id: str) -> Optional[SectionSpec]:
        for s in self.sections:
            if s.id == section_id:
                return s
        return None

    def shot(self, shot_id: str) -> Optional[ShotSpec]:
        for s in self.shots:
            if s.id == shot_id:
                return s
        return None

    def character(self, name: str) -> Optional[CharacterRef]:
        for c in self.characters:
            if c.name == name:
                return c
        return None

    def environment(self, name: str) -> Optional[EnvironmentRef]:
        for e in self.environments:
            if e.name == name:
                return e
        return None


class ProjectSummary(BaseModel):
    """Typed read view of a project — what ``muvid status`` printed, but typed.

    Returned by :meth:`Project.read_summary`. Holds the small facts the user
    most often wants: title, root, song path, counts of characters / shots /
    sections / output, plus a coarse "stages_done" list naming the lifecycle
    stages that have been reached.
    """

    model_config = {"extra": "ignore"}

    title: str
    root: str
    schema_version: int

    # Song
    song_path: Optional[str]
    song_duration_s: Optional[float]

    # Counts
    character_count: int
    environment_count: int
    section_count: int
    shot_count: int
    rendered_shot_count: int  # shots that have an output.mp4

    # Lifecycle markers — coarse-grained, present-or-not flags from disk.
    has_lyrics: bool
    has_alignment: bool
    has_script: bool
    has_final_compose: bool

    @property
    def stages_done(self) -> list[str]:
        """Coarse stage list — what's been reached, in lifecycle order."""
        out: list[str] = []
        if self.song_path:
            out.append("song")
        if self.has_lyrics:
            out.append("lyrics")
        if self.has_alignment:
            out.append("alignment")
        if self.character_count:
            out.append("characters")
        if self.environment_count:
            out.append("environments")
        if self.section_count:
            out.append("sections")
        if self.shot_count:
            out.append("shots")
        if self.has_script:
            out.append("script")
        if self.rendered_shot_count:
            out.append(f"rendered:{self.rendered_shot_count}/{self.shot_count}")
        if self.has_final_compose:
            out.append("compose")
        return out
