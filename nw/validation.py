"""Pluggable validation of finished work — the seam, not the checks.

**What this is for.** A render that finishes without raising is not a render
that is *right*. A one-minute short shipped with a burnt-in lower third reading
``1981 · Centr…``; a ten-minute cut shipped two-thirds encoded with a container
duration taken from its audio stream, so every duration check passed; a Ken
Burns move jumped a whole pixel mid-panel for months. Each was found by a human
watching the finished file, which is the most expensive detector we have and the
only one that gets tired.

**What this module owns, and what it deliberately does not.** It owns the
*seam*: how a check is declared, how the menu of available checks is assembled,
how dependencies between them are resolved, in what order and with how much
concurrency they run, and what a run of them reports. It owns no domain
knowledge whatsoever — every actual check is a plugin, registered from wherever
the knowledge lives (nw ships the two that :mod:`nw.inspect` already knew how to
do; a check about on-screen type belongs next to ``tituli``, one about camera
motion next to ``burns``).

**Where it goes in a pipeline.** Nowhere, by default. Validation is a thing you
*place*, and where to place it is a judgement about cost and consequence:

* before showing a result to a human, if the checks are cheap;
* before an irreversible or outward-facing step — a publish, an upload, a send —
  where the checks earn their cost whatever they cost;
* both, with a cheaper selection at the first point than the second.

So the surface is one function, :func:`validate`, and callers place it. Nothing
in nw calls it for you; a gate you did not ask for is a gate that fires at the
wrong moment.

**Declaring a check**::

    from nw.validation import Check, checks

    @checks.register_decorator("video.has_both_streams")
    def _has_both_streams():
        return Check(
            name="video.has_both_streams",
            summary="the file actually contains a video and an audio stream",
            run=lambda target, ctx: (...),
            example_requests=("is the video ok", "did the render work"),
        )

``example_requests`` is not decoration. A menu of forty checks is unusable by a
human and unselectable by a model; the phrases a person actually says are what
lets :func:`suggest` turn "make sure the captions look right" into a selection,
which is how this reaches an MCP tool surface without a forty-item enum.

Examples:
    >>> report = validate("some.mp4", checks=())   # nothing selected, nothing run
    >>> report.ok, list(report.findings)
    (True, [])
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence

from xdol import Registry

__all__ = [
    "Severity",
    "Finding",
    "CheckResult",
    "ValidationReport",
    "ValidationError",
    "Check",
    "checks",
    "register_check",
    "resolve_checks",
    "plan_checks",
    "validate",
    "suggest",
    "menu",
]


Severity = Literal["info", "warn", "error"]

#: Findings at or above this severity make a report not ``ok``.
FAILING_SEVERITY: Severity = "error"

_ORDER: dict[str, int] = {"info": 0, "warn": 1, "error": 2}


# ---------------------------------------------------------------------------
# What a check says
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One thing a check noticed.

    A finding is never a bare boolean. Whoever reads it — a human deciding
    whether to publish, or a model deciding what to fix — needs to know *where*
    in the work it is and *what* would make it go away, and a check that cannot
    say those two things has not finished its job.

    Attributes:
        check: the name of the check that produced it.
        severity: ``"info"``, ``"warn"`` or ``"error"``; only ``"error"``
            makes a report not ``ok``.
        message: what is wrong, in one sentence a human can act on.
        where: where in the work — a timestamp, a frame index, a shot id, a
            path. Free-form because the checks are, but never empty for
            anything above ``"info"``.
        remedy: what would fix it, when the check knows. ``None`` when it
            honestly does not.
        evidence: anything a reader would want to look at — an extracted
            frame's path, the numbers behind the verdict.
    """

    check: str
    severity: Severity
    message: str
    where: str = ""
    remedy: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in _ORDER:
            raise ValueError(
                f"unknown severity {self.severity!r}; use one of {sorted(_ORDER)}"
            )


