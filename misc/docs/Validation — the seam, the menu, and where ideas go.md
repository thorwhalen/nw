# Validation — the seam, the menu, and where ideas go

*Record of decision. Written 2026-09-17, when the seam landed. The checks themselves mostly do not exist yet; this says where they go when they do.*

## The problem

A render that finishes without raising is not a render that is right. Three real examples from one week of one production, each found by a human watching the finished file:

- a one-minute vertical short shipped with a burnt-in lower third reading `1981 · Centr…` — not a shortened label, a *wrong* one;
- a ten-minute cut was killed by the OOM killer two-thirds of the way through the encode, and every duration check passed, because an MP4 container takes its duration from the audio stream;
- a Ken Burns move jumped a whole pixel mid-panel for months, in every film that used it.

A human watching the output is the most expensive detector we have and the only one that gets tired. None of these needed judgement to find — each is a measurable property of the finished file.

## What this is, and what it deliberately is not

`nw.validation` owns the **seam**: how a check is declared, how the menu is assembled, how dependencies are resolved, in what order and with how much concurrency checks run, and what a run reports. It owns **no domain knowledge at all**. Every actual check is a plugin.

It also does not own **placement**. Nothing in nw calls `validate()` for you, and that is the decision, not an omission. Where validation belongs is a judgement about cost and consequence that only the caller can make:

- before showing a result to a human, when the checks are cheap;
- before an irreversible or outward-facing step — a publish, an upload, a send — where the checks earn their cost whatever it is;
- both, with a cheaper selection at the first point than the second.

A gate placed by the substrate is a gate that fires at the wrong moment, on somebody else's budget.

## The shape

```python
from nw import validate, menu, suggest

report = validate(film_path, checks=["media.encode_complete", "media.no_long_freeze"])
report.ok  # False if anything failed *or if any check could not run*
report.summary()  # a few lines a human reads without unpacking anything
report.raise_if_failed()  # for a hard gate
```

Four distinctions the types exist to keep apart, because blurring any of them produces a suite that reports all clear on a machine where half of it was never installed:

| | meaning |
|---|---|
| a finding at `warn` | worth knowing, does not block |
| a finding at `error` | blocks |
| `CheckResult.skipped` | never ran (a missing binary, say). **Not a pass.** |
| `CheckResult.error` | raised. **Not a pass**, and does not hide the other checks' findings |

`ValidationReport.ok` is therefore conservative by construction: a caller gating a publish on it gets the safe answer without having to remember to ask for it.

### Dependencies, ordering, concurrency

A check declares `requires=(...)`. `plan_checks` resolves the closure of the selection, refuses cycles *at plan time* (before anything has been spent), and returns **waves**: each wave's requirements are satisfied by earlier waves, so run each wave in turn and everything inside it concurrently. A check that declares `parallel_safe=False` gets a wave to itself.

Whatever a check returns as its second value arrives in its dependents' `context`, keyed by name — so an expensive shared computation (one ffprobe, one decode pass, one frame sampling) is done once. That is what makes a menu of thirty checks affordable: most of the cost is the shared passes, not the checks.

### `example_requests` is not decoration

A menu of forty checks is unusable by a human and unselectable by a model. Every check carries the phrases a person actually says when they want it, and `suggest("the picture freezes near the end")` matches against them. That is how this reaches an MCP tool surface as *one* tool that takes a request, rather than a forty-item enum that eats the caller's token budget and gets picked from badly.

`cost="paid"` checks (a hosted OCR, a transcription) are **never** suggested. Money is asked for by name.

## Where a check's code goes

The seam is in nw. The checks are not, necessarily — a check belongs **in the package that owns the knowledge it applies**, and is registered from there:

| the check is about | it lives in |
|---|---|
| the container, streams, duration, freezes, frame integrity | `nw.checks` (nw ships these three) |
| on-screen type — is the caption complete, legible, inside the safe area | next to `tituli` |
| camera motion — jumps, stepping, a move that leaves the picture | next to `burns` |
| audio — silence, clipping, loudness, narration/music balance | next to `mixing` or `braidio` |
| licensing and attribution — every credited image actually credited | next to `illustration` / `lacing` |
| the film against its own annotations — did what the graph asked for reach the screen | `nw`, because only nw has both sides |

Registration is an explicit call at import time (`nw.register_check`), the same way `nw.transforms` is populated — there is no entry-point discovery in this ecosystem, deliberately: a plugin that appears because it happens to be installed is a plugin nobody chose.

**Gathering them.** Each owning package registers its own checks when it is imported, so the menu a caller sees is exactly the menu their installed packages offer. `nw.menu()` is the whole answer; there is nothing to gather by hand.

## Where *ideas* go

Ideas arrive long before anyone builds them — usually as "here's another thing that should have been caught". They accumulate in **one place**:

> **GitHub issues on `thorwhalen/nw`, labelled `validation-idea`.**
> List them: `gh issue list -R thorwhalen/nw --label validation-idea`

One place, not one per owning package, because the *menu* is one thing even though the code is scattered — and because an idea rarely arrives knowing which package will end up owning it. The issue records which package it should live in as a field; if it moves, the field changes and nothing else does.

Each idea issue carries, at minimum:

- **What it catches** — and, if it exists, *the actual defect that prompted it*. An idea with a real failure attached is worth ten without.
- **How it would work** — enough to judge whether it is a day or a month.
- **What it needs** — other checks it would depend on, binaries, whether it costs money.
- **Example user requests** — the phrases that mean someone wants it. These become `example_requests`, so writing them down at idea time is not busywork.
- **Where it should live** — the owning package, per the table above.

The `github-memory` skill carries the same rule, so an agent told "here's another idea for video validation" knows where to put it without being told twice.

## Standing rules

1. **Placed, never assumed.** `validate(x)` with no selection runs nothing and returns `ok`. Validation that happens because a library decided it should is validation that fires at the wrong moment.
2. **Could-not-run is not a pass.** A skipped or errored check makes the report not `ok`.
3. **An unknown check name raises.** A silently dropped check is precisely the failure this exists to prevent.
4. **`paid` is opted into by name.** Never suggested, never pulled in implicitly.
5. **A finding says where and, when it can, what would fix it.** A boolean is not a finding.
6. **A check with no `summary` or no `example_requests` is not finished** — it cannot be chosen, by a human or by a model.
