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


# ---------------------------------------------------------------------------
# character-ref stable attributes (nw#5)
# ---------------------------------------------------------------------------


def test_character_ref_accepts_stable_attributes():
    """The enriched fields are accepted and round-trip through validate_body."""
    body = {
        "name": "thor",
        "description": "the narrator",
        "costume": "grey tweed jacket, green flat cap",
        "age": "late 50s",
        "default_setting": "frozen_belltower",
        "distinguishing_features": ("left eye scar",),
        "palette_anchors": ("#8899aa", "#221100"),
        "do_not_do": ("no shamrocks", "never a bowler hat"),
    }
    result = validate_body(body, CHARACTER_REF_BODY_SCHEMA_URI)
    assert result.costume == "grey tweed jacket, green flat cap"
    assert result.age == "late 50s"
    assert result.default_setting == "frozen_belltower"
    assert result.distinguishing_features == ("left eye scar",)
    assert result.palette_anchors == ("#8899aa", "#221100")
    assert result.do_not_do == ("no shamrocks", "never a bowler hat")


def test_character_ref_old_dumps_still_load():
    """Additive, not a new version: a pre-enrichment dump loads unchanged."""
    result = validate_body(
        {"name": "thor", "description": "the narrator"},
        CHARACTER_REF_BODY_SCHEMA_URI,
    )
    assert result.costume == ""
    assert result.age == ""
    assert result.default_setting == ""
    assert result.distinguishing_features == ()
    assert result.palette_anchors == ()
    assert result.do_not_do == ()


def test_character_ref_field_names_match_artful_model_sheet():
    """One ecosystem vocabulary: the overlapping concepts share a name AND a type.

    Guards against the drift the issue was filed to prevent — someone adding
    ``negative_prompts`` here while artful says ``do_not_do``.
    """
    artful_schema = pytest.importorskip("artful.schema")

    from nw.bodies import CharacterRefBodyV1

    shared = ("palette_anchors", "distinguishing_features", "do_not_do")
    sheet_fields = artful_schema.ModelSheet.model_fields
    ref_fields = CharacterRefBodyV1.model_fields
    for field in shared:
        assert field in sheet_fields, f"artful.ModelSheet lost {field!r}"
        assert field in ref_fields, f"CharacterRefBodyV1 lost {field!r}"
        assert (
            ref_fields[field].annotation == sheet_fields[field].annotation
        ), f"{field!r} types drifted between nw and artful"
