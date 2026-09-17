# nw.inspect

QA helpers — typed reports about rendered shots.

A render finishing without an exception doesn’t mean it’s *right*. The v3
fixture in muvid_project rendered successfully but produced a 5.87s clip
when 8s were asked for. The four-second freeze in v4 was visible only by
extracting frames and md5’ing them. These reports surface those defects
without a manual ffprobe / ffmpeg dance.

Public surface:

- [`shot_report()`](#nw.inspect.shot_report) — duration, frozen-frame segments, audio-video offset,
  whether the output is the requested length.
- [`compose_report()`](#nw.inspect.compose_report) — same but for the final composed video; plus
  per-shot rollup, gaps between shots, freeze alerts.

Both return frozen Pydantic models for typed downstream use.

### Functions

| [`compose_report`](#nw.inspect.compose_report)(project, \*[, ...])       | Per-shot reports + final-compose inspection in one call.        |
|-------------------------------------------------------------------------------------------|-----------------------------------------------------------------|
| [`shot_report`](#nw.inspect.shot_report)(project, shot_id, \*[, ...]) | Inspect `shots/<shot_id>/output.mp4` and return a typed report. |

### Classes

| [`ComposeReport`](#nw.inspect.ComposeReport)(\*\*data)   | Inspection of the project-level final composed video.   |
|----------------------------------------------------------------------------|---------------------------------------------------------|
| [`FrozenSegment`](#nw.inspect.FrozenSegment)(\*\*data)   | A run of consecutive frames whose pixels don't change.  |
| [`Gap`](#nw.inspect.Gap)(\*\*data)             | A gap on the timeline between two shots.                |
| [`ShotReport`](#nw.inspect.ShotReport)(\*\*data)      | Inspection of one rendered shot.                        |

### *class* nw.inspect.ComposeReport(\*\*data)

Bases: `BaseModel`

Inspection of the project-level final composed video.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.inspect.FrozenSegment(\*\*data)

Bases: `BaseModel`

A run of consecutive frames whose pixels don’t change.

A short freeze (≤ 0.25s) is usually a model artifact; a long one (≥ 1s)
is almost always a bug — Hailuo Pro returning a too-short clip + a tpad
fallback that froze the last frame, etc.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.inspect.Gap(\*\*data)

Bases: `BaseModel`

A gap on the timeline between two shots.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### *class* nw.inspect.ShotReport(\*\*data)

Bases: `BaseModel`

Inspection of one rendered shot.

#### *property* has_long_freeze *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

Any freeze ≥ 1.0s is suspicious. Anything ≥ 0.5s is worth flagging.

#### model_config *: [ClassVar](https://docs.python.org/3/library/typing.html#typing.ClassVar)[ConfigDict]* *= {'frozen': True}*

Configuration for the model, should be a dictionary conforming to [`ConfigDict`][pydantic.config.ConfigDict].

### nw.inspect.compose_report(project, , freeze_sample_fps=4.0, duration_tolerance_s=0.1)

Per-shot reports + final-compose inspection in one call.

* **Return type:**
  [`ComposeReport`](#nw.inspect.ComposeReport)

### nw.inspect.shot_report(project, shot_id, , freeze_sample_fps=4.0, duration_tolerance_s=0.1)

Inspect `shots/<shot_id>/output.mp4` and return a typed report.

* **Parameters:**
  * **project** ([`Project`](nw.project.html.md#nw.project.Project)) – The [`nw.Project`](nw.html.md#nw.Project).
  * **shot_id** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – The shot id.
  * **freeze_sample_fps** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – How many frames per second to extract for the
    freeze detector (default 4 fps; a freeze must hold across at
    least two consecutive samples to count).
  * **duration_tolerance_s** ([`float`](https://docs.python.org/3/builtins/functions.html#float)) – Acceptable difference between actual and
    target duration before flagging.
* **Return type:**
  [`ShotReport`](#nw.inspect.ShotReport)
* **Returns:**
  A [`ShotReport`](#nw.inspect.ShotReport).
