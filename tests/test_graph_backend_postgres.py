"""Postgres-backend tests for nw's annotation graph (Phase 4, reelee#177).

Validates that the backend-selection seam (:mod:`nw.graph_backend`) routes the
project graph, storyboard, and alignment stores onto a shared Postgres DB when
``NW_GRAPH_BACKEND=postgres`` is set — and that two projects in one DB are
isolated.

Gated exactly like ``lacing/tests/test_store_postgres.py``: requires
``pytest-postgresql`` + ``psycopg`` + a Postgres binary on PATH. The SQLite
default path (zero behaviour change) is covered by the rest of the nw suite.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

# Skip the whole module unless a real local Postgres is available.
pytest.importorskip("pytest_postgresql")
pytest.importorskip("psycopg")
if not any(shutil.which(name) for name in ("pg_ctl", "postgres")):
    pytest.skip("Postgres binary not on PATH", allow_module_level=True)

import psycopg  # noqa: E402

import nw  # noqa: E402
from nw.bodies import SectionBodyV1, ShotBodyV1  # noqa: E402
from nw.migrate import (  # noqa: E402
    migrate_to_graph,
    open_project_graph,
    project_asset_id,
)
from lacing import TimeInterval  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _conninfo(postgresql) -> str:
    info = postgresql.info
    return psycopg.conninfo.make_conninfo(
        host=info.host,
        port=info.port,
        user=info.user,
        password=info.password or "",
        dbname=info.dbname,
    )


def _seed_pre_graph_project(tmp_path: Path, name: str, *, title: str) -> Path:
    """A minimal pre-graph project.json (no song → stable derived asset_id)."""
    root = tmp_path / name
    root.mkdir()
    for sub in ("characters", "environments", "shots", "lyrics", ".nw"):
        (root / sub).mkdir(exist_ok=True)
    spec = {
        "schema_version": 1,
        "title": title,
        "song": None,
        "characters": [{"name": "thor", "description": "the singer"}],
        "environments": [{"name": "tower", "description": "Gothic"}],
        "sections": [
            {
                "id": "verse",
                "start_s": 0.0,
                "end_s": 8.0,
                "label": "verse",
                "energy": "low",
                "mood": "noir",
            }
        ],
        "shots": [
            {
                "id": "s01",
                "start_s": 0.0,
                "end_s": 8.0,
                "section_id": "verse",
                "render_strategy": "lipsync",
                "environment": "tower",
                "characters": ["thor"],
                "description": "Thor singing",
                "camera": "static",
                "framing": "medium",
                "notes": "",
            }
        ],
        "global_style": "noir",
        "notes": "",
    }
    (root / "project.json").write_text(json.dumps(spec, indent=2))
    return root


@pytest.fixture
def pg_env(postgresql, monkeypatch):
    """Point nw's graph backend at the per-test Postgres DB."""
    monkeypatch.setenv("NW_GRAPH_BACKEND", "postgres")
    monkeypatch.setenv("NW_GRAPH_DB_URL", _conninfo(postgresql))
    # close pooled connections between tests so a stale conn to a torn-down DB
    # never leaks into the next test.
    from lacing.store import close_all_pools

    yield
    close_all_pools()


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_migration_round_trip_on_postgres(tmp_path, pg_env):
    """Migrating a pre-graph project writes annotations into Postgres and the
    typed accessors read them back — no SQLite file is created."""
    root = _seed_pre_graph_project(tmp_path, "p", title="One")
    counts = migrate_to_graph(root)
    assert counts["sections"] == 1
    assert counts["shots"] == 1

    # No SQLite file was created — the graph lives in Postgres.
    assert not (root / "project.annot.sqlite").exists()

    proj = nw.Project(root)
    sections = proj.graph.sections()
    shots = proj.graph.shots()
    assert len(sections) == 1
    assert sections[0].body.section_id == "verse"
    assert len(shots) == 1
    assert shots[0].body.shot_id == "s01"

    # iter_all_annotations walks the Postgres-backed stores.
    anns = list(nw.iter_all_annotations(root))
    tiers = {a.tier for a in anns}
    assert {"section", "shot", "character-ref", "environment-ref"} <= tiers


