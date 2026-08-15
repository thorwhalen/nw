"""Project facade: a folder on disk → typed reads, typed writes, typed summary.

A nw project lives at a folder. ``project.json`` is the SSOT; every other
file is a derived artifact (lyrics, alignment store, character cards, shot
output videos, etc.).

The facade is deliberately small. It exposes:

- read/write/update of the :class:`ProjectSpec`,
- folder helpers (:meth:`character_dir`, :meth:`environment_dir`,
  :meth:`shot_dir`),
- the setter operations the muvid_project agent had to express via
  ``python -c`` glue (``set_title``, ``set_global_style``,
  ``set_character_anchor``, ``list_character_images``),
- a typed :meth:`read_summary` that returns the facts ``muvid status``
  printed,
- a :meth:`log_decision` append-only line writer.

It does NOT do rendering — that's :mod:`nw.workflow` (Phase 1b.2).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from dol import Files, mk_dirs_if_missing, wrap_kvs


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iv(start_s: float, end_s: float) -> TimeInterval:
    """Build a TimeInterval from seconds, robust to floats that don't quantize.

    Floats like 8.021333 may not be exactly representable at the default 24000
    rate; routing through a string forces the lossless path via Fraction. If
    that still rounds (sub-microsecond noise), we round to microseconds first.
    """
    return TimeInterval(
        start=_seconds_to_rt(start_s),
        end=_seconds_to_rt(end_s),
    )


def _seconds_to_rt(seconds: float):
    from fractions import Fraction
    from lacing import RationalTime, DEFAULT_RATE

    # Round to nearest sample at DEFAULT_RATE.
    samples = round(float(seconds) * DEFAULT_RATE)
    return RationalTime.from_fraction(
        Fraction(samples, DEFAULT_RATE), rate=DEFAULT_RATE
    )


from lacing import TimeInterval

from .bodies import (
    CharacterRefBodyV1,
    DecisionBodyV1,
    EnvironmentRefBodyV1,
    SectionBodyV1,
    ShotBodyV1,
)
from .graph import (
    ProjectGraph,
    descendants_of,
    iter_all_annotations,
    remove_annotations_with_traces,
)
from .bodies import GENRE_ENVELOPE_TIER, VERIFYING_TRACE_TIER
from .migrate import (
    _TIER_CHARACTER_REF,
    _TIER_DECISION,
    _TIER_ENVIRONMENT_REF,
    _TIER_SECTION,
    _TIER_SHOT,
    is_migrated,
    migrate_to_graph,
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


_BOOKKEEPING_TIERS = frozenset(
    {_TIER_DECISION, VERIFYING_TRACE_TIER, GENRE_ENVELOPE_TIER}
)
"""Tiers that record *what nw did*, not *what the project is*.

The resumption brief is a statement about project content, so these are
excluded from both halves of it: they are neither an authored change nor a
downstream consequence of one. The genre envelope qualifies on both counts:
it is parentless and written at creation, so without this it would shadow
the user's actual last authored change."""


# ---------------------------------------------------------------------------
# CharacterImage — typed view over the files under characters/<name>/
# ---------------------------------------------------------------------------


class CharacterImage:
    """One image associated with a character.

    Returned by :meth:`Project.list_character_images`. Distinguishes:

    - ``from_ref``: file lives under ``characters/<name>/refs/`` — a candidate
      from generation or upload.
    - ``from_selected``: under ``characters/<name>/selected/`` — curator-picked.
    - ``is_anchor``: this is the file the character card currently points at as
      the "use this image" anchor (lipsync seed, etc.).
    """

    __slots__ = ("path", "image_id", "from_ref", "from_selected", "is_anchor")

    def __init__(
        self,
        path: Path,
        *,
        from_ref: bool = False,
        from_selected: bool = False,
        is_anchor: bool = False,
    ):
        self.path = path
        self.image_id = path.stem
        self.from_ref = from_ref
        self.from_selected = from_selected
        self.is_anchor = is_anchor

    def __repr__(self) -> str:
        flags = []
        if self.from_ref:
            flags.append("ref")
        if self.from_selected:
            flags.append("selected")
        if self.is_anchor:
            flags.append("anchor")
        return f"<CharacterImage {self.path.name} [{','.join(flags) or '?'}]>"


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


_PROJECT_FILE_NAME = "project.json"