@dataclass(frozen=True)
class CheckResult:
    """What one check produced, including the case where it could not run.

    ``skipped`` and ``error`` are kept distinct from "found nothing", because
    conflating them is how a validation suite comes to report all-clear on a
    machine where half of it never ran. A missing binary is not a pass.
    """

    name: str
    findings: tuple[Finding, ...] = ()
    skipped: str = ""  # why, when it did not run at all
    error: str = ""  # why, when it raised
    elapsed_s: float = 0.0
    produced: Any = None  # for checks that other checks depend on

    @property
    def ran(self) -> bool:
        return not self.skipped and not self.error

    @property
    def ok(self) -> bool:
        """Ran, and found nothing at or above :data:`FAILING_SEVERITY`."""
        return self.ran and not any(
            _ORDER[f.severity] >= _ORDER[FAILING_SEVERITY] for f in self.findings
        )


@dataclass(frozen=True)
class ValidationReport:
    """Everything a :func:`validate` run produced.

    Attributes:
        target: what was validated.
        results: one per check that was selected, in the order they were run.
        elapsed_s: wall-clock for the whole run.
    """

    target: Any
    results: tuple[CheckResult, ...] = ()
    elapsed_s: float = 0.0

    @property
    def findings(self) -> tuple[Finding, ...]:
        return tuple(f for r in self.results for f in r.findings)

    @property
    def ok(self) -> bool:
        """No failing findings **and** nothing that failed to run.

        A check that errored is not a pass. Callers gating a publish on this
        get the conservative answer without having to remember to ask for it.
        """
        return all(r.ok or r.skipped for r in self.results) and not any(
            r.error for r in self.results
        )

    def by_severity(self, severity: Severity) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == severity)

    def raise_if_failed(self) -> "ValidationReport":
        """Return self, or raise :class:`ValidationError` — for a hard gate."""
        if not self.ok:
            raise ValidationError(self)
        return self

    def summary(self) -> str:
        """A few lines a human can read without unpacking the object."""
        lines = [
            f"{'PASS' if self.ok else 'FAIL'}  {self.target}  "
            f"({len(self.results)} checks, {self.elapsed_s:.1f}s)"
        ]
        for r in self.results:
            if r.skipped:
                lines.append(f"  - {r.name}: skipped — {r.skipped}")
            elif r.error:
                lines.append(f"  ! {r.name}: ERROR — {r.error}")
            else:
                for f in r.findings:
                    at = f" @ {f.where}" if f.where else ""
                    fix = f"  → {f.remedy}" if f.remedy else ""
                    lines.append(f"  {f.severity:5} {f.message}{at}{fix}")
        return "\n".join(lines)


class ValidationError(AssertionError):
    """Raised by :meth:`ValidationReport.raise_if_failed`."""

    def __init__(self, report: ValidationReport):
        self.report = report
        super().__init__(report.summary())


# ---------------------------------------------------------------------------
# What a check is
# ---------------------------------------------------------------------------

#: A check's callable: ``(target, context) -> findings``. ``context`` maps the
#: names of the checks this one ``requires`` to their ``CheckResult.produced``,
#: so an expensive shared computation (sampling frames, probing a container) is
#: done once by a check that others depend on.
RunCheck = Callable[[Any, Mapping[str, Any]], Iterable[Finding]]


