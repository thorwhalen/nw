"""Storyboard ↔ Project bridge.

A storyboard lives at ``<project_root>/storyboard.annot.sqlite`` (a lacing
``SqliteStore``). Each panel is an ``annot://schema/storyboard-panel/v1``
:class:`lacing.Annotation`; the storyboard's own asset_id is the project's
song hash, so panels share an interval space with the project's shots and
alignment.

Public surface:

- :func:`open_storyboard(project)` — load the project's storyboard, or return
  an empty one if none exists yet.
- :func:`save_storyboard(project, sb, *, panel_intervals)` — persist panels
  into the project's lacing store.
- :func:`storyboard_from_shots(project)` — convenience: build a Storyboard
  with one panel per shot, intervals matching the shots.
- :func:`plan_render_panel_images(sb, *, quality, model_overrides)` — a
  Plan with one ``generate_image`` call per panel that doesn't yet have a
  ``role="seed"`` image. Pure data; cost-aware.
- :func:`execute_render_panel_images(project, sb, plan)` — execute the Plan,
  download images into ``storyboard/`` under the project, return an updated
  Storyboard with the new ``role="seed"`` PanelImages attached.

The artful package is the storyboard *data layer*; nw.storyboard wires it
into a folder-backed nw project.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from artful import (
    PanelBody,
    PanelImage,
    Storyboard,
    load_storyboard,
    new_panel_id,
    panel_intervals_from_panels as _panel_intervals_from_panels,
    save_storyboard as _save_storyboard,
)
from artful.exports import to_html, to_markdown, from_markdown  # re-exported
from falaw import Plan, execute_plan_isolated, plan_generate_image
from lacing import (
    Artifact,
    MemoryStore,
    Tier,
    TierStereotype,
    TimeInterval,
)

from .graph import remove_annotations_with_traces
from .graph_backend import SCOPE_STORYBOARD, open_graph_store, selected_backend
from .project import Project
from .transforms import OnFailure


_STORYBOARD_DB_NAME = "storyboard.annot.sqlite"


# ---------------------------------------------------------------------------
# Project-level storyboard wiring
# ---------------------------------------------------------------------------


def storyboard_db_path(project: Project) -> Path:
    """Return the path to the project's storyboard SQLite store."""
    return project.root / _STORYBOARD_DB_NAME


def project_asset_id(project: Project) -> str:
    """The asset_id used for storyboard panel references.

    Uses the SHA-256 of the project's song bytes when available, so the
    asset_id matches whatever a downstream consumer would compute via
    :func:`lacing.hash_file`. Falls back to a stable derived id when the
    song isn't available yet.
    """
    song = project.song_path()
    if song is not None and song.exists():
        from lacing import hash_file

        return hash_file(song)
    # Stable fallback: derived from project root + title.
    spec = project.read_spec()
    seed = f"nw:project:{project.root}:{spec.title}".encode()
    return hashlib.sha256(seed).hexdigest()


def open_storyboard(project: Project) -> Storyboard:
    """Load the project's storyboard. Returns an empty one if not present."""
    db = storyboard_db_path(project)
    asset_id = project_asset_id(project)
    # In SQLite mode an absent file means "no storyboard yet". In Postgres mode
    # the file never exists; presence is a DB question, so we always open and
    # let load_storyboard return an empty Storyboard when the tenant has no
    # panels.
    if selected_backend() == "sqlite" and not db.exists():
        return Storyboard(asset_id=asset_id)
    store = open_graph_store(db, asset_id=asset_id, scope=SCOPE_STORYBOARD)
    try:
        return load_storyboard(store, asset_id=asset_id)
    finally:
        _close_if_possible(store)