class Project:
    """A folder-backed nw project.

    Construct from a path (must exist + must contain ``project.json``); use
    :meth:`Project.init` to bootstrap a new project on disk.
    """

    def __init__(self, root: str | Path, *, auto_migrate: bool = True) -> None:
        self.root = Path(root).resolve()
        if not self.root.exists():
            raise FileNotFoundError(f"Project root does not exist: {self.root}")
        if not self.project_file.exists():
            raise FileNotFoundError(
                f"No {_PROJECT_FILE_NAME} at {self.root} — "
                "use Project.init() to bootstrap a new project."
            )
        # Auto-migrate pre-graph projects to the graph-backed format.
        # Idempotent — runs once per project, no-op afterward.
        if auto_migrate and not is_migrated(self.root):
            migrate_to_graph(self.root)
        self.graph = ProjectGraph(self.root)
        # Single storage seam for the project's JSON documents (see _json_docs).
        self._docs = _json_docs(self.root)

    # -- bootstrap ---------------------------------------------------------

    @classmethod
    def init(
        cls,
        root: str | Path,
        *,
        title: str = "",
        song: Optional[str | Path] = None,
        force: bool = False,
    ) -> "Project":
        """Create a new project on disk and return the :class:`Project` facade.

        Args:
            root: Folder to create. Must not exist (or pass ``force=True`` to
                overwrite an empty folder).
            title: Optional human-readable title; defaults to the folder name.
            song: Optional path to a master audio file. When given, the file
                is *copied* into ``<root>/song/`` and registered in the spec.
            force: When True, accept an existing folder if it's empty (no
                ``project.json``); refuse if a project already exists there.
        """
        root = Path(root).resolve()

        if root.exists():
            if (root / _PROJECT_FILE_NAME).exists() and not force:
                raise FileExistsError(
                    f"A project already exists at {root}. Pass force=True to overwrite."
                )
            if any(root.iterdir()) and not force:
                raise FileExistsError(
                    f"{root} is not empty. Pass force=True to use it anyway."
                )
        else:
            root.mkdir(parents=True)

        # Conventional subfolders. Empty is fine — they're created lazily
        # by the setters too, but creating them up front makes the layout
        # discoverable.
        for sub in (
            "characters",
            "environments",
            "shots",
            "output",
            "lyrics",
            "script",
            "song",
            ".nw",
        ):
            (root / sub).mkdir(exist_ok=True)

        spec = ProjectSpec(
            schema_version=SCHEMA_VERSION,
            title=title or root.name,
        )
        _write_spec(root, spec)

        # New projects are graph-native from the start; mark migrated so the
        # auto-migrator skips them. The graph store is created lazily on first
        # write.
        docs = _json_docs(root)
        if ".nw/migrated_to_graph" not in docs:
            docs[".nw/migrated_to_graph"] = {
                "migrated_at": _now_iso(),
                "counts": {"native": 1},
            }

        proj = cls(root)
        if song is not None:
            proj.set_song(song)
        return proj

    # -- properties --------------------------------------------------------

    @property
    def project_file(self) -> Path:
        return self.root / _PROJECT_FILE_NAME

    # -- spec read / write -------------------------------------------------

    def read_spec(self) -> ProjectSpec:
        """Read the project spec, synthesizing from the graph for graph-native fields.

        Project-level metadata (title, song, global_style, notes,
        schema_version) lives in ``project.json``. Sections, shots,
        characters, and environments live in the lacing graph and are
        synthesized into the returned :class:`ProjectSpec` for back-compat
        with code that still reads via ``read_spec()``.
        """
        meta = self._docs[_PROJECT_FILE_NAME]

        # Pull graph-backed entities.
        sections = tuple(
            SectionSpec(
                id=s.body.section_id,
                start_s=s.interval.start.to_seconds(),
                end_s=s.interval.end.to_seconds(),
                label=s.body.label,
                energy=s.body.energy,
                mood=s.body.mood,
            )
            for s in self.graph.sections()
        )
        shots = tuple(
            ShotSpec(
                id=s.body.shot_id,
                start_s=s.interval.start.to_seconds(),
                end_s=s.interval.end.to_seconds(),
                section_id=s.body.section_id,
                render_strategy=s.body.render_strategy,
                environment=s.body.environment,
                characters=tuple(s.body.characters),
                description=s.body.description,
                camera=s.body.camera,
                framing=s.body.framing,
                notes=s.body.notes,
            )
            for s in self.graph.shots()
        )
        characters = tuple(
            _character_ref_of_body(c.body) for c in self.graph.character_refs()
        )
        environments = tuple(
            _environment_ref_of_body(e.body) for e in self.graph.environment_refs()
        )

        song = meta.get("song")
        return ProjectSpec(
            schema_version=meta.get("schema_version", SCHEMA_VERSION),
            title=meta.get("title", ""),
            song=SongInfo.model_validate(song) if isinstance(song, dict) else None,
            characters=characters,
            environments=environments,
            sections=sections,
            shots=shots,
            global_style=meta.get("global_style", ""),
            notes=meta.get("notes", ""),
        )

    def resolved_genre(self) -> Optional[dict]:
        """The ``{genre, template, params}`` envelope this project was created as.

        The read accessor for the envelope :func:`nw.genres.initialize_genre`
        persists at creation (nw#32) — same shape as
        :func:`nw.genres.resolve_genre` returns, so consumers reuse or diff
        the *effective* creation params without re-deriving them. ``None``
        for a project with no recorded genre (created before nw#32, or not
        through the genre machinery).
        """
        body = self.graph.genre_envelope()
        if body is None:
            return None
        return {
            "genre": body.genre,
            "template": body.template,
            "params": dict(body.params),
        }

    def write_spec(self, spec: ProjectSpec) -> None:
        """Write the spec.

        For back-compat with existing code that builds a ``ProjectSpec`` and
        calls ``write_spec``, this routes graph-backed fields (sections,
        shots, characters, environments) through the graph and persists the
        rest as project.json metadata.
        """
        # Project-level metadata (no graph entities).
        meta = {
            "schema_version": spec.schema_version,
            "title": spec.title,
            "song": spec.song.model_dump() if spec.song is not None else None,
            "global_style": spec.global_style,
            "notes": spec.notes,
            "_graph_db": "project.annot.sqlite",
        }
        self._docs[_PROJECT_FILE_NAME] = meta

        # Graph entities — sync each tier to the spec by removing entries
        # not in spec and upserting the ones that are.
        self._sync_character_refs(spec.characters)
        self._sync_environment_refs(spec.environments)
        self._sync_sections(spec.sections)
        self._sync_shots(spec.shots)

    def _drop_entities_not_in(
        self, *, tier: str, identity_key: str, keep: set
    ) -> None:
        """Remove ``tier`` entities whose ``identity_key`` is not in ``keep``.

        The removal half of ``write_spec``'s reconciliation. Routes through
        :func:`nw.graph.remove_annotations_with_traces` so a removed entity's
        verifying traces go with it (nw#36) — an annotation at an entity tier
        can be a *derived* write (a reelee agent proposing a shot), and those
        carry traces.
        """
        with self.graph._open() as store:
            annotations = list(store.all())
            victims = {
                ann.id
                for ann in annotations
                if ann.tier == tier
                and isinstance(ann.body, dict)
                and ann.body.get(identity_key) not in keep
            }
            remove_annotations_with_traces(store, victims, annotations=annotations)

    def _sync_character_refs(self, refs: tuple[CharacterRef, ...]) -> None:
        self._drop_entities_not_in(
            tier=_TIER_CHARACTER_REF,
            identity_key="name",
            keep={r.name for r in refs},
        )
        for ch in refs:
            self.graph.upsert_character_ref(_character_ref_body_of(ch))

    def _sync_environment_refs(self, refs: tuple[EnvironmentRef, ...]) -> None:
        self._drop_entities_not_in(
            tier=_TIER_ENVIRONMENT_REF,
            identity_key="name",
            keep={r.name for r in refs},
        )
        for env in refs:
            self.graph.upsert_environment_ref(_environment_ref_body_of(env))

    def _sync_sections(self, sections: tuple[SectionSpec, ...]) -> None:
        self._drop_entities_not_in(
            tier=_TIER_SECTION,
            identity_key="section_id",
            keep={s.id for s in sections},
        )
        for sec in sections:
            self.graph.upsert_section(
                SectionBodyV1(
                    section_id=sec.id,
                    label=sec.label,
                    energy=sec.energy,
                    mood=sec.mood,
                ),
                interval=_iv(sec.start_s, sec.end_s),
            )

    def _sync_shots(self, shots: tuple[ShotSpec, ...]) -> None:
        self._drop_entities_not_in(
            tier=_TIER_SHOT,
            identity_key="shot_id",
            keep={s.id for s in shots},
        )
        for shot in shots:
            self.graph.upsert_shot(
                ShotBodyV1(
                    shot_id=shot.id,
                    section_id=shot.section_id,
                    render_strategy=shot.render_strategy,
                    environment=shot.environment,
                    characters=tuple(shot.characters),
                    description=shot.description,
                    camera=shot.camera,
                    framing=shot.framing,
                    notes=shot.notes,
                ),
                interval=_iv(shot.start_s, shot.end_s),
            )

    def update_spec(self, **changes: Any) -> ProjectSpec:
        """Apply field-level changes to the spec; return the new spec."""
        spec = self.read_spec()
        new = spec.model_copy(update=changes)
        self.write_spec(new)
        return new

    # -- top-level setters (replacements for python -c glue) ---------------

    def set_title(self, title: str) -> ProjectSpec:
        """Set the project title."""
        return self.update_spec(title=title)

    def set_global_style(self, style: str) -> ProjectSpec:
        """Set the project-level visual style hint."""
        return self.update_spec(global_style=style)

    def set_song(self, source: str | Path, *, copy: bool = True) -> SongInfo:
        """Register an audio file as the project's master song.

        Probes duration / sample-rate / bitrate via ``mixing.audio.Audio`` if
        available, else leaves them at 0 (the spec accepts the SSOT-only
        path with placeholder metadata).
        """
        src = Path(source).resolve()
        if not src.exists():
            raise FileNotFoundError(f"Song file not found: {src}")

        song_dir = self.root / "song"
        song_dir.mkdir(exist_ok=True)
        if copy:
            dst = song_dir / src.name
            if not dst.exists() or src.resolve() != dst.resolve():
                shutil.copy2(src, dst)
        else:
            dst = src

        rel = (
            dst.relative_to(self.root).as_posix()
            if dst.is_relative_to(self.root)
            else str(dst)
        )

        info = _probe_audio(dst)
        song = SongInfo(
            audio_path=rel,
            duration_s=info.get("duration_s", 0.0),
            sample_rate=info.get("sample_rate", 0),
            bitrate=info.get("bitrate", 0),
            bpm=info.get("bpm"),
        )
        self.update_spec(song=song)
        return song

    def song_path(self) -> Optional[Path]:
        spec = self.read_spec()
        if spec.song is None:
            return None
        p = Path(spec.song.audio_path)
        return p if p.is_absolute() else self.root / p

    # -- characters --------------------------------------------------------

    def character_dir(self, name: str) -> Path:
        return self.root / "characters" / name

    def add_character(self, name: str, *, description: str = "") -> CharacterRef:
        """Add a character (idempotent: re-adds update the description).

        Re-adding updates *only* the description: any stable attributes
        already recorded on the character (costume, palette anchors,
        ``do_not_do`` …) are carried over, so calling this again is not a
        way to lose them.
        """
        d = self.character_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "refs").mkdir(exist_ok=True)
        (d / "selected").mkdir(exist_ok=True)
        spec = self.read_spec()
        existing = next((c for c in spec.characters if c.name == name), None)
        ref = (
            existing.model_copy(update={"description": description})
            if existing is not None
            else CharacterRef(name=name, description=description)
        )
        chars = tuple(c for c in spec.characters if c.name != name) + (ref,)
        self.update_spec(characters=chars)
        # Initialize a card.json so set_character_anchor / list_character_images
        # always have something to read.
        card_key = f"characters/{name}/card.json"
        if card_key not in self._docs:
            self._docs[card_key] = {
                "name": name,
                "description": description,
                "reference_image_path": "",
                "voice": {},
            }
        return ref

    def read_character_card(self, name: str) -> dict[str, Any]:
        key = f"characters/{name}/card.json"
        if key not in self._docs:
            raise FileNotFoundError(
                f"No card.json for character {name!r}; add the character first."
            )
        return self._docs[key]

    def write_character_card(self, name: str, card: dict[str, Any]) -> None:
        self._docs[f"characters/{name}/card.json"] = card

    def list_character_images(self, name: str) -> list[CharacterImage]:
        """Return all images associated with a character, with provenance flags.

        Walks ``characters/<name>/refs/`` and ``characters/<name>/selected/``.
        Marks the file the card's ``reference_image_path`` points at as
        ``is_anchor=True``.
        """
        d = self.character_dir(name)
        if not d.exists():
            return []

        anchor_path: Optional[Path] = None
        try:
            card = self.read_character_card(name)
            ref = card.get("reference_image_path") or ""
            if ref:
                p = Path(ref)
                anchor_path = (p if p.is_absolute() else self.root / p).resolve()
        except FileNotFoundError:
            pass

        out: list[CharacterImage] = []
        for sub, flag in (("refs", "from_ref"), ("selected", "from_selected")):
            sub_dir = d / sub
            if not sub_dir.exists():
                continue
            for image_path in sorted(sub_dir.iterdir()):
                if not image_path.is_file():
                    continue
                if image_path.suffix.lower() not in {
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".webp",
                    ".gif",
                }:
                    continue
                is_anchor = (
                    anchor_path is not None and image_path.resolve() == anchor_path
                )
                out.append(
                    CharacterImage(
                        image_path,
                        from_ref=(flag == "from_ref"),
                        from_selected=(flag == "from_selected"),
                        is_anchor=is_anchor,
                    )
                )
        return out

    def set_character_anchor(self, name: str, image_path: str | Path) -> dict[str, Any]:
        """Pick an existing image as the character's anchor (lipsync seed, etc.).

        Returns the updated card. Raises if the image isn't under the
        character's folder, since cross-character anchoring is almost always
        a mistake.
        """
        char_dir = self.character_dir(name).resolve()
        p = Path(image_path)
        abs_p = (p if p.is_absolute() else self.root / p).resolve()
        if not abs_p.exists():
            raise FileNotFoundError(f"Image not found: {abs_p}")
        try:
            abs_p.relative_to(char_dir)
        except ValueError as e:
            raise ValueError(
                f"Image {abs_p} is not under {char_dir}. Refusing to anchor "
                f"to an out-of-character file (likely a mistake)."
            ) from e

        card = self.read_character_card(name)
        # Store a project-relative path so the anchor moves with the project.
        rel = abs_p.relative_to(self.root).as_posix()
        card["reference_image_path"] = rel
        self.write_character_card(name, card)
        self.log_decision("set_character_anchor", character=name, anchor_path=rel)
        return card

    # -- environments ------------------------------------------------------

    def environment_dir(self, name: str) -> Path:
        return self.root / "environments" / name

    def add_environment(self, name: str, *, description: str = "") -> EnvironmentRef:
        d = self.environment_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        ref = EnvironmentRef(name=name, description=description)
        spec = self.read_spec()
        envs = tuple(e for e in spec.environments if e.name != name) + (ref,)
        self.update_spec(environments=envs)
        return ref

    def read_environment_card(self, name: str) -> dict[str, Any]:
        key = f"environments/{name}/card.json"
        if key not in self._docs:
            return {"name": name, "description": ""}
        return self._docs[key]

    def write_environment_card(self, name: str, card: dict[str, Any]) -> None:
        self._docs[f"environments/{name}/card.json"] = card

    # -- shots / sections --------------------------------------------------

    def shot_dir(self, shot_id: str) -> Path:
        return self.root / "shots" / shot_id

    def upsert_shot(self, shot: ShotSpec) -> ShotSpec:
        self._docs[f"shots/{shot.id}/shot.json"] = json.loads(shot.model_dump_json())
        spec = self.read_spec()
        shots = tuple(s for s in spec.shots if s.id != shot.id) + (shot,)
        # Preserve start_s ordering.
        shots = tuple(sorted(shots, key=lambda s: s.start_s))
        self.update_spec(shots=shots)
        return shot

    def upsert_section(self, section: SectionSpec) -> SectionSpec:
        spec = self.read_spec()
        sections = tuple(s for s in spec.sections if s.id != section.id) + (section,)
        sections = tuple(sorted(sections, key=lambda s: s.start_s))
        self.update_spec(sections=sections)
        return section

    # -- decisions log -----------------------------------------------------

    def log_decision(self, kind: str, **payload: Any) -> None:
        """Record a typed decision in the project graph + the JSONL audit log.

        Decisions are project-local provenance: which character anchor was
        picked, which model overrode the default, why a shot was retried.
        Both surfaces stay in sync:
        - The lacing graph (``decision`` tier, body schema
          ``annot://schema/decision/v1``) is the SSOT — reelee will surface
          these in inspector / network views.
        - ``.nw/decisions.jsonl`` continues as a tail-grep-able audit trail.
        """
        # Graph (canonical).
        try:
            self.graph.append_decision(DecisionBodyV1(kind=kind, payload=dict(payload)))
        except Exception:
            # Don't fail user operations on graph write errors; the JSONL
            # below preserves the record as a fallback audit.
            pass

        # JSONL audit log (back-compat surface).
        self._append_decision_log({"ts": _now_iso(), "kind": kind, **payload})

    def _append_decision_log(self, record: dict[str, Any]) -> None:
        """Append one record to the ``.nw/decisions.jsonl`` audit stream.

        This is the one persistence path that is *not* routed through the
        :func:`_json_docs` key-value store: an append-only, tail-grep-able log
        is a stream, not a document, and modelling it as a ``MutableMapping``
        would force a read-modify-write per line. It is a secondary surface —
        the lacing graph (``decision`` tier) is the store-backed SSOT — so the
        raw append is isolated here behind a single named method.
        """
        nw_dir = self.root / ".nw"
        nw_dir.mkdir(exist_ok=True)
        with open(nw_dir / "decisions.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")

    # -- summary -----------------------------------------------------------

    def read_summary(self) -> ProjectSummary:
        """Return a typed read view of the project — all the facts at once."""
        spec = self.read_spec()
        rendered = sum(
            1 for shot in spec.shots if (self.shot_dir(shot.id) / "output.mp4").exists()
        )

        return ProjectSummary(
            title=spec.title,
            root=str(self.root),
            schema_version=spec.schema_version,
            song_path=str(self.song_path()) if self.song_path() else None,
            song_duration_s=spec.song.duration_s if spec.song else None,
            character_count=len(spec.characters),
            environment_count=len(spec.environments),
            section_count=len(spec.sections),
            shot_count=len(spec.shots),
            rendered_shot_count=rendered,
            has_lyrics=(self.root / "lyrics" / "lyrics.md").exists()
            or (self.root / "lyrics" / "transcript.json").exists(),
            has_alignment=any((self.root / "lyrics").glob("alignment.*"))
            if (self.root / "lyrics").exists()
            else False,
            has_script=(self.root / "script" / "script.md").exists(),
            has_final_compose=(self.root / "output" / "final.mp4").exists(),
        )

    # -- session resumption -------------------------------------------------

    def total_spend_usd(self) -> float:
        """Sum the cost recorded on every decision in the project.

        Prefers each decision's *actual* per-artifact ``cost_usd`` and falls
        back to its ``total_estimated_cost_usd`` when no artifact costs were
        recorded.

        Walks **every store scope** (graph, storyboard, alignment), not just
        the project graph: a decision written to the storyboard scope is money
        that was spent, and counting only one scope would silently *under*-report
        while :attr:`~nw.schema.ResumptionBrief.caveats` claims an upper bound.

        **This is an upper bound on money usefully spent.** A render that was
        billed and then failed is recorded exactly like one that succeeded,
        because nothing in the execution layer records a per-branch outcome
        yet. When failure isolation lands, this should sum over the *produced*
        branches only — and this method is the one place that changes.
        """
        return sum(
            _decision_spend_usd(ann.body.get("payload") or {})
            for ann in iter_all_annotations(self.root)
            if ann.tier == _TIER_DECISION and isinstance(ann.body, dict)
        )

    def resumption_brief(self, *, recent: int = 10) -> ResumptionBrief:
        """Return a "where we left off" snapshot for the start of a session.

        Pure data, fully offline: a decision-log tail, what is reachable
        downstream of the last change, recorded spend, and a deterministic
        list of suggested next actions. reelee renders it as prose and
        injects it as the opening context of a session.

        Read :class:`~nw.schema.ResumptionBrief` before trusting the numbers —
        two of them are upper bounds, and the brief says so in
        :attr:`~nw.schema.ResumptionBrief.caveats` rather than only in a
        docstring.

        Args:
            recent: How many decision-log entries to include, most recent last.
        """
        spec = self.read_spec()
        annotations = list(iter_all_annotations(self.root))

        # Stable ordering: sort by generation time, ties broken by write order.
        indexed = sorted(
            enumerate(annotations),
            key=lambda pair: (
                pair[1].provenance.generated_at_time.to_seconds(),
                pair[0],
            ),
        )

        last_session_at, gap_seconds = _last_touched(indexed)
        decisions = tuple(
            DecisionEntry(
                kind=ann.body.get("kind", ""),
                at=_iso_utc(ann.provenance.generated_at_time),
                payload=ann.body.get("payload") or {},
            )
            for _, ann in indexed
            if ann.tier == _TIER_DECISION and isinstance(ann.body, dict)
        )[-recent:]

        last_change = _last_authored_change(indexed)
        downstream: tuple[str, ...] = ()
        if last_change is not None:
            downstream = tuple(
                str(a.id)
                for a in descendants_of(self.root, last_change.id)
                if a.tier not in _BOOKKEEPING_TIERS
            )

        unrendered = tuple(
            shot.id
            for shot in spec.shots
            if not (self.shot_dir(shot.id) / "output.mp4").exists()
        )
        spend = self.total_spend_usd()

        return ResumptionBrief(
            title=spec.title,
            root=str(self.root),
            last_session_at=last_session_at,
            gap_seconds=gap_seconds,
            recent_decisions=decisions,
            downstream_of_last_authored_change=downstream,
            last_authored_change_id=(
                str(last_change.id) if last_change is not None else None
            ),
            total_spend_usd=spend,
            unrendered_shot_ids=unrendered,
            suggested_next=_suggested_next(self, spec, unrendered, downstream),
            caveats=_brief_caveats(downstream=downstream, spend=spend),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_docs(root: str | Path):
    """The storage facade for a project's JSON documents.

    A ``dol`` ``MutableMapping`` keyed by project-relative POSIX path
    (``"project.json"``, ``"characters/<name>/card.json"``,
    ``"environments/<name>/card.json"``, ``"shots/<id>/shot.json"``, the
    ``".nw/migrated_to_graph"`` sentinel). Values are Python objects; on disk
    they are ``indent=2`` JSON, preserving the historical layout so existing
    projects and any external readers keep working. Parent directories are
    created on write; a missing key raises ``KeyError``.

    This is the single seam through which project state is persisted — no
    business-method touches ``open``/``write_text`` directly (the append-only
    ``decisions.jsonl`` audit stream is the one documented exception; see
    :meth:`Project._append_decision_log`).
    """
    return wrap_kvs(
        mk_dirs_if_missing(Files(str(root))),
        data_of_obj=lambda obj: json.dumps(obj, indent=2).encode("utf-8"),
        obj_of_data=lambda data: json.loads(data),
    )


def _write_spec(root: Path, spec: ProjectSpec) -> None:
    _json_docs(root)[_PROJECT_FILE_NAME] = json.loads(spec.model_dump_json())


# -- entity refs: the one mapping between the spec types and the graph bodies --
#
# ``CharacterRef``/``EnvironmentRef`` (nw.schema) and their ``*BodyV1``
# counterparts (nw.bodies) carry the same fields; read_spec/write_spec convert
# between them on every spec update, so **a field present on one side and
# missing on the other is silently erased by the next update_spec** — the
# failure ``reference_image_urls`` had on both tiers.
#
# ``tests/test_project.py`` asserts these tuples equal both models' field sets,
# so adding a field to a body and forgetting the spec type fails the suite
# instead of losing user data.

_CHARACTER_REF_FIELDS = (
    "name",
    "description",
    "reference_image_urls",
    "costume",
    "age",
    "default_setting",
    "distinguishing_features",
    "palette_anchors",
    "do_not_do",
)

_ENVIRONMENT_REF_FIELDS = (
    "name",
    "description",
    "reference_image_urls",
)


def _iso_utc(rt) -> str:
    """A lacing ``RationalTime`` wall clock → an ISO-8601 UTC string."""
    return datetime.fromtimestamp(rt.to_seconds(), tz=timezone.utc).isoformat()


def _last_touched(indexed) -> tuple[Optional[str], Optional[float]]:
    """``(iso_timestamp, seconds_since)`` of the most recent graph write."""
    if not indexed:
        return None, None
    newest = indexed[-1][1].provenance.generated_at_time
    at = datetime.fromtimestamp(newest.to_seconds(), tz=timezone.utc)
    return at.isoformat(), max(0.0, (datetime.now(timezone.utc) - at).total_seconds())


def _last_authored_change(indexed):
    """The most recent annotation the *user* authored, or ``None``.

    "Authored" means it has no ``was_derived_from`` parents and is not on a
    **bookkeeping** tier — a shot, a section, a character or environment ref
    the user wrote. That is what makes the downstream walk meaningful.

    Both bookkeeping tiers would otherwise qualify, and both are written
    *after* the edit they describe, so either would shadow the real answer:
    a decision-log row, and a verifying trace (which by design carries no
    provenance parents — :mod:`nw.bodies.verifying_trace`).

    Walking from "the most recently generated annotation" instead is
    **inverted**: a descendant is by construction generated *after* its
    ancestor, so the newest node in a provenance graph is a leaf. Its
    descendant set is empty in exactly the case the brief exists for ("I
    edited the costume — what did that invalidate?"), and non-empty only for
    audit rows appended after the edit.
    """
    return next(
        (
            ann
            for _, ann in reversed(indexed)
            if ann.tier not in _BOOKKEEPING_TIERS
            and not ann.provenance.was_derived_from
        ),
        None,
    )


def _decision_spend_usd(payload: dict[str, Any]) -> float:
    """Money recorded on one decision payload, actual costs preferred."""
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, list):
        costs = [
            a["cost_usd"]
            for a in artifacts
            if isinstance(a, dict) and isinstance(a.get("cost_usd"), (int, float))
        ]
        if costs:
            return float(sum(costs))
    estimated = payload.get("total_estimated_cost_usd")
    return float(estimated) if isinstance(estimated, (int, float)) else 0.0