@dataclass(frozen=True)
class Check:
    """One validation, and everything a scheduler and a menu need to know.

    Attributes:
        name: dotted and stable — it is what a user selects and what a
            ``requires`` refers to.
        summary: one line, for the menu.
        run: ``(target, context) -> findings``. May also return a
            ``(findings, produced)`` pair when other checks depend on it.
        requires: names of checks that must run first, whose ``produced``
            values arrive in ``context``. Cycles raise at plan time.
        parallel_safe: whether it may run alongside its independent peers.
            ``False`` for anything that is not thread-safe or that saturates
            the machine on its own (a full decode).
        cost: rough wall-clock class — ``"free"`` (no subprocess),
            ``"cheap"`` (seconds), ``"dear"`` (a full pass over the media), or
            ``"paid"`` (spends money, e.g. a hosted OCR or transcription).
            ``"paid"`` is never selected by :func:`suggest`; it must be asked
            for by name.
        example_requests: things a person actually says that mean they want
            this check. What lets a menu of forty be navigated, and what an
            MCP surface matches against instead of exposing an enum.
        requires_binaries: external programs it shells out to. A missing one
            makes the check *skip with a reason*, never silently pass.
    """

    name: str
    summary: str
    run: RunCheck
    requires: tuple[str, ...] = ()
    parallel_safe: bool = True
    cost: Literal["free", "cheap", "dear", "paid"] = "cheap"
    example_requests: tuple[str, ...] = ()
    requires_binaries: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a check needs a name — it is what users select")
        if not self.summary:
            raise ValueError(
                f"check {self.name!r} needs a summary: an unexplained item on a "
                f"menu will not be chosen, and cannot be chosen *for* a user"
            )
        if self.name in self.requires:
            raise ValueError(f"check {self.name!r} requires itself")


#: The menu. ``on_conflict="error"`` so a plugin that shadows a built-in fails
#: loudly rather than quietly changing what "validated" means.
checks: Registry = Registry(name="nw.validation.checks", on_conflict="error")


def register_check(check: Check | None = None, **kwargs: Any):
    """Add a check to the menu, as a call or as a decorator.

    As a call::

        register_check(Check(name="video.duration", summary="...", run=...))

    As a decorator on the run function, with the rest as keywords::

        @register_check(name="video.duration", summary="...", cost="cheap")
        def _duration(target, ctx): ...
    """
    if check is not None:
        if kwargs:
            raise TypeError("pass a Check, or keywords — not both")
        checks.register(check.name, check)
        return check

    def deco(fn: RunCheck) -> RunCheck:
        checks.register(kwargs["name"], Check(run=fn, **kwargs))
        return fn

    return deco


# ---------------------------------------------------------------------------
# Selecting, ordering, running
# ---------------------------------------------------------------------------


def menu(*, cost: Sequence[str] | None = None) -> tuple[Check, ...]:
    """Every registered check, name-ordered — what a user chooses from."""
    out = tuple(checks[name] for name in sorted(checks))
    return tuple(c for c in out if c.cost in cost) if cost else out


def suggest(request: str, *, include_paid: bool = False) -> tuple[Check, ...]:
    """Checks whose ``example_requests`` look like what the user just asked for.

    Deliberately crude — a word-overlap score, not a model call — because this
    runs on every request and its job is to narrow forty items to a handful
    that a human or a model then confirms. ``"paid"`` checks are never
    suggested: money is asked for by name.

    Examples:
        >>> suggest("")
        ()
    """
    words = {w for w in _words(request) if len(w) > 3}
    if not words:
        return ()
    scored = []
    for c in menu():
        if c.cost == "paid" and not include_paid:
            continue
        hay = _words(" ".join((*c.example_requests, c.summary, c.name)))
        overlap = len(words & hay)
        if overlap:
            scored.append((-overlap, c.name, c))
    return tuple(c for _, _, c in sorted(scored))


def _words(text: str) -> set[str]:
    return {
        w
        for w in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split()
        if w
    }


def resolve_checks(selection: Iterable[str | Check]) -> tuple[Check, ...]:
    """Selection plus everything it requires, with names resolved to Checks.

    A user picks what they care about; what those checks *need* is not their
    problem. Raises ``KeyError`` for an unknown name — a silently dropped check
    is the failure this whole module exists to prevent.
    """
    out: dict[str, Check] = {}
    pending = list(selection)
    while pending:
        item = pending.pop()
        check = item if isinstance(item, Check) else checks[item]
        if check.name in out:
            continue
        out[check.name] = check
        pending.extend(n for n in check.requires if n not in out)
    return tuple(out.values())


