# nw.validation

Pluggable validation of finished work — the seam, not the checks.

**What this is for.** A render that finishes without raising is not a render
that is *right*. A one-minute short shipped with a burnt-in lower third reading
`1981 · Centr…`; a ten-minute cut shipped two-thirds encoded with a container
duration taken from its audio stream, so every duration check passed; a Ken
Burns move jumped a whole pixel mid-panel for months. Each was found by a human
watching the finished file, which is the most expensive detector we have and the
only one that gets tired.

**What this module owns, and what it deliberately does not.** It owns the
*seam*: how a check is declared, how the menu of available checks is assembled,
how dependencies between them are resolved, in what order and with how much
concurrency they run, and what a run of them reports. It owns no domain
knowledge whatsoever — every actual check is a plugin, registered from wherever
the knowledge lives (nw ships the two that [`nw.inspect`](nw.inspect.html.md#module-nw.inspect) already knew how to
do; a check about on-screen type belongs next to `tituli`, one about camera
motion next to `burns`).

**Where it goes in a pipeline.** Nowhere, by default. Validation is a thing you
*place*, and where to place it is a judgement about cost and consequence:

* before showing a result to a human, if the checks are cheap;
* before an irreversible or outward-facing step — a publish, an upload, a send —
  where the checks earn their cost whatever they cost;
* both, with a cheaper selection at the first point than the second.

So the surface is one function, [`validate()`](#nw.validation.validate), and callers place it. Nothing
in nw calls it for you; a gate you did not ask for is a gate that fires at the
wrong moment.

**Declaring a check**:

```default
from nw.validation import Check, checks

@checks.register_decorator("video.has_both_streams")
def _has_both_streams():
    return Check(
        name="video.has_both_streams",
        summary="the file actually contains a video and an audio stream",
        run=lambda target, ctx: (...),
        example_requests=("is the video ok", "did the render work"),
    )
```

`example_requests` is not decoration. A menu of forty checks is unusable by a
human and unselectable by a model; the phrases a person actually says are what
lets [`suggest()`](#nw.validation.suggest) turn “make sure the captions look right” into a selection,
which is how this reaches an MCP tool surface without a forty-item enum.

### Examples

```pycon
>>> report = validate("some.mp4", checks=())   # nothing selected, nothing run
>>> report.ok, list(report.findings)
(True, [])
```

### Module Attributes

| [`checks`](#nw.validation.checks)   | The menu.   |
|-----------------------------------------------------------|-------------|

### Functions

| [`register_check`](#nw.validation.register_check)([check])                          | Add a check to the menu, as a call or as a decorator.                   |
|---------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------|
| [`resolve_checks`](#nw.validation.resolve_checks)(selection)                        | Selection plus everything it requires, with names resolved to Checks.   |
| [`plan_checks`](#nw.validation.plan_checks)(selection)                           | Order the selection into waves that may each run concurrently.          |
| [`validate`](#nw.validation.validate)(target, \*[, checks, max_workers, ...]) | Run `checks` against `target` and report.                               |
| [`suggest`](#nw.validation.suggest)(request, \*[, include_paid])             | Checks whose `example_requests` look like what the user just asked for. |
| [`menu`](#nw.validation.menu)(\*[, cost])                                 | Every registered check, name-ordered — what a user chooses from.        |

### Classes

| [`Finding`](#nw.validation.Finding)(check, severity, message[, where, ...])   | One thing a check noticed.                                                             |
|----------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------|
| [`CheckResult`](#nw.validation.CheckResult)(name[, findings, skipped, ...])       | What one check produced, including the case where it could not run.                    |
| [`ValidationReport`](#nw.validation.ValidationReport)(target[, results, elapsed_s])    | Everything a [`validate()`](#nw.validation.validate) run produced. |
| [`Check`](#nw.validation.Check)(name, summary, run[, requires, ...])        | One validation, and everything a scheduler and a menu need to know.                    |

### Exceptions

| [`ValidationError`](#nw.validation.ValidationError)(report)   | Raised by [`ValidationReport.raise_if_failed()`](#nw.validation.ValidationReport.raise_if_failed).   |
|----------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------|

### *class* nw.validation.Check(name, summary, run, requires=(), parallel_safe=True, cost='cheap', example_requests=(), requires_binaries=())

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One validation, and everything a scheduler and a menu need to know.

#### name

dotted and stable — it is what a user selects and what a
`requires` refers to.

#### summary

one line, for the menu.

#### run

`(target, context) -> findings`. May also return a
`(findings, produced)` pair when other checks depend on it.

#### requires

names of checks that must run first, whose `produced`
values arrive in `context`. Cycles raise at plan time.

#### parallel_safe

whether it may run alongside its independent peers.
`False` for anything that is not thread-safe or that saturates
the machine on its own (a full decode).

#### cost

rough wall-clock class — `"free"` (no subprocess),
`"cheap"` (seconds), `"dear"` (a full pass over the media), or
`"paid"` (spends money, e.g. a hosted OCR or transcription).
`"paid"` is never selected by [`suggest()`](#nw.validation.suggest); it must be asked
for by name.

#### example_requests

things a person actually says that mean they want
this check. What lets a menu of forty be navigated, and what an
MCP surface matches against instead of exposing an enum.

#### requires_binaries

external programs it shells out to. A missing one
makes the check *skip with a reason*, never silently pass.

### *class* nw.validation.CheckResult(name, findings=(), skipped='', error='', elapsed_s=0.0, produced=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

What one check produced, including the case where it could not run.

`skipped` and `error` are kept distinct from “found nothing”, because
conflating them is how a validation suite comes to report all-clear on a
machine where half of it never ran. A missing binary is not a pass.

#### *property* ok *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

Ran, and found nothing at or above `FAILING_SEVERITY`.

### *class* nw.validation.Finding(check, severity, message, where='', remedy=None, evidence=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One thing a check noticed.

A finding is never a bare boolean. Whoever reads it — a human deciding
whether to publish, or a model deciding what to fix — needs to know *where*
in the work it is and *what* would make it go away, and a check that cannot
say those two things has not finished its job.

#### check

the name of the check that produced it.

#### severity

`"info"`, `"warn"` or `"error"`; only `"error"`
makes a report not `ok`.

#### message

what is wrong, in one sentence a human can act on.

#### where

where in the work — a timestamp, a frame index, a shot id, a
path. Free-form because the checks are, but never empty for
anything above `"info"`.

#### remedy

what would fix it, when the check knows. `None` when it
honestly does not.

#### evidence

anything a reader would want to look at — an extracted
frame’s path, the numbers behind the verdict.

### *exception* nw.validation.ValidationError(report)

Bases: [`AssertionError`](https://docs.python.org/3/builtins/exceptions.html#AssertionError)

Raised by [`ValidationReport.raise_if_failed()`](#nw.validation.ValidationReport.raise_if_failed).

### *class* nw.validation.ValidationReport(target, results=(), elapsed_s=0.0)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Everything a [`validate()`](#nw.validation.validate) run produced.

#### target

what was validated.

#### results

one per check that was selected, in the order they were run.

#### elapsed_s

wall-clock for the whole run.

#### *property* ok *: [bool](https://docs.python.org/3/builtins/functions.html#bool)*

No failing findings **and** nothing that failed to run.

A check that errored is not a pass. Callers gating a publish on this
get the conservative answer without having to remember to ask for it.

#### raise_if_failed()

Return self, or raise [`ValidationError`](#nw.validation.ValidationError) — for a hard gate.

* **Return type:**
  [`ValidationReport`](#nw.validation.ValidationReport)

#### summary()

A few lines a human can read without unpacking the object.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### nw.validation.checks *: Registry* *= <Registry nw.validation.checks>*

The menu. `on_conflict="error"` so a plugin that shadows a built-in fails
loudly rather than quietly changing what “validated” means.

### nw.validation.menu(, cost=None)

Every registered check, name-ordered — what a user chooses from.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Check`](#nw.validation.Check), [`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis)]

### nw.validation.plan_checks(selection)

Order the selection into waves that may each run concurrently.

Every check in a wave has all its requirements satisfied by earlier waves,
so the waves are the schedule: run each in turn, in parallel within it.
A check that is not `parallel_safe` gets a wave to itself.

* **Raises:**
  [**ValueError**](https://docs.python.org/3/builtins/exceptions.html#ValueError) – on a dependency cycle, or a requirement that is not
      registered — both at plan time, before anything has been spent.
* **Return type:**
  [*tuple*](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[*tuple*](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[*Check*](#nw.validation.Check), …], …]

### Examples

```pycon
>>> plan_checks(())
()
```

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Check`](#nw.validation.Check), [`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis)], [`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis)]

### nw.validation.register_check(check=None, \*\*kwargs)

Add a check to the menu, as a call or as a decorator.

As a call:

```default
register_check(Check(name="video.duration", summary="...", run=...))
```

As a decorator on the run function, with the rest as keywords:

```default
@register_check(name="video.duration", summary="...", cost="cheap")
def _duration(target, ctx): ...
```

### nw.validation.resolve_checks(selection)

Selection plus everything it requires, with names resolved to Checks.

A user picks what they care about; what those checks *need* is not their
problem. Raises `KeyError` for an unknown name — a silently dropped check
is the failure this whole module exists to prevent.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Check`](#nw.validation.Check), [`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis)]

### nw.validation.suggest(request, , include_paid=False)

Checks whose `example_requests` look like what the user just asked for.

Deliberately crude — a word-overlap score, not a model call — because this
runs on every request and its job is to narrow forty items to a handful
that a human or a model then confirms. `"paid"` checks are never
suggested: money is asked for by name.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Check`](#nw.validation.Check), [`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis)]

### Examples

```pycon
>>> suggest("")
()
```

### nw.validation.validate(target, , checks=(), max_workers=4, on_error='report')

Run `checks` against `target` and report.

* **Parameters:**
  * **target** ([`Any`](https://docs.python.org/3/library/typing.html#typing.Any)) – whatever the checks understand — a path to a rendered file, a
    `Project`, a `(video, annotations)` pair. This module does not
    care; it is the checks that agree with their caller.
  * **checks** ([`Iterable`](https://docs.python.org/3/library/typing.html#typing.Iterable)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`Check`](#nw.validation.Check)]) – names or [`Check`](#nw.validation.Check) objects. Requirements are pulled in
    automatically. Empty means empty: validation is placed, never
    assumed.
  * **max_workers** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – concurrency within a wave.
  * **on_error** ([`Literal`](https://docs.python.org/3/library/typing.html#typing.Literal)[`'report'`, `'raise'`]) – `"report"` records a raising check as an errored
    [`CheckResult`](#nw.validation.CheckResult) and carries on, so one broken plugin cannot
    hide the findings of the other nine. `"raise"` is for developing
    a check.
* **Return type:**
  [`ValidationReport`](#nw.validation.ValidationReport)
* **Returns:**
  A [`ValidationReport`](#nw.validation.ValidationReport). Note that `report.ok` is `False` when
  a check *errored*, not only when one failed: a suite that could not run
  has not said the work is good.

### Examples

```pycon
>>> validate("x.mp4").ok
True
```