def _brief_caveats(*, downstream: tuple[str, ...], spend: float) -> tuple[str, ...]:
    """The qualifications that apply to *this* brief, as renderable data.

    Emitted only when the number they qualify is non-zero, so a fresh project
    carries none — and each string is deleted by the change that makes it
    false, rather than living on in a docstring nobody renders.
    """
    out: list[str] = []
    if downstream:
        out.append(
            f"{len(downstream)} items are downstream of the last authored "
            "change by provenance reachability. That is an upper bound on "
            "what needs attention: nothing is compared, so anything already "
            "regenerated since is still counted."
        )
    if spend:
        out.append(
            f"${spend:.2f} is every render decision ever recorded, including "
            "any that were billed and then failed — no per-branch outcome is "
            "recorded yet."
        )
    return tuple(out)


def _suggested_next(
    project: "Project",
    spec: ProjectSpec,
    unrendered: tuple[str, ...],
    downstream: tuple[str, ...],
) -> tuple[str, ...]:
    """Deterministic next-action hints, most actionable first.

    Deliberately a pure function of project state — same project, same
    suggestions — so a consumer can diff two briefs and an LLM never has to
    be asked what to do next.
    """
    out: list[str] = []
    if not spec.shots and not spec.characters and spec.song is None:
        return ("Empty project — register a song or import a script to start.",)
    if unrendered:
        out.append(
            f"{len(unrendered)} of {len(spec.shots)} shots have never been "
            f"rendered: {', '.join(unrendered[:5])}"
            + ("…" if len(unrendered) > 5 else "")
        )
    if downstream:
        out.append(
            f"Up to {len(downstream)} items are downstream of the last "
            "authored change — review or regenerate them."
        )
    anchorless = tuple(
        c.name for c in spec.characters if not _character_has_anchor(project, c.name)
    )
    if anchorless:
        out.append("No reference image locked for: " + ", ".join(sorted(anchorless)))
    return tuple(out)