def plan_checks(selection: Iterable[str | Check]) -> tuple[tuple[Check, ...], ...]:
    """Order the selection into waves that may each run concurrently.

    Every check in a wave has all its requirements satisfied by earlier waves,
    so the waves are the schedule: run each in turn, in parallel within it.
    A check that is not ``parallel_safe`` gets a wave to itself.

    Raises:
        ValueError: on a dependency cycle, or a requirement that is not
            registered — both at plan time, before anything has been spent.

    Examples:
        >>> plan_checks(())
        ()
    """
    selected = {c.name: c for c in resolve_checks(selection)}
    done: set[str] = set()
    waves: list[tuple[Check, ...]] = []
    while len(done) < len(selected):
        ready = [
            c
            for name, c in sorted(selected.items())
            if name not in done and set(c.requires) <= done
        ]
        if not ready:
            stuck = sorted(set(selected) - done)
            raise ValueError(
                f"cannot order checks {stuck}: a dependency cycle, or a "
                f"requirement outside the selection"
            )
        solo = [c for c in ready if not c.parallel_safe]
        if solo:
            waves.append((solo[0],))
            done.add(solo[0].name)
            continue
        waves.append(tuple(ready))
        done.update(c.name for c in ready)
    return tuple(waves)


def validate(
    target: Any,
    *,
    checks: Iterable[str | Check] = (),  # noqa: A002 — shadows the registry on purpose
    max_workers: int = 4,
    on_error: Literal["report", "raise"] = "report",
) -> ValidationReport:
    """Run ``checks`` against ``target`` and report.

    Args:
        target: whatever the checks understand — a path to a rendered file, a
            ``Project``, a ``(video, annotations)`` pair. This module does not
            care; it is the checks that agree with their caller.
        checks: names or :class:`Check` objects. Requirements are pulled in
            automatically. Empty means empty: validation is placed, never
            assumed.
        max_workers: concurrency within a wave.
        on_error: ``"report"`` records a raising check as an errored
            :class:`CheckResult` and carries on, so one broken plugin cannot
            hide the findings of the other nine. ``"raise"`` is for developing
            a check.

    Returns:
        A :class:`ValidationReport`. Note that ``report.ok`` is ``False`` when
        a check *errored*, not only when one failed: a suite that could not run
        has not said the work is good.

    Examples:
        >>> validate("x.mp4").ok
        True
    """
    started = time.monotonic()
    waves = plan_checks(checks)
    context: dict[str, Any] = {}
    results: list[CheckResult] = []

    for wave in waves:
        if len(wave) == 1:
            wave_results = [_run_one(wave[0], target, context, on_error)]
        else:
            with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
                wave_results = list(
                    pool.map(lambda c: _run_one(c, target, context, on_error), wave)
                )
        for r in wave_results:
            context[r.name] = r.produced
        results.extend(wave_results)

    return ValidationReport(
        target=target,
        results=tuple(results),
        elapsed_s=time.monotonic() - started,
    )


def _run_one(
    check: Check,
    target: Any,
    context: Mapping[str, Any],
    on_error: str,
) -> CheckResult:
    import shutil

    missing = [b for b in check.requires_binaries if shutil.which(b) is None]
    if missing:
        return CheckResult(
            name=check.name,
            skipped=f"needs {', '.join(missing)} on PATH",
        )
    started = time.monotonic()
    try:
        produced = check.run(target, {k: context.get(k) for k in check.requires})
    except Exception as exc:  # one bad plugin must not hide nine good ones
        if on_error == "raise":
            raise
        return CheckResult(
            name=check.name,
            error=f"{type(exc).__name__}: {exc}",
            elapsed_s=time.monotonic() - started,
        )
    findings, extra = _split(produced)
    return CheckResult(
        name=check.name,
        findings=tuple(findings),
        produced=extra,
        elapsed_s=time.monotonic() - started,
    )


def _split(produced: Any) -> tuple[Iterable[Finding], Any]:
    """A check returns findings, or ``(findings, produced)`` for dependents."""
    if (
        isinstance(produced, tuple)
        and len(produced) == 2
        and not isinstance(produced[0], Finding)
    ):
        return produced[0] or (), produced[1]
    return produced or (), None
