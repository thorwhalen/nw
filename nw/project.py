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
from pathlib import Path
from typing import Any, Iterable, Optional

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

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        if not self.root.exists():
            raise FileNotFoundError(f"Project root does not exist: {self.root}")
        if not self.project_file.exists():
            raise FileNotFoundError(
                f"No {_PROJECT_FILE_NAME} at {self.root} — "
                "use Project.init() to bootstrap a new project."
            )

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
        for sub in ("characters", "environments", "shots", "output", "lyrics", "script", "song", ".nw"):
            (root / sub).mkdir(exist_ok=True)

        spec = ProjectSpec(
            schema_version=SCHEMA_VERSION,
            title=title or root.name,
        )
        _write_spec(root, spec)

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
        return ProjectSpec.model_validate_json(self.project_file.read_text())

    def write_spec(self, spec: ProjectSpec) -> None:
        _write_spec(self.root, spec)

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
        """Add a character (idempotent: re-adds update the description)."""
        d = self.character_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "refs").mkdir(exist_ok=True)
        (d / "selected").mkdir(exist_ok=True)
        ref = CharacterRef(name=name, description=description)
        spec = self.read_spec()
        chars = tuple(c for c in spec.characters if c.name != name) + (ref,)
        self.update_spec(characters=chars)
        # Initialize a card.json so set_character_anchor / list_character_images
        # always have something to read.
        card_path = d / "card.json"
        if not card_path.exists():
            card_path.write_text(
                json.dumps(
                    {
                        "name": name,
                        "description": description,
                        "reference_image_path": "",
                        "voice": {},
                    },
                    indent=2,
                )
            )
        return ref

    def read_character_card(self, name: str) -> dict[str, Any]:
        path = self.character_dir(name) / "card.json"
        if not path.exists():
            raise FileNotFoundError(
                f"No card.json for character {name!r}; add the character first."
            )
        return json.loads(path.read_text())

    def write_character_card(self, name: str, card: dict[str, Any]) -> None:
        path = self.character_dir(name) / "card.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(card, indent=2))

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
                if image_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                    continue
                is_anchor = (
                    anchor_path is not None
                    and image_path.resolve() == anchor_path
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
        self.log_decision(
            "set_character_anchor", character=name, anchor_path=rel
        )
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
        path = self.environment_dir(name) / "card.json"
        if not path.exists():
            return {"name": name, "description": ""}
        return json.loads(path.read_text())

    def write_environment_card(self, name: str, card: dict[str, Any]) -> None:
        path = self.environment_dir(name) / "card.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(card, indent=2))

    # -- shots / sections --------------------------------------------------

    def shot_dir(self, shot_id: str) -> Path:
        return self.root / "shots" / shot_id

    def upsert_shot(self, shot: ShotSpec) -> ShotSpec:
        d = self.shot_dir(shot.id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "shot.json").write_text(shot.model_dump_json(indent=2))
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
        """Append a one-line JSON record to ``.nw/decisions.jsonl``.

        Decisions are project-local provenance: which character anchor was
        picked, which model overrode the default, why a shot was retried.
        Future Phase-3 work will mirror these into lacing annotations with
        body schema ``annot://schema/decision/v1``.
        """
        nw_dir = self.root / ".nw"
        nw_dir.mkdir(exist_ok=True)
        path = nw_dir / "decisions.jsonl"
        from datetime import datetime, timezone
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            **payload,
        }
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")

    # -- summary -----------------------------------------------------------

    def read_summary(self) -> ProjectSummary:
        """Return a typed read view of the project — all the facts at once."""
        spec = self.read_spec()
        rendered = sum(
            1
            for shot in spec.shots
            if (self.shot_dir(shot.id) / "output.mp4").exists()
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
            has_alignment=any(
                (self.root / "lyrics").glob("alignment.*")
            ) if (self.root / "lyrics").exists() else False,
            has_script=(self.root / "script" / "script.md").exists(),
            has_final_compose=(self.root / "output" / "final.mp4").exists(),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_spec(root: Path, spec: ProjectSpec) -> None:
    path = root / _PROJECT_FILE_NAME
    path.write_text(spec.model_dump_json(indent=2))


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