def _character_has_anchor(project: "Project", name: str) -> bool:
    """True when the character's card names an anchor image that exists."""
    try:
        card = project.read_character_card(name)
    except FileNotFoundError:
        return False
    rel = card.get("reference_image_path") or ""
    return bool(rel) and (project.root / rel).exists()


def _character_ref_of_body(body: CharacterRefBodyV1) -> CharacterRef:
    """Graph body → the spec-level :class:`CharacterRef` (lossless)."""
    return CharacterRef(**{f: getattr(body, f) for f in _CHARACTER_REF_FIELDS})


def _character_ref_body_of(ref: CharacterRef) -> CharacterRefBodyV1:
    """Spec-level :class:`CharacterRef` → the graph body (lossless)."""
    return CharacterRefBodyV1(**{f: getattr(ref, f) for f in _CHARACTER_REF_FIELDS})


def _environment_ref_of_body(body: EnvironmentRefBodyV1) -> EnvironmentRef:
    """Graph body → the spec-level :class:`EnvironmentRef` (lossless)."""
    return EnvironmentRef(**{f: getattr(body, f) for f in _ENVIRONMENT_REF_FIELDS})


def _environment_ref_body_of(ref: EnvironmentRef) -> EnvironmentRefBodyV1:
    """Spec-level :class:`EnvironmentRef` → the graph body (lossless)."""
    return EnvironmentRefBodyV1(**{f: getattr(ref, f) for f in _ENVIRONMENT_REF_FIELDS})


def _probe_audio(path: Path) -> dict:
    """Best-effort audio metadata probe via ``mixing.audio``; quiet on failure."""
    try:
        from mixing.audio import Audio  # type: ignore[import-not-found]

        a = Audio(str(path))
        return {
            "duration_s": float(a.duration),
            "sample_rate": int(a.sample_rate),
            "bitrate": int(getattr(a, "bitrate", 0) or 0),
        }
    except Exception:
        return {}
