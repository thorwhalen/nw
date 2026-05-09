"""Workflow: prepare → plan → execute, the architectural pivot.

The render pipeline has three phases, each cleanly separable:

1. **Prepare** (:func:`prepare_shot`) — *local* work: extract the audio slice
   for the shot, find character/environment anchor images, gather lyric lines,
   build the storyboard prompt. No fal calls. Output: :class:`ShotPreparation`,
   a typed bundle of local file paths and prose.

2. **Plan** (:func:`plan_render_shot`) — *pure-data* work: given a
   :class:`ShotPreparation`, build a :class:`falaw.Plan` of the fal calls
   that will produce the shot. Returns the Plan + the list of artifacts
   that haven't been generated yet (e.g. uploads needed first). Still no
   fal calls.

3. **Execute** (:func:`execute_render`) — the *only* phase with fal contact.
   Uploads local files, drives the Plan, downloads outputs, trims/pads to
   the shot's exact duration. Returns the final mp4 path.

This split is what enables:

- A budget gate that's honest (cost is computed at plan time).
- Tests that exercise plan construction without a fal account.
- A UI that says "you're about to spend $4.12, click confirm" before the
  network goes near a credit card.
- The "render then kill once audio.wav exists" hack from interface_design_plan
  (item #6) becomes one call: ``prepare_shot(project, shot_id)``.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from falaw import Plan, execute_plan
from lacing import Artifact

from .project import Project
from .renderers import get_strategy
from .schema import ShotSpec


@dataclass(frozen=True, slots=True)
class ShotPreparation:
    """Local-only inputs for rendering a single shot.

    Building a ShotPreparation is a pure-filesystem operation: no fal calls
    that bill, no network beyond fal-storage uploads (which are free). The
    upload step happens here so the resulting URLs are stable and the cache
    key derived from them is honest.

    Multiple downstream consumers (the planner, an inspection report, a UI
    preview) can read this without re-doing the audio extraction.
    """

    project_root: Path
    shot: ShotSpec
    shot_dir: Path

    audio_slice_path: Path
    """Local path to the song's audio over [shot.start_s, shot.end_s]."""

    audio_slice_url: str = ""
    """fal-storage URL of the audio slice (set by :func:`prepare_shot` when
    a fal API key is available; empty otherwise — strategies that need URLs
    will raise descriptively)."""

    character_anchor_paths: dict[str, Path] = field(default_factory=dict)
    """Per-character path to the curated anchor image."""

    character_anchor_urls: dict[str, str] = field(default_factory=dict)
    """Per-character fal-storage URL of the anchor image."""

    environment_anchor_path: Optional[Path] = None
    """Path to the environment establishing image, or None."""

    environment_anchor_url: str = ""
    """fal-storage URL of the environment image; empty if no env image."""

    lyric_lines: list[dict] = field(default_factory=list)
    """List of ``{"text", "start_s", "end_s", "line_index", "section"}`` dicts."""

    storyboard_prompt: str = ""
    """Full prose prompt: shot description + framing + camera + characters +
    environment + style + lyric lines (when present)."""

    global_style: str = ""

    @property
    def duration_s(self) -> float:
        return self.shot.duration_s

    @property
    def shot_id(self) -> str:
        return self.shot.id


# ---------------------------------------------------------------------------
# prepare_shot — local work only
# ---------------------------------------------------------------------------


def prepare_shot(
    project: Project, shot_id: str, *, upload: bool = True
) -> ShotPreparation:
    """Resolve all local inputs for rendering a shot.

    No billable fal calls. When ``upload=True`` (the default), local files
    are uploaded to fal-storage so the planner can build a Plan with stable
    URLs (uploads are free; the cache key derived from those URLs is honest).
    When ``upload=False`` (e.g. for tests or dry-run reporting), the URL
    fields are left empty.

    Idempotent in spirit but not byte-stable: fal-storage URLs include
    expiring signatures, so two ``prepare_shot`` calls on the same project
    produce different URLs. The local file paths are byte-stable.

    Args:
        project: An :class:`nw.Project` instance.
        shot_id: The shot's id, as in ``project.read_spec().shots[*].id``.
        upload: When True (default), upload local files to fal-storage and
            populate the ``*_url`` fields. When False, only the local paths
            are populated.

    Returns:
        A :class:`ShotPreparation` with local paths (and URLs if ``upload``)
        ready to plan.
    """
    spec = project.read_spec()
    shot = spec.shot(shot_id)
    if shot is None:
        raise KeyError(
            f"No shot {shot_id!r} in project {project.root.name!r}; "
            f"known: {[s.id for s in spec.shots]}"
        )

    shot_dir = project.shot_dir(shot.id)
    shot_dir.mkdir(parents=True, exist_ok=True)

    audio_slice = _ensure_audio_slice(project, shot)
    char_imgs = _resolve_character_anchors(project, shot)
    env_img = _resolve_environment_anchor(project, shot)
    lyric_lines = _lyric_lines_for_shot(project, shot)
    prompt = _build_storyboard_prompt(
        spec=spec,
        shot=shot,
        char_descriptions={
            name: spec.character(name).description if spec.character(name) else ""
            for name in shot.characters
        },
        env_description=spec.environment(shot.environment).description
        if shot.environment and spec.environment(shot.environment)
        else "",
        lyric_lines=lyric_lines,
    )

    audio_url = ""
    char_urls: dict[str, str] = {}
    env_url = ""
    if upload:
        from .renderers._common import upload_local_file

        audio_url = upload_local_file(audio_slice)
        for name, p in char_imgs.items():
            char_urls[name] = upload_local_file(p)
        if env_img is not None:
            env_url = upload_local_file(env_img)

    return ShotPreparation(
        project_root=project.root,
        shot=shot,
        shot_dir=shot_dir,
        audio_slice_path=audio_slice,
        audio_slice_url=audio_url,
        character_anchor_paths=char_imgs,
        character_anchor_urls=char_urls,
        environment_anchor_path=env_img,
        environment_anchor_url=env_url,
        lyric_lines=lyric_lines,
        storyboard_prompt=prompt,
        global_style=spec.global_style,
    )


# ---------------------------------------------------------------------------
# plan_render_shot — pure data, no fal calls
# ---------------------------------------------------------------------------


def plan_render_shot(
    prep: ShotPreparation,
    *,
    quality: str = "balanced",
    model_overrides: Optional[dict[str, str]] = None,
) -> Plan:
    """Build a :class:`falaw.Plan` for rendering a prepared shot.

    Dispatches on ``prep.shot.render_strategy`` via :mod:`nw.renderers`.

    Args:
        prep: A :class:`ShotPreparation` from :func:`prepare_shot`.
        quality: Default quality tier passed to the strategy.
        model_overrides: Optional mapping of strategy-step → model_id, e.g.
            ``{"avatar": "fal-ai/bytedance/omnihuman/v1.5"}`` to bypass the
            default avatar model. The keys understood by each strategy are
            documented on the strategy itself.

    Returns:
        A :class:`falaw.Plan`. Caller can inspect ``plan.total_cost_usd`` and
        decide whether to ``execute_plan(plan)``.
    """
    strategy = get_strategy(prep.shot.render_strategy)
    return strategy.plan(prep, quality=quality, model_overrides=model_overrides or {})


# ---------------------------------------------------------------------------
# execute_render — the only phase with fal calls
# ---------------------------------------------------------------------------


def execute_render(
    prep: ShotPreparation,
    plan: Plan,
    *,
    on_event=None,
    use_cache: bool = True,
    project: Optional[Project] = None,
) -> Path:
    """Execute a Plan, materialize the result as ``shot_dir/output.mp4``.

    Refuses to execute a plan-only Plan (one whose arguments still contain
    ``<plan-only:...>`` placeholders) — those exist so the planner can show
    cost without any uploads, and need to be replaced with real URLs (call
    :func:`prepare_shot` with ``upload=True``) before execute.

    Args:
        prep: The :class:`ShotPreparation` the Plan was built for.
        plan: A :class:`falaw.Plan` (typically from :func:`plan_render_shot`).
        on_event: Optional event subscriber forwarded to the falaw call layer.
        use_cache: When True (default), routes via ``cached_call_fal`` so
            cache hits skip the network.
        project: Optional :class:`Project`. When given, a render-decision
            annotation is appended to the project graph after execution
            with ``was_derived_from = (shot_annotation_id,)``, so reelee's
            freshness queries (``descendants_of`` / ``stale_after``) walk
            from the shot to its render output.

    Returns:
        Path to ``shot_dir/output.mp4`` (trimmed/padded to ``prep.duration_s``).
    """
    _refuse_plan_only_plan(plan)
    artifacts: list[Artifact] = execute_plan(plan, on_event=on_event, use_cache=use_cache)
    strategy = get_strategy(prep.shot.render_strategy)
    output = strategy.materialize(prep, plan, artifacts)

    if project is not None:
        _record_render_decision(project, prep, plan, artifacts, output)

    return output


def _record_render_decision(
    project: "Project",
    prep: "ShotPreparation",
    plan: "Plan",
    artifacts: list,
    output: "Path",
) -> None:
    """Write a render-decision annotation derived from the shot annotation.

    The decision's ``was_derived_from`` is the shot annotation's id, so a
    reelee freshness traversal from the shot will find the render output.
    Cost, model ids, and call ids are persisted in the decision payload so
    a later inspector view can show "what fired and what it cost."
    """
    # Find the shot's annotation id in the graph.
    shot_anns = project.graph.shots()
    matching = next((s for s in shot_anns if s.body.shot_id == prep.shot_id), None)
    if matching is None:
        return  # shouldn't happen for a real Project; bail quietly

    payload = {
        "shot_id": prep.shot_id,
        "strategy": prep.shot.render_strategy,
        "duration_s": prep.duration_s,
        "output_path": str(output.relative_to(project.root))
            if output.is_relative_to(project.root) else str(output),
        "calls": [
            {
                "tool": c.tool,
                "application": c.application,
                "estimated_cost_usd": c.estimated_cost_usd,
                "cache_status": c.cache_status,
            }
            for c in plan.calls
        ],
        "artifacts": [
            {
                "asset_id": a.asset_id,
                "kind": a.kind,
                "url": a.url,
                "duration_s": a.duration_s,
                "cost_usd": a.cost_usd,
            }
            for a in artifacts
        ],
        "total_estimated_cost_usd": plan.total_cost_usd,
    }
    from .bodies import DecisionBodyV1
    project.graph.append_decision(
        DecisionBodyV1(kind="render_shot", payload=payload),
        was_derived_from=(matching.annotation_id,),
    )


def _refuse_plan_only_plan(plan: Plan) -> None:
    """Raise if any CallPlan has a ``<plan-only:…>`` arg. The strategies use
    such placeholders when ``prepare_shot(upload=False)`` was called — they're
    fine for inspection, fatal for execution."""
    for i, call in enumerate(plan.calls):
        for k, v in _walk_strings(call.arguments):
            if isinstance(v, str) and v.startswith("<plan-only:"):
                raise RuntimeError(
                    f"execute_render: call {i} ({call.tool}) has a plan-only "
                    f"placeholder at {k!r}={v!r}. The Plan was built from a "
                    "ShotPreparation without uploads. Re-run prepare_shot with "
                    "upload=True (the default) before calling execute_render."
                )


def _walk_strings(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, prefix=f"{prefix}.{k}" if prefix else k)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, prefix=f"{prefix}[{i}]")
    else:
        yield prefix, obj


# ---------------------------------------------------------------------------
# Local helpers (no falaw / network access)
# ---------------------------------------------------------------------------


def _ensure_audio_slice(project: Project, shot: ShotSpec) -> Path:
    """Extract the song over [start_s, end_s] into ``shot_dir/audio.wav``.

    Uses ``mixing.audio.Audio`` if available; falls back to ffmpeg directly if
    mixing isn't installed.
    """
    out = project.shot_dir(shot.id) / "audio.wav"
    if out.exists():
        return out

    song = project.song_path()
    if song is None:
        raise RuntimeError(
            f"Cannot extract audio for shot {shot.id!r}: project has no song registered."
        )

    try:
        from mixing.audio import Audio  # type: ignore[import-not-found]
        seg = Audio(str(song))[shot.start_s : shot.end_s]
        seg.save(str(out))
        return out
    except ImportError:
        pass

    # Fallback: ffmpeg.
    import subprocess
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(song),
            "-ss", f"{shot.start_s:.3f}",
            "-to", f"{shot.end_s:.3f}",
            "-c:a", "pcm_s16le",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


def _resolve_character_anchors(project: Project, shot: ShotSpec) -> dict[str, Path]:
    """Pick the anchor image for each character referenced by the shot.

    Honors :meth:`Project.set_character_anchor` (reads ``card.json``'s
    ``reference_image_path``). Falls back to the first image in
    ``selected/``, then ``refs/``, if no anchor is set. Missing characters
    are skipped silently — strategies that need them will raise their own
    descriptive error.
    """
    out: dict[str, Path] = {}
    for name in shot.characters:
        char_dir = project.character_dir(name)
        if not char_dir.exists():
            continue

        # 1. Explicit anchor from card.json
        try:
            card = project.read_character_card(name)
            ref = card.get("reference_image_path") or ""
            if ref:
                p = (Path(ref) if Path(ref).is_absolute() else project.root / ref).resolve()
                if p.exists():
                    out[name] = p
                    continue
        except FileNotFoundError:
            pass

        # 2. First image in selected/, then refs/
        for sub in ("selected", "refs"):
            sub_dir = char_dir / sub
            if not sub_dir.exists():
                continue
            for image_path in sorted(sub_dir.iterdir()):
                if image_path.is_file() and image_path.suffix.lower() in {
                    ".png", ".jpg", ".jpeg", ".webp"
                }:
                    out[name] = image_path
                    break
            if name in out:
                break
    return out


def _resolve_environment_anchor(project: Project, shot: ShotSpec) -> Optional[Path]:
    """Return ``environments/<name>/establishing.png`` if present, else None."""
    if not shot.environment:
        return None
    env_dir = project.environment_dir(shot.environment)
    candidate = env_dir / "establishing.png"
    if candidate.exists():
        return candidate
    # Look for any image in the environment dir.
    if env_dir.exists():
        for p in sorted(env_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                return p
    return None


def _lyric_lines_for_shot(project: Project, shot: ShotSpec) -> list[dict]:
    """Read the lacing alignment store and return lyric lines that fall in the shot."""
    align_path = project.root / "lyrics" / "alignment.annot"
    if not align_path.exists():
        return []
    try:
        from lacing import SqliteStore
        from lacing.tracks.subtitle import SubtitleTrack
    except Exception:
        return []
    store = SqliteStore(str(align_path))
    try:
        track = SubtitleTrack(store, asset_id=None)
        return [
            {
                "text": ann.body.get("text", ""),
                "start_s": ann.reference.interval.start.to_seconds(),
                "end_s": ann.reference.interval.end.to_seconds(),
                "line_index": ann.body.get("line_index"),
                "section": ann.body.get("section"),
            }
            for ann in track.lines_in(shot.start_s, shot.end_s)
        ]
    finally:
        store.close()


def _build_storyboard_prompt(
    *,
    spec,
    shot: ShotSpec,
    char_descriptions: dict[str, str],
    env_description: str,
    lyric_lines: list[dict],
) -> str:
    parts: list[str] = []
    if shot.description:
        parts.append(shot.description)
    if shot.framing:
        parts.append(f"framing: {shot.framing}")
    if shot.camera:
        parts.append(f"camera: {shot.camera}")
    for name, desc in char_descriptions.items():
        if desc:
            parts.append(f"{name}: {desc}")
    if env_description:
        parts.append(f"location: {env_description}")
    if spec.global_style:
        parts.append(f"style: {spec.global_style}")
    if lyric_lines:
        parts.append(
            "lyrics: " + " / ".join(L["text"] for L in lyric_lines if L.get("text"))
        )
    return " | ".join(parts)