def test_two_projects_one_db_are_isolated(tmp_path, pg_env):
    """The headline guarantee: two nw projects sharing one Postgres DB never
    see each other's annotations."""
    root_a = _seed_pre_graph_project(tmp_path, "a", title="Alpha")
    root_b = _seed_pre_graph_project(tmp_path, "b", title="Beta")
    migrate_to_graph(root_a)
    migrate_to_graph(root_b)

    # Distinct titles + roots → distinct project_asset_id → distinct tenants.
    assert project_asset_id(root_a) != project_asset_id(root_b)

    # Add a second shot to project A only.
    pa = nw.Project(root_a)
    pa.graph.upsert_shot(
        ShotBodyV1(
            shot_id="s02",
            section_id="verse",
            render_strategy="image_to_video",
            environment="tower",
            characters=("thor",),
            description="extra shot",
            camera="pan",
            framing="wide",
            notes="",
        ),
        interval=TimeInterval.from_seconds(8, 16),
    )

    a_shots = nw.Project(root_a).graph.shots()
    b_shots = nw.Project(root_b).graph.shots()
    assert {s.body.shot_id for s in a_shots} == {"s01", "s02"}
    assert {s.body.shot_id for s in b_shots} == {"s01"}  # B unaffected

    # Cross-project annotation counts don't bleed.
    a_anns = list(nw.iter_all_annotations(root_a))
    b_anns = list(nw.iter_all_annotations(root_b))
    a_shot_ids = {a.body.get("shot_id") for a in a_anns if a.tier == "shot"}
    b_shot_ids = {a.body.get("shot_id") for a in b_anns if a.tier == "shot"}
    assert a_shot_ids == {"s01", "s02"}
    assert b_shot_ids == {"s01"}


def test_storyboard_on_postgres(tmp_path, pg_env):
    """The storyboard scope round-trips on Postgres and stays distinct from the
    graph scope (same project, different tenant project_id)."""
    from nw import open_storyboard
    from nw.storyboard import save_storyboard, storyboard_from_shots

    root = _seed_pre_graph_project(tmp_path, "p", title="SB")
    migrate_to_graph(root)
    proj = nw.Project(root)

    # Empty storyboard before any save.
    assert len(open_storyboard(proj).panels) == 0

    sb, intervals = storyboard_from_shots(proj)
    assert len(sb.panels) == 1
    save_storyboard(proj, sb, panel_intervals=intervals)

    loaded = open_storyboard(nw.Project(root))
    assert len(loaded.panels) == 1
    assert loaded.panels[0].shot_id == "s01"

    # The storyboard panels are NOT visible as graph shots (separate scope).
    assert {s.body.shot_id for s in nw.Project(root).graph.shots()} == {"s01"}


def test_alignment_scope_present_in_walk(tmp_path, pg_env):
    """An alignment-scope annotation written via the seam is picked up by the
    project-wide walk under Postgres."""
    import uuid as _uuid

    from lacing import Annotation, MediaRef, Provenance, Tier, TierStereotype
    from lacing.artifact import _now_rt

    from nw.graph_backend import SCOPE_ALIGNMENT, open_graph_store

    root = _seed_pre_graph_project(tmp_path, "p", title="AL")
    migrate_to_graph(root)

    asset_id = project_asset_id(root)
    align_path = root / "lyrics" / "alignment.annot"
    store = open_graph_store(align_path, asset_id=asset_id, scope=SCOPE_ALIGNMENT)
    try:
        store.add_tier(Tier(name="lyric-line", stereotype=TierStereotype.NONE))
        store.add(
            Annotation(
                id=_uuid.uuid4(),
                tier="lyric-line",
                reference=MediaRef(
                    asset_id=asset_id, interval=TimeInterval.from_seconds(0, 4)
                ),
                body={"text": "ring the bells"},
                body_schema_uri="annot://schema/lyric-line/v1",
                provenance=Provenance(
                    was_generated_by="test",
                    was_attributed_to="test",
                    generated_at_time=_now_rt(),
                ),
            )
        )
    finally:
        store.close()

    texts = [
        a.body.get("text")
        for a in nw.iter_all_annotations(root)
        if a.tier == "lyric-line"
    ]
    assert texts == ["ring the bells"]