def save_storyboard(
    project: Project,
    storyboard: Storyboard,
    *,
    panel_intervals: dict[str, TimeInterval],
    was_attributed_to: str = "user:nw",
    was_generated_by: str = "agent:nw.storyboard",
) -> None:
    """Persist a Storyboard into the project's SqliteStore.

    Wipes the existing storyboard panels (under the default tier) so the
    save is idempotent — re-running with edited panels replaces them rather
    than accumulating duplicates.
    """
    db = storyboard_db_path(project)
    asset_id = project_asset_id(project)
    store = open_graph_store(db, asset_id=asset_id, scope=SCOPE_STORYBOARD)
    try:
        # The store enforces a foreign key on tier; register it first so the
        # save is not rejected. Idempotent — re-registering is a no-op in lacing.
        store.add_tier(Tier(name="storyboard", stereotype=TierStereotype.NONE))
        # Drop any existing panels for this asset+tier so save is idempotent.
        # Their verifying traces go with them (nw#36): a re-saved panel is a
        # new row, and a trace describing the old one must not survive to
        # answer a freshness query for its successor.
        annotations = list(store.all())
        panel_ids = {
            ann.id
            for ann in annotations
            if ann.tier == "storyboard"
            and getattr(ann.reference, "asset_id", None) == storyboard.asset_id
        }
        remove_annotations_with_traces(store, panel_ids, annotations=annotations)

        _save_storyboard(
            storyboard,
            store,
            panel_intervals=panel_intervals,
            was_attributed_to=was_attributed_to,
            was_generated_by=was_generated_by,
        )
    finally:
        _close_if_possible(store)
    project.log_decision(
        "save_storyboard",
        panel_count=len(storyboard.panels),
        title=storyboard.title,
    )


# ---------------------------------------------------------------------------
# Build a Storyboard from a project's shots
# ---------------------------------------------------------------------------


def storyboard_from_shots(
    project: Project,
    *,
    title: Optional[str] = None,
    style: Optional[str] = None,
) -> tuple[Storyboard, dict[str, TimeInterval]]:
    """Build a one-panel-per-shot draft Storyboard from a project's shots.

    Each panel's caption defaults to the shot's description, framing and
    camera carry over, and the panel's ``shot_id`` points back at the shot.
    No images are attached yet — use :func:`plan_render_panel_images` to
    generate them.

    Returns ``(storyboard, panel_intervals)`` so the caller can feed both
    into :func:`save_storyboard`.
    """
    spec = project.read_spec()
    asset_id = project_asset_id(project)

    panel_specs: list[tuple[str, float, float]] = []
    panels: list[PanelBody] = []
    for shot in spec.shots:
        pid = new_panel_id()
        panel_specs.append((pid, shot.start_s, shot.end_s))
        panels.append(
            PanelBody(
                panel_id=pid,
                shot_id=shot.id,
                images=(),
                caption=shot.description,
                framing=shot.framing,
                camera=shot.camera,
                transition_in="cut",
                notes=shot.notes,
            )
        )

    sb = Storyboard(
        title=title if title is not None else spec.title,
        asset_id=asset_id,
        panels=tuple(panels),
        style=style if style is not None else spec.global_style,
        aspect="16:9",
    )
    intervals = _panel_intervals_from_panels(panel_specs)
    return sb, intervals


# ---------------------------------------------------------------------------
# Plan + execute panel-image generation
# ---------------------------------------------------------------------------


def plan_render_panel_images(
    storyboard: Storyboard,
    *,
    quality: str = "balanced",
    image_size: str = "landscape_16_9",
    model_id: Optional[str] = None,
    only_missing: bool = True,
) -> tuple[Plan, list[str]]:
    """Build a Plan that generates a seed image for each panel that lacks one.

    Args:
        storyboard: The :class:`artful.Storyboard`.
        quality: image-gen quality tier.
        image_size: "landscape_16_9" by default; respects the storyboard's
            aspect when it can be mapped to a falaw size, otherwise uses
            this default.
        model_id: Override the image-gen model. Defaults to whatever
            ``falaw.pick_model(category="image", quality_tier=quality)``
            picks (e.g. flux/dev at balanced).
        only_missing: When True (default), skip panels that already have a
            ``role="seed"`` image. When False, plan one call per panel
            regardless.

    Returns:
        ``(plan, panel_ids)`` — the Plan, and the panel ids in the same
        order as the Plan's calls (so :func:`execute_render_panel_images`
        knows which panel each artifact belongs to).
    """
    calls = []
    panel_ids: list[str] = []

    for panel in storyboard.panels:
        if only_missing and any(img.role == "seed" for img in panel.images):
            continue
        if not panel.caption:
            # Skip panels with no prompt — the user has to fill the caption.
            continue

        prompt = panel.caption
        if storyboard.style:
            prompt = f"{prompt} | style: {storyboard.style}"
        if panel.framing:
            prompt = f"{prompt} | framing: {panel.framing}"
        if panel.camera:
            prompt = f"{prompt} | camera: {panel.camera}"

        call = plan_generate_image(
            prompt=prompt,
            quality=quality,
            image_size=_image_size_for_aspect(storyboard.aspect, image_size),
            model_id=model_id,
            metadata={
                "panel_id": panel.panel_id,
                "shot_id": panel.shot_id or "",
                "kind": "storyboard_panel_seed",
            },
        )
        calls.append(call)
        panel_ids.append(panel.panel_id)

    return Plan(calls=tuple(calls)), panel_ids


