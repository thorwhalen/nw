"""Smoke test: load the four canonical the_bells_v* fixtures without migration.

These projects were created by muvid during the 2026-05-07/08 experiment run.
The whole point of nw's schema design is that they load directly into nw.Project
without any migration. If this test fails, the muvid → nw schema compatibility
contract has been broken.

The test is gated on the fixture's presence at /Users/thorwhalen/Downloads/muvid_project/
— it skips on a CI box that doesn't have the fixtures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nw import Project, summarize_all


_FIXTURE_BASE = Path("/Users/thorwhalen/Downloads/muvid_project")
_FIXTURES = (
    "the_bells_v1_lipsync",
    "the_bells_v2_establish",
    "the_bells_v3_still",
    "the_bells_v4_surreal",
)


def _fixture_roots():
    return [_FIXTURE_BASE / name for name in _FIXTURES]


def _fixtures_present() -> bool:
    return all(
        (_FIXTURE_BASE / name / "project.json").exists() for name in _FIXTURES
    )


pytestmark = pytest.mark.skipif(
    not _fixtures_present(),
    reason=f"fixtures not present at {_FIXTURE_BASE}",
)


def test_each_fixture_loads_as_nw_project():
    """Each muvid-shaped project.json must validate against nw.ProjectSpec."""
    for root in _fixture_roots():
        proj = Project(root)
        spec = proj.read_spec()
        assert spec.schema_version == 1
        # Each was titled by muvid:
        assert spec.title == root.name
        # Each had a song registered:
        assert spec.song is not None
        assert spec.song.duration_s > 0


def test_each_fixture_has_thor_as_character():
    """All four fixtures share the same character list — Thor."""
    for root in _fixture_roots():
        proj = Project(root)
        spec = proj.read_spec()
        names = {c.name for c in spec.characters}
        assert "thor" in names, f"{root.name} missing thor: {names}"


def test_each_fixture_has_at_least_one_shot():
    for root in _fixture_roots():
        spec = Project(root).read_spec()
        assert len(spec.shots) >= 1


def test_summarize_all_fixtures():
    """The user's 'compare four interpretations' use case: one call, all four
    summaries."""
    summaries = summarize_all(_fixture_roots())
    assert len(summaries) == 4
    titles = {s.title for s in summaries}
    assert titles == set(_FIXTURES)
    # All four should report at least one rendered shot (the experiments shipped).
    rendered_any = sum(s.rendered_shot_count for s in summaries)
    assert rendered_any >= 4


def test_v3_uses_image_to_video_strategy():
    """v3_still rendered via image_to_video; sanity check the strategy field
    survives the JSON round-trip even though nw treats it as an open string."""
    spec = Project(_FIXTURE_BASE / "the_bells_v3_still").read_spec()
    strategies = {s.render_strategy for s in spec.shots}
    assert "image_to_video" in strategies


def test_v1_lipsync_strategy_loaded():
    spec = Project(_FIXTURE_BASE / "the_bells_v1_lipsync").read_spec()
    strategies = {s.render_strategy for s in spec.shots}
    assert "lipsync" in strategies


def test_loading_does_not_modify_disk():
    """Loading a fixture must be read-only; the on-disk project.json should
    be byte-identical after a Project(...) + read_spec() round-trip."""
    root = _FIXTURE_BASE / "the_bells_v1_lipsync"
    before = (root / "project.json").read_bytes()
    Project(root).read_spec()
    after = (root / "project.json").read_bytes()
    assert before == after
