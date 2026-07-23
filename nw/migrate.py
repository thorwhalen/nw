"""Idempotent migration: project.json (sections/shots/refs) → lacing graph.

Pre-graph nw projects (and all the_bells_v* fixtures from the muvid era)
keep sections, shots, characters, and environments in ``project.json`` as
arrays. From Phase 3 forward, those live in a per-project lacing annotation
store so reelee can walk the graph for freshness analysis, provenance
queries, and view rendering. The backend is chosen by :mod:`nw.graph_backend`
— a per-project ``SqliteStore`` file by default, or a shared Postgres DB when
``NW_GRAPH_BACKEND=postgres`` (Phase 4, reelee#177).

This module's job is to bridge the two formats *without losing data and
without requiring the user to do anything*. :func:`migrate_to_graph`:

- Is idempotent — running it twice is a no-op.
- Only writes the graph; the original ``project.json`` is left in place
  (and trimmed of fields the graph now owns, with the original kept under
  ``.nw/project.json.pre-graph.bak`` so a downgrade is possible).
- Marks completion via a ``.nw/migrated_to_graph`` sentinel file.

Project-level metadata (title, song, global_style, notes, schema_version)
stays in ``project.json``. Sections, shots, characters, environments,
decisions move into the graph.
"""

from __future__ import annotations

import json
import shutil
import uuid as _uuid
from pathlib import Path
from typing import Any, Optional

from lacing import (
    Annotation,
    MediaRef,
    Provenance,
    Tier,
    TierStereotype,
    TimeInterval,
)
from lacing.artifact import _now_rt
from lacing.store import IntervalAnnotationStore

from .bodies import (
    CHARACTER_REF_BODY_SCHEMA_URI,
    DECISION_BODY_SCHEMA_URI,
    ENVIRONMENT_REF_BODY_SCHEMA_URI,
    SECTION_BODY_SCHEMA_URI,
    SHOT_BODY_SCHEMA_URI,
)
from .graph_backend import SCOPE_GRAPH, open_graph_store


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROJECT_GRAPH_DB_NAME = "project.annot.sqlite"
_MIGRATED_SENTINEL_NAME = "migrated_to_graph"
_BACKUP_NAME = "project.json.pre-graph.bak"

# Tiers used for project-graph annotations.
_TIER_SECTION = "section"
_TIER_SHOT = "shot"
_TIER_CHARACTER_REF = "character-ref"
_TIER_ENVIRONMENT_REF = "environment-ref"
_TIER_DECISION = "decision"

_PROJECT_TIERS = (
    _TIER_SECTION,
    _TIER_SHOT,
    _TIER_CHARACTER_REF,
    _TIER_ENVIRONMENT_REF,
    _TIER_DECISION,
)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def project_graph_db_path(project_root: Path) -> Path:
    return Path(project_root) / _PROJECT_GRAPH_DB_NAME


def is_migrated(project_root: Path) -> bool:
    """True iff this project has been migrated to the lacing graph."""
    sentinel = Path(project_root) / ".nw" / _MIGRATED_SENTINEL_NAME
    return sentinel.exists()


def open_project_graph(project_root: Path) -> IntervalAnnotationStore:
    """Open (and create on first call) the project's graph store, with tiers.

    The backend (SQLite file by default, or a shared Postgres DB when
    ``NW_GRAPH_BACKEND=postgres``) is resolved by :mod:`nw.graph_backend` — the
    single config-driven seam. Callers get an ``IntervalAnnotationStore`` and
    never learn which backend answered.
    """
    db = project_graph_db_path(project_root)
    store = open_graph_store(
        db, asset_id=project_asset_id(project_root), scope=SCOPE_GRAPH
    )
    _ensure_tiers(store)
    return store


def _ensure_tiers(store: IntervalAnnotationStore) -> None:
    for name in _PROJECT_TIERS:
        store.add_tier(Tier(name=name, stereotype=TierStereotype.NONE))


