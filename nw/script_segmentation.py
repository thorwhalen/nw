"""
``nw.script_segmentation`` — narrow LLM-backed helper that converts a
free-form script into a list of storyboard-panel proposals.

This module is intentionally **focused and narrow**: it's the smallest
possible thing that turns "user pasted some prose" into "n panels with
descriptions and durations" so a downstream UI can render them. It is
*not* a full :class:`nw.transforms.Transform` — that abstraction will
absorb this work once it lands. For now we keep the surface as a plain
function with a dependency-injection seam (the ``llm`` arg), so:

- Tests can pass a deterministic stub (or a cassette-wrapped function)
  without needing API keys or network.
- The real implementation can swap between OpenAI / Anthropic / local
  models without callers caring.
- The cost-honesty rule (every billable call should be inspectable) is
  trivially upheld: the seam is the call.

Persisting the proposals as annotations is the *caller's* job (this
module is pure — no project I/O). See
``reelee_backend.handlers.post_script_segment`` for the wiring.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Iterable

from pydantic import BaseModel, Field, ValidationError, field_validator


class PanelProposal(BaseModel):
    """One storyboard panel proposed by the segmenter.

    The shape is intentionally close to ``annot://schema/storyboard-panel/v1``
    (the lacing body schema) so the caller can promote a proposal into a
    real panel annotation with a minimal mapping step.
    """

    description: str = Field(..., min_length=1, description="What's on screen.")
    duration_s: float = Field(
        ..., gt=0.0, le=60.0, description="Suggested duration in seconds."
    )
    notes: str | None = Field(
        default=None, description="Optional director's notes / mood / camera."
    )

    @field_validator("description")
    @classmethod
    def _strip_description(cls, v: str) -> str:
        return v.strip()


LLM = Callable[[str], str]
"""The LLM seam — any function taking a string prompt and returning a
string response. Tests pass a cassette-wrapped stub; production passes
``oa.chat`` (or whatever's been wired)."""


PROMPT_TEMPLATE = """\
You are a storyboard editor. Read the script below and segment it into \
EXACTLY {n} storyboard panels. Each panel covers one continuous moment.

Respond with a JSON array of objects with these fields:
- description: one or two sentences describing what is on screen.
- duration_s: a float between 0.5 and 60.0, the seconds the panel holds.
- notes: optional, brief director notes (mood, camera, lighting).

Total durations should roughly match the rhythm of the script — short \
beats get short durations, sustained moments get longer ones.

Return ONLY the JSON array, no surrounding prose. Do not wrap it in \
markdown fences.

Script:
{script}
"""


def build_prompt(script: str, *, target_panel_count: int) -> str:
    """The canonical prompt string sent to the LLM. Exposed so callers
    + tests can inspect / version it. **The cassette hashes this string**,
    so any change here invalidates recorded fixtures."""
    return PROMPT_TEMPLATE.format(n=target_panel_count, script=script.strip())


def segment_script_into_panels(
    script: str,
    *,
    target_panel_count: int,
    llm: LLM,
) -> list[PanelProposal]:
    """Segment ``script`` into ``target_panel_count`` panel proposals.

    Pure function — no I/O beyond the ``llm`` callable. The caller is
    responsible for choosing / wrapping the LLM (e.g. with a cassette
    or with caching).

    Args:
        script: Free-form prose. Whitespace is preserved verbatim in
            the prompt, so trimming + canonicalisation is the caller's
            decision.
        target_panel_count: Soft target — the LLM is asked for exactly
            this many. Real-world deviations of ±1 are tolerated.
        llm: The text→text seam. Receives the formatted prompt, must
            return a string. The expected response is a JSON array of
            ``{description, duration_s, notes}`` objects.

    Returns:
        A list of validated :class:`PanelProposal` instances.

    Raises:
        ValueError: The LLM response could not be parsed as a JSON
            array of panels, or no valid panels survived validation.
    """
    if not script or not script.strip():
        raise ValueError("segment_script_into_panels: script is empty")
    if target_panel_count < 1:
        raise ValueError(
            f"target_panel_count must be ≥ 1, got {target_panel_count}"
        )

    prompt = build_prompt(script, target_panel_count=target_panel_count)
    raw = llm(prompt)
    return _parse_panel_response(raw)


def _parse_panel_response(raw: str) -> list[PanelProposal]:
    """Parse the LLM's JSON-array response into validated panels.

    Tolerant of a few common LLM quirks:

    - Surrounding ```json fences``` (despite the prompt asking against them).
    - A wrapping object ``{"panels": [...]}`` instead of a bare array.
    - Whitespace / a leading prose line before the array.
    """
    text = raw.strip()
    # Strip markdown code fences if the LLM ignored the prompt.
    fence = re.match(r"^```(?:json)?\s*(.*?)```$", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Try parsing as-is first; if that fails, attempt to locate the
    # outermost array / object in the string.
    payload = _try_parse_json(text) or _try_parse_json(_first_json_value(text))
    if payload is None:
        raise ValueError(
            f"could not parse LLM response as JSON; got: {raw[:200]!r}…"
        )
    items = _coerce_items(payload)
    panels: list[PanelProposal] = []
    errors: list[str] = []
    for i, item in enumerate(items):
        try:
            panels.append(PanelProposal.model_validate(item))
        except ValidationError as e:
            errors.append(f"panel {i}: {e.errors()[0]['msg']}")
    if not panels:
        raise ValueError(
            "no valid panels in LLM response; errors: " + "; ".join(errors)
        )
    return panels


def _try_parse_json(text: str | None) -> object | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _first_json_value(text: str) -> str | None:
    """Return the substring starting at the first ``[`` or ``{`` to its
    matching close — best-effort extraction of a JSON value embedded in
    surrounding prose."""
    for opener, closer in (("[", "]"), ("{", "}")):
        i = text.find(opener)
        j = text.rfind(closer)
        if i != -1 and j != -1 and j > i:
            return text[i : j + 1]
    return None


def _coerce_items(payload: object) -> Iterable[dict]:
    """Accept either a top-level array or a ``{panels: [...]}`` object."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("panels", "items", "result", "results"):
            v = payload.get(key)
            if isinstance(v, list):
                return v
    raise ValueError(
        f"expected a JSON array of panels or object containing one; got "
        f"{type(payload).__name__}"
    )
