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
    """Pointer to a character folder under ``characters/<name>/``.

    The stable-attribute fields mirror
    :class:`nw.bodies.CharacterRefBodyV1` field-for-field, and that is
    load-bearing rather than cosmetic: :meth:`nw.Project.read_spec` builds
    a ``CharacterRef`` from the graph body and
    :meth:`nw.Project.write_spec` writes the body back from the
    ``CharacterRef``. Any field present on the body but missing here is
    **silently erased** by the next ``update_spec`` — which is what used to
    happen to ``reference_image_urls``. Add a field to one, add it to both.
    """

    model_config = {"extra": "ignore"}

    name: str
    description: str = ""
    reference_image_urls: tuple[str, ...] = ()
    costume: str = ""
    age: str = ""
    default_setting: str = ""
    distinguishing_features: tuple[str, ...] = ()
    palette_anchors: tuple[str, ...] = ()
    do_not_do: tuple[str, ...] = ()


class EnvironmentRef(BaseModel):
    """Pointer to an environment folder under ``environments/<name>/``.

    Mirrors :class:`nw.bodies.EnvironmentRefBodyV1` field-for-field, for the
    same load-bearing reason as :class:`CharacterRef` — see that docstring.
    ``reference_image_urls`` (the lookbook the FE curates for a *location*)
    was erased by every ``update_spec`` until this mirror was completed.
    """

    model_config = {"extra": "ignore"}

    name: str
    description: str = ""
    reference_image_urls: tuple[str, ...] = ()


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


# ---------------------------------------------------------------------------
# Session resumption — "where we left off"
# ---------------------------------------------------------------------------


class DecisionEntry(BaseModel):
    """One entry of a project's decision log, flattened for display."""

    model_config = {"extra": "ignore"}

    kind: str
    at: Optional[str] = Field(
        None,
        description=(
            "ISO-8601 UTC timestamp from the annotation's "
            "``provenance.generated_at_time``. ``None`` when the decision "
            "predates provenance timestamps."
        ),
    )
    payload: dict = Field(default_factory=dict)


class ResumptionBrief(BaseModel):
    """A "where we left off" snapshot, returned by :meth:`nw.Project.resumption_brief`.

    Pure data: no fal calls, no LLM, no network. reelee renders it as prose
    and injects it as the first tool-result of a session.

    **The field names are chosen to be honest about what nw can currently
    measure**, because a confidently wrong number is worse than no number:

    - :attr:`downstream_of_last_authored_change` is *not* "stale". It is
      ``nw.descendants_of`` — pure provenance reachability, comparing no
      content and no timestamp — so this set includes everything already
      regenerated since the change. It is an **upper bound** on what needs
      attention, and it is named for what it measures.

      ``nw.stale_after`` is the narrower answer and it now cuts off early
      (nw#24), so switching this field to it would return a smaller and
      correct set. That is deliberately **not** done here: the field would
      then be named for the wrong measurement, and which of the two a
      resumption brief should show is nw#7's call, not nw#24's. Callers who
      want the exact set can call ``nw.stale_after`` with
      :attr:`last_authored_change_id`.
    - The walk starts at the last **authored** change — the most recent
      annotation the user wrote (a shot, a section, a character or
      environment ref), never one a Transform derived. Walking from "the
      newest annotation" instead would be inverted: the newest node in a
      provenance graph is by construction a *leaf*, so its descendant set is
      empty in exactly the case the field exists for.
    - :attr:`total_spend_usd` sums *every* recorded render decision across
      every store scope. Nothing records per-branch outcomes yet, so a render
      that failed after being billed is counted here exactly like one that
      succeeded. Also an upper bound.

    :attr:`caveats` carries those qualifications as data — so a consumer
    renders them next to the numbers instead of rediscovering them.
    """

    model_config = {"extra": "ignore"}

    title: str
    root: str

    last_session_at: Optional[str] = Field(
        None, description="ISO-8601 UTC time of the most recent graph write."
    )
    gap_seconds: Optional[float] = Field(
        None, description="Seconds between ``last_session_at`` and now."
    )

    recent_decisions: tuple[DecisionEntry, ...] = ()

    downstream_of_last_authored_change: tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            "Annotation ids reachable from :attr:`last_authored_change_id` "
            "via ``was_derived_from``, excluding decision-log rows (an audit "
            "entry is never something to regenerate). Reachability, not "
            "staleness — an upper bound. See the class docstring."
        ),
    )
    last_authored_change_id: Optional[str] = Field(
        None,
        description=(
            "The annotation the walk started from: the most recent one the "
            "user authored — no ``was_derived_from`` parents and not a "
            "decision — i.e. the last edit whose consequences are downstream."
        ),
    )

    total_spend_usd: float = Field(
        0.0,
        description=(
            "Sum over recorded render-decision cost payloads, across every "
            "store scope. Includes renders that were billed and then failed "
            "— an upper bound."
        ),
    )

    unrendered_shot_ids: tuple[str, ...] = ()

    suggested_next: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Deterministic next-action hints, most actionable first.",
    )
    caveats: tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            "Known imprecisions in *this* brief, emitted only when the "
            "number they qualify is non-zero."
        ),
    )

    @property
    def downstream_count(self) -> int:
        return len(self.downstream_of_last_authored_change)
