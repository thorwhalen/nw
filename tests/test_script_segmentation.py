"""
Unit tests for :mod:`nw.script_segmentation`.

These run against a deterministic stub LLM — the real LLM seam is
exercised by the downstream cassette-based e2e in ``reelee-web``.
"""

from __future__ import annotations

import pytest

from nw.script_segmentation import (
    PanelProposal,
    _parse_panel_response,
    build_prompt,
    segment_script_into_panels,
)


def _stub_llm_returns(payload: str):
    """Build an LLM seam that ignores the prompt and returns ``payload``."""
    return lambda _prompt: payload


# ────────────────────────── happy path ────────────────────────────────


def test_happy_path_returns_validated_panels() -> None:
    llm = _stub_llm_returns(
        '[{"description":"wide shot","duration_s":4.0},'
        ' {"description":"close-up","duration_s":2.5}]'
    )
    panels = segment_script_into_panels(
        "Some script.", target_panel_count=2, llm=llm
    )
    assert len(panels) == 2
    assert panels[0].description == "wide shot"
    assert panels[0].duration_s == 4.0
    assert panels[1].notes is None


def test_build_prompt_is_deterministic() -> None:
    """Same inputs → same prompt string. The cassette hashes this; any
    change here invalidates recorded fixtures (by design)."""
    a = build_prompt("Hello.", target_panel_count=3)
    b = build_prompt("Hello.", target_panel_count=3)
    assert a == b
    assert "3 storyboard panels" in a
    assert "Hello." in a


# ────────────────────────── tolerant parsing ──────────────────────────


def test_tolerates_markdown_code_fences() -> None:
    raw = '```json\n[{"description":"x","duration_s":1.0}]\n```'
    panels = _parse_panel_response(raw)
    assert len(panels) == 1
    assert panels[0].description == "x"


def test_tolerates_wrapper_object() -> None:
    raw = '{"panels":[{"description":"a","duration_s":1.0}]}'
    panels = _parse_panel_response(raw)
    assert len(panels) == 1


def test_tolerates_leading_prose() -> None:
    raw = 'Here you go:\n[{"description":"x","duration_s":1.0}]\n'
    panels = _parse_panel_response(raw)
    assert len(panels) == 1


# ────────────────────────── validation ────────────────────────────────


def test_rejects_zero_or_negative_duration() -> None:
    """The Pydantic model rejects ``duration_s <= 0``; ``_parse_panel_response``
    raises when no valid panel survives."""
    raw = '[{"description":"x","duration_s":0}]'
    with pytest.raises(ValueError, match="no valid panels"):
        _parse_panel_response(raw)


def test_rejects_empty_description() -> None:
    raw = '[{"description":"","duration_s":1.0}]'
    with pytest.raises(ValueError, match="no valid panels"):
        _parse_panel_response(raw)


def test_drops_invalid_keeps_valid() -> None:
    raw = (
        '[{"description":"ok","duration_s":1.0},'
        ' {"description":"","duration_s":1.0}]'
    )
    panels = _parse_panel_response(raw)
    assert len(panels) == 1
    assert panels[0].description == "ok"


def test_empty_script_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        segment_script_into_panels("   ", target_panel_count=2, llm=lambda _: "[]")


def test_non_json_response_raises() -> None:
    with pytest.raises(ValueError, match="could not parse"):
        _parse_panel_response("totally not json at all")