def execute_render_panel_images(
    project: Project,
    storyboard: Storyboard,
    plan: Plan,
    panel_ids: list[str],
    *,
    on_event=None,
    use_cache: bool = True,
    on_failure: OnFailure = "halt",
) -> Storyboard:
    """Execute ``plan``, download each artifact, attach a PanelImage.

    Returns a NEW :class:`Storyboard` (input ``storyboard`` is unchanged) with
    the materialized seed images attached as ``role="seed"`` PanelImages.

    Files land under ``<project_root>/storyboard/<panel_id>.png``. The
    PanelImage record stores both the project-relative path and the
    artifact_id (content hash via lacing.Artifact), so downstream consumers
    can prefer one or the other.

    ``on_failure`` is nw#25's policy, and this is the function the issue names
    as **nw's real fan-out shape** — one ``generate_image`` per panel. Under
    ``"isolate"`` a panel whose call failed is simply left without a seed image;
    every panel that rendered keeps its own, instead of one content-filtered
    panel discarding the whole batch. ``"halt"`` is the default and unchanged.

    Panels are matched to outcomes **by index into the plan**, never by position
    in a shortened artifact list — the latter attaches panel 48's image to panel
    47 the moment one call drops out.
    """
    if len(plan.calls) != len(panel_ids):
        raise ValueError(
            f"plan has {len(plan.calls)} calls but panel_ids has {len(panel_ids)} "
            "ids; pass the same panel_ids returned by plan_render_panel_images."
        )
    if on_failure not in ("halt", "isolate"):
        raise ValueError(
            f"execute_render_panel_images: on_failure must be 'halt' or "
            f"'isolate', got {on_failure!r}."
        )

    report = execute_plan_isolated(
        plan,
        on_event=on_event,
        use_cache=use_cache,
        halt_on_failure=on_failure == "halt",
    )
    if on_failure == "halt":
        report.artifacts_or_raise()
    artifacts = [o.artifact if o.ok else None for o in report.outcomes]

    storyboard_dir = project.root / "storyboard"
    storyboard_dir.mkdir(exist_ok=True)

    # Build a mutable map panel_id -> updated PanelBody.
    new_panels: dict[str, PanelBody] = {p.panel_id: p for p in storyboard.panels}

    for panel_id, artifact in zip(panel_ids, artifacts, strict=True):
        if artifact is None:
            continue  # this call failed or was blocked; the panel keeps no seed
        if not artifact.url:
            continue  # silently skip; rare but safer than failing the batch
        local = storyboard_dir / f"{panel_id}.png"
        _download_to(artifact.url, local)

        panel = new_panels[panel_id]
        # Hash the file we just wrote, rather than trusting `artifact.asset_id`.
        # Since falaw 0.0.24 that id *is* the SHA-256 of the media bytes — but
        # only when falaw could materialize them (`bytes_size > 0`); a fetch it
        # could not complete degrades to a URL-only artifact whose id is a
        # digest of the response. Hashing the local bytes is the identity of the
        # thing on disk either way, which is what a PanelImage path refers to.
        from lacing import hash_file

        true_artifact_id = hash_file(local)

        new_image = PanelImage(
            artifact_id=true_artifact_id,
            path=str(local.relative_to(project.root)),
            url=artifact.url,
            role="seed",
            caption=panel.caption[:80],
        )
        # Replace any existing seed image; keep non-seed images.
        non_seed = tuple(img for img in panel.images if img.role != "seed")
        new_panels[panel_id] = panel.model_copy(
            update={"images": non_seed + (new_image,)}
        )

    return storyboard.model_copy(
        update={"panels": tuple(new_panels[p.panel_id] for p in storyboard.panels)}
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _image_size_for_aspect(aspect: str, default: str) -> str:
    """Map "16:9" / "9:16" / "1:1" / "4:3" / "3:4" to falaw image_size names.
    Falls back to ``default`` for anything unrecognized."""
    return {
        "16:9": "landscape_16_9",
        "9:16": "portrait_16_9",
        "1:1": "square_hd",
        "4:3": "landscape_4_3",
        "3:4": "portrait_4_3",
    }.get(aspect, default)


def _download_to(url: str, dst: Path) -> Path:
    import urllib.request

    dst.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, str(dst))
    return dst


def _close_if_possible(store) -> None:
    close = getattr(store, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass
