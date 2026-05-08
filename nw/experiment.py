"""Experiment helpers — clone projects, apply operations across siblings.

Replaces the bash glue from the muvid_project run:

- ``cp -r the_bells the_bells_v1_lipsync; cp -r the_bells the_bells_v2_…``
  becomes :func:`clone_project` calls in a Python loop, with typed
  control over what's preserved vs. reset.
- ``for v in v1 v2 v3 v4; do muvid script-apply …; done`` becomes
  :func:`apply_to_projects`.

The "compare four interpretations" workflow is now a first-class feature
rather than a shell pipeline.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Iterable, Optional, TypeVar

from .project import Project
from .schema import ProjectSummary


T = TypeVar("T")


# Subtrees that make sense to preserve when cloning — copying these from src
# carries forward shared inputs (the song, the alignment, the casting).
_DEFAULT_PRESERVE: tuple[str, ...] = ("song", "lyrics", "characters")

# Subtrees that make sense to reset (recreate empty) when cloning — these are
# per-experiment outputs that must NOT carry over.
_DEFAULT_RESET: tuple[str, ...] = ("script", "shots", "output", ".nw")


def clone_project(
    src_root: str | Path,
    dst_root: str | Path,
    *,
    preserve: Iterable[str] = _DEFAULT_PRESERVE,
    reset: Iterable[str] = _DEFAULT_RESET,
    title: Optional[str] = None,
    force: bool = False,
) -> ProjectSummary:
    """Clone an nw project to a new root.

    Args:
        src_root: Path to an existing nw project (must contain ``project.json``).
        dst_root: Destination path. Must not exist (or pass ``force=True`` to
            overwrite).
        preserve: Subtrees of ``src_root`` to copy verbatim into ``dst_root``.
            Default: ``("song", "lyrics", "characters")``.
        reset: Subtrees of ``dst_root`` to (re)create as empty after copying.
            Default: ``("script", "shots", "output", ".nw")``.
        title: New title for the cloned project. Defaults to ``dst_root``'s
            folder name.
        force: When True, overwrite an existing ``dst_root`` (refuses by default
            to avoid clobbering work).

    Returns:
        :class:`ProjectSummary` of the cloned project.
    """
    src = Path(src_root).resolve()
    dst = Path(dst_root).resolve()

    if not (src / "project.json").exists():
        raise FileNotFoundError(
            f"{src} is not an nw project (no project.json)."
        )

    if dst.exists():
        if not force:
            raise FileExistsError(
                f"{dst} already exists. Pass force=True to overwrite."
            )
        shutil.rmtree(dst)

    dst.mkdir(parents=True)

    # Copy project.json — always preserved (it's the SSOT pointer).
    shutil.copy2(src / "project.json", dst / "project.json")

    # Copy each preserve subtree, when it exists in src.
    preserve_set = set(preserve)
    for name in preserve_set:
        src_sub = src / name
        if src_sub.exists() and src_sub.is_dir():
            shutil.copytree(src_sub, dst / name)
        elif src_sub.exists() and src_sub.is_file():
            shutil.copy2(src_sub, dst / name)

    # Reset each "reset" subtree to an empty directory.
    for name in reset:
        target = dst / name
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        target.mkdir()

    # Update title if requested (or fall back to the dst folder name).
    proj = Project(dst)
    proj.set_title(title if title is not None else dst.name)
    proj.log_decision(
        "clone_project",
        src=str(src),
        preserve=sorted(preserve_set),
        reset=sorted(reset),
    )
    return proj.read_summary()


def apply_to_projects(
    roots: Iterable[str | Path],
    fn: Callable[[Project], T],
    *,
    parallel: bool = False,
) -> list[T]:
    """Apply ``fn`` to each project at ``roots`` and collect the results.

    Args:
        roots: Iterable of project roots. Each must point to an existing
            nw project.
        fn: Callable taking a :class:`Project` and returning anything. Use this
            for per-project operations: parsing a script, estimating cost,
            rendering, gathering reports.
        parallel: When True, run ``fn`` in a thread pool. Useful when ``fn``
            is I/O- or API-bound (e.g. a render). When False (default), runs
            sequentially in submission order — the safest semantics.

    Returns:
        A list of ``fn(project)`` results in the same order as ``roots``.

    Examples:
        >>> # Estimate cost of all four sibling experiments without rendering:
        >>> # totals = apply_to_projects(roots, lambda p: estimate_render_cost(p))
        >>> # Apply the same script to all of them after a refactor:
        >>> # apply_to_projects(roots, lambda p: parse_script(p))
    """
    projects = [Project(r) for r in roots]
    if not parallel:
        return [fn(p) for p in projects]

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(projects)))) as exe:
        futures = [exe.submit(fn, p) for p in projects]
        return [f.result() for f in futures]


def summarize_all(roots: Iterable[str | Path]) -> list[ProjectSummary]:
    """Convenience: return a :class:`ProjectSummary` for each project.

    Equivalent to ``apply_to_projects(roots, lambda p: p.read_summary())``.
    """
    return apply_to_projects(roots, lambda p: p.read_summary())
