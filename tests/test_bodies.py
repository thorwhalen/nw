"""Tests for nw.bodies — body-schema registration with lacing."""

from __future__ import annotations

import pytest

from lacing import validate_body

import nw  # registers all bodies at import
from nw.bodies import (
    CHARACTER_REF_BODY_SCHEMA_URI,
    DECISION_BODY_SCHEMA_URI,
    ENVIRONMENT_REF_BODY_SCHEMA_URI,
    SECTION_BODY_SCHEMA_URI,
    SHOT_BODY_SCHEMA_URI,
)


@pytest.mark.parametrize(
    "uri,body",
    [
        (
            SECTION_BODY_SCHEMA_URI,
            {"section_id": "verse", "label": "verse", "energy": "low", "mood": "calm"},
        ),
        (
            SHOT_BODY_SCHEMA_URI,
            {
                "shot_id": "s01",
                "section_id": "verse",
                "render_strategy": "composite_lipsync",
                "environment": "bell_tower",
                "characters": ("thor",),
                "description": "Thor at the piano",
                "camera": "static",
                "framing": "medium",
                "notes": "",
            },
        ),
        (
            CHARACTER_REF_BODY_SCHEMA_URI,
            {"name": "thor", "description": "narrator"},
        ),
        (
            ENVIRONMENT_REF_BODY_SCHEMA_URI,
            {"name": "bell_tower", "description": "Gothic"},
        ),
        (
            DECISION_BODY_SCHEMA_URI,
            {"kind": "render_shot", "payload": {"shot_id": "s01", "quality": "balanced"}},
        ),
    ],
)
def test_body_validates_against_registered_schema(uri, body):
    """Each body schema accepts a well-formed body via lacing.validate_body."""
    result = validate_body(body, uri)
    # Validate returns the parsed model.
    assert result is not None


@pytest.mark.parametrize(
    "uri,bad_body",
    [
        # Missing required fields.
        (SECTION_BODY_SCHEMA_URI, {"label": "verse"}),  # no section_id
        (SHOT_BODY_SCHEMA_URI, {"section_id": "v"}),    # no shot_id
        (CHARACTER_REF_BODY_SCHEMA_URI, {"description": "x"}),  # no name
        # Extra forbidden fields.
        (
            SECTION_BODY_SCHEMA_URI,
            {"section_id": "v", "rogue_field": 1},
        ),
    ],
)
def test_body_rejects_invalid(uri, bad_body):
    with pytest.raises(Exception):
        validate_body(bad_body, uri)


def test_decision_payload_is_freeform():
    """DecisionBodyV1.payload accepts arbitrary keys."""
    body = {
        "kind": "set_character_anchor",
        "payload": {
            "character": "thor",
            "anchor_path": "characters/thor/selected/Thor-Whalen-2025-15.jpg",
            "rationale": "matches v1 anchor",
            "any_int": 42,
            "any_list": [1, 2, 3],
        },
    }
    result = validate_body(body, DECISION_BODY_SCHEMA_URI)
    assert result.payload["any_int"] == 42
