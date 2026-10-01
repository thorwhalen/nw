# nw.experiment

Experiment helpers — clone projects, apply operations across siblings.

Replaces the bash glue from the muvid_project run:

- `cp -r the_bells the_bells_v1_lipsync; cp -r the_bells the_bells_v2_…`
  becomes [`clone_project()`](#nw.experiment.clone_project) calls in a Python loop, with typed
  control over what’s preserved vs. reset.
- `for v in v1 v2 v3 v4; do muvid script-apply …; done` becomes
  [`apply_to_projects()`](#nw.experiment.apply_to_projects).

The “compare four interpretations” workflow is now a first-class feature
rather than a shell pipeline.

### Functions

| [`apply_to_projects`](#nw.experiment.apply_to_projects)(roots, fn, \*[, parallel])   | Apply `fn` to each project at `roots` and collect the results.   |
|-------------------------------------------------------------------------------------------------|------------------------------------------------------------------|
| [`clone_project`](#nw.experiment.clone_project)(src_root, dst_root, \*[, ...])   | Clone an nw project to a new root.                               |
| [`summarize_all`](#nw.experiment.summarize_all)(roots)                           | Convenience: return a `ProjectSummary` for each project.         |

### nw.experiment.apply_to_projects(roots, fn, , parallel=False)

Apply `fn` to each project at `roots` and collect the results.

* **Parameters:**
  * **roots** (`Iterable`[`str` | `Path`]) – Iterable of project roots. Each must point to an existing
    nw project.
  * **fn** (`Callable`[[[`Project`](nw.project.md#nw.project.Project)], `TypeVar`(`T`)]) – Callable taking a `Project` and returning anything. Use this
    for per-project operations: parsing a script, estimating cost,
    rendering, gathering reports.
  * **parallel** (`bool`) – When True, run `fn` in a thread pool. Useful when `fn`
    is I/O- or API-bound (e.g. a render). When False (default), runs
    sequentially in submission order — the safest semantics.
* **Return type:**
  `list`[`TypeVar`(`T`)]
* **Returns:**
  A list of `fn(project)` results in the same order as `roots`.

### Examples

```pycon
>>> # Estimate cost of all four sibling experiments without rendering:
>>> # totals = apply_to_projects(roots, lambda p: estimate_render_cost(p))
>>> # Apply the same script to all of them after a refactor:
>>> # apply_to_projects(roots, lambda p: parse_script(p))
```

### nw.experiment.clone_project(src_root, dst_root, , preserve=('song', 'lyrics', 'characters'), reset=('script', 'shots', 'output', '.nw'), title=None, force=False)

Clone an nw project to a new root.

* **Parameters:**
  * **src_root** (`str` | `Path`) – Path to an existing nw project (must contain `project.json`).
  * **dst_root** (`str` | `Path`) – Destination path. Must not exist (or pass `force=True` to
    overwrite).
  * **preserve** (`Iterable`[`str`]) – Subtrees of `src_root` to copy verbatim into `dst_root`.
    Default: `("song", "lyrics", "characters")`.
  * **reset** (`Iterable`[`str`]) – Subtrees of `dst_root` to (re)create as empty after copying.
    Default: `("script", "shots", "output", ".nw")`.
  * **title** (`Optional`[`str`]) – New title for the cloned project. Defaults to `dst_root`’s
    folder name.
  * **force** (`bool`) – When True, overwrite an existing `dst_root` (refuses by default
    to avoid clobbering work).
* **Return type:**
  [`ProjectSummary`](nw.schema.md#nw.schema.ProjectSummary)
* **Returns:**
  `ProjectSummary` of the cloned project.

### nw.experiment.summarize_all(roots)

Convenience: return a `ProjectSummary` for each project.

Equivalent to `apply_to_projects(roots, lambda p: p.read_summary())`.

* **Return type:**
  `list`[[`ProjectSummary`](nw.schema.md#nw.schema.ProjectSummary)]
