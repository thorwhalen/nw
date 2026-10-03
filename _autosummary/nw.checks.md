# nw.checks

The checks nw itself ships — a small, honest default menu.

These are the three that need nothing nw does not already shell out to, and
they are here because each of them has already caught a real defect in a
finished film:

* **a missing stream** — a render that produced a video track and no audio, or
  an audio track and no video, and returned a path either way;
* **an encode that stopped early** — a ten-minute cut killed by the OOM killer
  two-thirds of the way through, whose container still reported the full
  duration, because the container takes its duration from the *audio* stream.
  Every duration check passed. Counting frames is what catches it;
* **a long freeze** — a model returning a too-short clip and a `tpad`
  fallback holding the last frame for four seconds, which looks exactly like a
  deliberate hold until you measure it.

They are registered at import of [`nw`](nw.md#module-nw), so they are on the menu. They are
not *run* by anything: see [`nw.validation`](nw.validation.md#module-nw.validation) on why validation is placed by
a caller and never assumed.

Every check here is built on [`nw.inspect`](nw.inspect.md#module-nw.inspect), which is the older, direct form
of the same knowledge. That module stays: a caller who wants one typed report
about one shot should keep calling `shot_report`. These wrap it for callers
who want a *selection* of checks scheduled and reported together.

### Functions

| [`as_path`](#nw.checks.as_path)(target)           | The media file a target refers to.   |
|----------------------------------------------------------------------------|--------------------------------------|
| [`register_builtin_checks`](#nw.checks.register_builtin_checks)() | Put nw's own checks on the menu.     |

### nw.checks.as_path(target)

The media file a target refers to.

Checks are handed whatever the caller validates. These built-ins want a
file, so they accept a path, a string, or anything with a `path` or
`output_path` attribute — and say so plainly when they get none, rather
than reporting a clean bill of health on something they never opened.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### nw.checks.register_builtin_checks()

Put nw’s own checks on the menu. Idempotent.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)