def project_asset_id(project_root: Path) -> str:
    """The asset_id used as the project's graph anchor.

    For projects with a song registered in project.json, this is the SHA-256
    of the song bytes. Otherwise a stable fallback derived from the title.

    Mirrors :func:`nw.storyboard.project_asset_id` so the project graph and
    the storyboard share an asset_id.
    """
    project_json = Path(project_root) / "project.json"
    title = ""
    song_path: Optional[Path] = None
    if project_json.exists():
        spec = json.loads(project_json.read_text())
        title = spec.get("title") or ""
        song = spec.get("song")
        if isinstance(song, dict) and song.get("audio_path"):
            sp = Path(song["audio_path"])
            song_path = sp if sp.is_absolute() else Path(project_root) / sp
    if song_path is not None and song_path.exists():
        from lacing import hash_file

        return hash_file(song_path)
    import hashlib

    seed = f"nw:project:{Path(project_root).resolve()}:{title}".encode()
    return hashlib.sha256(seed).hexdigest()


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def migrate_to_graph(
    project_root: Path,
    *,
    backup: bool = True,
    was_attributed_to: str = "agent:nw.migrate",
) -> dict[str, int]:
    """Migrate ``project_root``'s project.json into the lacing graph.

    Idempotent: returns ``{"already_migrated": 1, ...}`` with zero writes if
    the sentinel exists. Otherwise reads ``project.json``, writes equivalent
    annotations into ``project.annot.sqlite``, drops the migrated arrays from
    ``project.json``, and writes the sentinel.

    Args:
        project_root: Path to a project root.
        backup: When True (default), copy the original ``project.json`` to
            ``.nw/project.json.pre-graph.bak`` before trimming.
        was_attributed_to: Provenance for the migrator. Defaults to
            ``"agent:nw.migrate"``; pass ``"user:<handle>"`` from a CLI.

    Returns:
        Counts: ``{"sections": N, "shots": N, "characters": N, "environments": N,
                   "decisions": N}``.
    """
    project_root = Path(project_root).resolve()
    counts = {
        "sections": 0,
        "shots": 0,
        "characters": 0,
        "environments": 0,
        "decisions": 0,
    }

    if is_migrated(project_root):
        return {**counts, "already_migrated": 1}

    project_json = project_root / "project.json"
    if not project_json.exists():
        raise FileNotFoundError(
            f"{project_root} is not an nw project (no project.json)."
        )

    spec = json.loads(project_json.read_text())
    if backup:
        backup_path = project_root / ".nw" / _BACKUP_NAME
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(project_json, backup_path)

    asset_id = project_asset_id(project_root)
    store = open_project_graph(project_root)
    prov = _make_prov(was_attributed_to)

    try:
        # Sections: each is an interval annotation [start_s..end_s).
        for section in spec.get("sections", ()):
            iv = TimeInterval.from_seconds(
                _wrap_seconds(section.get("start_s", 0.0)),
                _wrap_seconds(section.get("end_s", 0.0)),
            )
            ann = Annotation(
                id=_uuid.uuid4(),
                tier=_TIER_SECTION,
                reference=MediaRef(asset_id=asset_id, interval=iv),
                body={
                    "section_id": section["id"],
                    "label": section.get("label", ""),
                    "energy": section.get("energy", ""),
                    "mood": section.get("mood", ""),
                },
                body_schema_uri=SECTION_BODY_SCHEMA_URI,
                provenance=prov,
            )
            store.add(ann)
            counts["sections"] += 1

        # Shots: interval annotation, body holds strategy/characters/etc.
        for shot in spec.get("shots", ()):
            iv = TimeInterval.from_seconds(
                _wrap_seconds(shot.get("start_s", 0.0)),
                _wrap_seconds(shot.get("end_s", 0.0)),
            )
            body = {
                "shot_id": shot["id"],
                "section_id": shot.get("section_id", ""),
                "render_strategy": shot.get("render_strategy", "image_to_video"),
                "environment": shot.get("environment", ""),
                "characters": tuple(shot.get("characters", ())),
                "description": shot.get("description", ""),
                "camera": shot.get("camera", ""),
                "framing": shot.get("framing", "medium"),
                "notes": shot.get("notes", ""),
            }
            ann = Annotation(
                id=_uuid.uuid4(),
                tier=_TIER_SHOT,
                reference=MediaRef(asset_id=asset_id, interval=iv),
                body=body,
                body_schema_uri=SHOT_BODY_SCHEMA_URI,
                provenance=prov,
            )
            store.add(ann)
            counts["shots"] += 1

        # Character refs: timeless annotations (zero-width interval at t=0).
        for char in spec.get("characters", ()):
            ann = Annotation(
                id=_uuid.uuid4(),
                tier=_TIER_CHARACTER_REF,
                reference=MediaRef(
                    asset_id=asset_id,
                    interval=TimeInterval.from_seconds(0, 0),
                ),
                body={
                    "name": char["name"],
                    "description": char.get("description", ""),
                },
                body_schema_uri=CHARACTER_REF_BODY_SCHEMA_URI,
                provenance=prov,
            )
            store.add(ann)
            counts["characters"] += 1

        # Environment refs: same pattern.
        for env in spec.get("environments", ()):
            ann = Annotation(
                id=_uuid.uuid4(),
                tier=_TIER_ENVIRONMENT_REF,
                reference=MediaRef(
                    asset_id=asset_id,
                    interval=TimeInterval.from_seconds(0, 0),
                ),
                body={
                    "name": env["name"],
                    "description": env.get("description", ""),
                },
                body_schema_uri=ENVIRONMENT_REF_BODY_SCHEMA_URI,
                provenance=prov,
            )
            store.add(ann)
            counts["environments"] += 1

        # Existing .nw/decisions.jsonl entries (if any) → decision annotations.
        decisions_jsonl = project_root / ".nw" / "decisions.jsonl"
        if decisions_jsonl.exists():
            for line in decisions_jsonl.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = rec.pop("kind", "unknown")
                rec.pop("ts", None)  # generated_at_time on the annotation itself
                ann = Annotation(
                    id=_uuid.uuid4(),
                    tier=_TIER_DECISION,
                    reference=MediaRef(
                        asset_id=asset_id,
                        interval=TimeInterval.from_seconds(0, 0),
                    ),
                    body={"kind": kind, "payload": rec},
                    body_schema_uri=DECISION_BODY_SCHEMA_URI,
                    provenance=prov,
                )
                store.add(ann)
                counts["decisions"] += 1
    finally:
        _close_if_possible(store)

    # Trim project.json: keep title, song, global_style, notes, schema_version.
    trimmed = {
        "schema_version": spec.get("schema_version", 1),
        "title": spec.get("title", ""),
        "song": spec.get("song"),
        "global_style": spec.get("global_style", ""),
        "notes": spec.get("notes", ""),
        # Tombstone marker so a human reading project.json knows where the
        # graph data went.
        "_graph_db": _PROJECT_GRAPH_DB_NAME,
    }
    project_json.write_text(json.dumps(trimmed, indent=2))

    sentinel = project_root / ".nw" / _MIGRATED_SENTINEL_NAME
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(
        json.dumps({"migrated_at": _now_iso(), "counts": counts}, indent=2)
    )
    return counts


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _wrap_seconds(v: Any) -> str:
    """Stringify floats so RationalTime.from_seconds takes the lossless path.

    Floats like 8.021333 may not quantize exactly to DEFAULT_RATE. Strings
    are parsed via Fraction, which fails fast on lossy conversion — but with
    the integer-string form below we get clean ticks.
    """
    return f"{float(v):.6f}"


def _make_prov(was_attributed_to: str) -> Provenance:
    return Provenance(
        was_generated_by="agent:nw.migrate",
        was_attributed_to=was_attributed_to,
        was_derived_from=[],
        generated_at_time=_now_rt(),
        activity="migrate",
    )


def _close_if_possible(store) -> None:
    close = getattr(store, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
