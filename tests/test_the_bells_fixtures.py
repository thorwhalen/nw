"""Contract test: muvid-shaped projects load into ``nw.Project`` without migration.

The four ``the_bells_v*`` projects were created by muvid during the
2026-05-07/08 experiment run, one per render strategy. The whole point of
nw's schema design is that a project.json written by muvid loads directly
into ``nw.Project``. **If this module fails, the muvid → nw schema
compatibility contract has been broken.**

Two fixture sources, and the difference matters:

- **Committed, always runs.** ``tests/fixtures/muvid_projects/<name>/project.json``
  holds each project's *pre-graph* muvid-shaped document — sections, shots,
  characters and environments still inline as arrays, exactly as muvid wrote
  them. A few KB each, no media. This is the artefact the contract is
  actually about, so the assertion executes on every run, everywhere, with
  nothing configured. Each is copied into ``tmp_path`` first because loading
  a pre-graph project *migrates* it, and a test must not mutate a committed
  fixture.

- **Opt-in, media-bearing.** Set ``NW_FIXTURE_BASE`` to a folder holding the
  full original projects (each a directory named as below, containing a
  ``project.json``) to additionally run the contract against them. Those are
  hundreds of MB of audio and video, so they are not committed; the variable
  exists so a machine that has a copy can run the fuller smoke test. Unset —
  the normal case, including CI — those tests skip and the committed ones
  still prove the contract. These are copied into ``tmp_path`` too (JSON
  copied, media hard-linked so the copy costs nothing) for the same reason:
  loading a pre-graph project migrates it, and no test may mutate the
  developer's real projects.

This module previously gated *everything* on a hardcoded absolute path in
one developer's home directory, so the contract was unguarded on CI, on the
server, and in every other checkout, while the suite reported green. A
skipped test and a passing test are indistinguishable in the summary line.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from nw import Project, summarize_all


_COMMITTED_BASE = Path(__file__).parent / "fixtures" / "muvid_projects"
_FIXTURE_BASE_ENVVAR = "NW_FIXTURE_BASE"
_FIXTURES = (
    "the_bells_v1_lipsync",
    "the_bells_v2_establish",
    "the_bells_v3_still",
    "the_bells_v4_surreal",
)


# ---------------------------------------------------------------------------
# Committed fixtures — these run everywhere, with nothing configured
# ---------------------------------------------------------------------------


@pytest.fixture
def fixture_roots(tmp_path) -> list[Path]:
    """Working copies of all four committed fixtures.

    Copied because ``Project(...)`` auto-migrates a pre-graph project, which
    rewrites ``project.json`` and creates the graph store beside it.
    """
    roots = []
    for name in _FIXTURES:
        dst = tmp_path / name
        shutil.copytree(_COMMITTED_BASE / name, dst)
        roots.append(dst)
    return roots


def test_the_committed_fixtures_are_the_pre_graph_muvid_shape():
    """Guards the fixtures themselves: inline arrays, no nw graph artefacts.

    A fixture accidentally re-committed in post-migration form would still
    load — and would silently stop testing the muvid → nw contract, because
    the migration path is exactly what it exercises.
    """
    for name in _FIXTURES:
        doc = json.loads((_COMMITTED_BASE / name / "project.json").read_text())
        assert doc["schema_version"] == 1
        assert isinstance(doc["shots"], list) and doc["shots"], f"{name}: no shots"
        assert isinstance(doc["characters"], list)
        assert "_graph_db" not in doc, f"{name} was re-committed post-migration"
        assert not (_COMMITTED_BASE / name / ".nw").exists()


def test_each_fixture_loads_as_nw_project(fixture_roots):
    """Each muvid-shaped project.json must validate against nw.ProjectSpec."""
    for root in fixture_roots:
        spec = Project(root).read_spec()
        assert spec.schema_version == 1
        # Each was titled by muvid:
        assert spec.title == root.name
        # Each had a song registered:
        assert spec.song is not None
        assert spec.song.duration_s > 0


def test_each_fixture_has_thor_as_character(fixture_roots):
    """All four fixtures share the same character list — thor."""
    for root in fixture_roots:
        names = {c.name for c in Project(root).read_spec().characters}
        assert "thor" in names, f"{root.name} missing thor: {names}"


def test_each_fixture_has_at_least_one_shot(fixture_roots):
    for root in fixture_roots:
        assert len(Project(root).read_spec().shots) >= 1


def test_fixtures_cover_four_distinct_render_strategy_mixes(fixture_roots):
    """The four projects exist to cover four render strategies; keep it so.

    ``render_strategy`` is an open string to nw, so this also checks the
    field survives the muvid → graph → ProjectSpec round trip verbatim.
    """
    by_name = {
        root.name: {s.render_strategy for s in Project(root).read_spec().shots}
        for root in fixture_roots
    }
    assert "lipsync" in by_name["the_bells_v1_lipsync"]
    assert by_name["the_bells_v2_establish"] == {"image_to_video", "lipsync"}
    assert "image_to_video" in by_name["the_bells_v3_still"]
    assert by_name["the_bells_v4_surreal"] == {"image_to_video", "lipsync"}


def test_summarize_all_fixtures(fixture_roots):
    """The 'compare four interpretations' use case: one call, all four summaries."""
    summaries = summarize_all(fixture_roots)
    assert len(summaries) == 4
    assert {s.title for s in summaries} == set(_FIXTURES)


def test_rendered_shot_count_reflects_shot_outputs(fixture_roots):
    """``rendered_shot_count`` counts shots with an ``output.mp4``.

    The committed fixtures carry no media — that is the point — so the marker
    files are created here: this asserts the *counting*, while the opt-in
    media-bearing run below sees the real experiment outputs.
    """
    root = fixture_roots[0]
    spec = Project(root).read_spec()
    for shot in spec.shots:
        out = root / "shots" / shot.id
        out.mkdir(parents=True, exist_ok=True)
        (out / "output.mp4").write_bytes(b"")

    assert Project(root).read_summary().rendered_shot_count == len(spec.shots)


def test_loading_a_migrated_project_does_not_modify_disk(fixture_roots):
    """Once migrated, loading must be read-only.

    The first load migrates (a write, by design). Every load after it must
    leave ``project.json`` byte-identical.
    """
    root = fixture_roots[0]
    Project(root).read_spec()  # migrates

    before = (root / "project.json").read_bytes()
    Project(root).read_spec()
    assert (root / "project.json").read_bytes() == before


# ---------------------------------------------------------------------------
# Opt-in: the full media-bearing originals, via NW_FIXTURE_BASE
# ---------------------------------------------------------------------------


def _media_fixture_roots() -> list[Path]:
    base = os.environ.get(_FIXTURE_BASE_ENVVAR)
    if not base:
        return []
    root = Path(base).expanduser()
    roots = [root / name for name in _FIXTURES]
    return roots if all((r / "project.json").exists() for r in roots) else []


media_fixtures = pytest.mark.skipif(
    not _media_fixture_roots(),
    reason=(
        f"set {_FIXTURE_BASE_ENVVAR} to a folder holding the full "
        "the_bells_v* projects to run the media-bearing smoke test"
    ),
)


def _link_or_copy(src: str, dst: str, *, follow_symlinks: bool = True) -> None:
    """Hard-link *src* to *dst*, except JSON — which is copied.

    Loading a pre-graph project *migrates* it, and migration rewrites the
    project's JSON documents. Hard-linking those would truncate the original
    through the shared inode, so a test run would silently mutate the
    developer's real projects. The media (hundreds of MB) is never written,
    so linking it is safe and keeps the copy free.
    """
    if src.endswith(".json"):
        shutil.copy2(src, dst)
        return
    try:
        os.link(src, dst)
    except OSError:  # different filesystem, or a filesystem without hard links
        shutil.copy2(src, dst)


@media_fixtures
def test_media_fixtures_load_and_report_rendered_shots(tmp_path):
    """The fuller smoke test: the real experiment projects, with their media.

    Also asserts the run left the *sources* alone: these are a developer's
    real project folders, not a checkout, and ``summarize_all`` migrates a
    pre-graph project in place.
    """
    sources = _media_fixture_roots()
    before = {src: (src / "project.json").read_bytes() for src in sources}

    roots = []
    for src in sources:
        dst = tmp_path / src.name
        shutil.copytree(src, dst, symlinks=True, copy_function=_link_or_copy)
        roots.append(dst)

    summaries = summarize_all(roots)
    assert len(summaries) == 4
    assert {s.title for s in summaries} == set(_FIXTURES)
    # All four should report at least one rendered shot (the experiments shipped).
    assert sum(s.rendered_shot_count for s in summaries) >= 4

    for src, raw in before.items():
        assert (src / "project.json").read_bytes() == raw, f"{src.name} was mutated"
        assert not (src / ".nw").exists(), f"{src.name} was migrated in place"
