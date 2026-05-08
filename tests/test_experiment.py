"""Tests for nw.experiment — clone_project + apply_to_projects."""

from __future__ import annotations

from pathlib import Path

import pytest

from nw import (
    Project,
    ShotSpec,
    apply_to_projects,
    clone_project,
    summarize_all,
)


def _seed_project(root: Path, *, title: str) -> Project:
    proj = Project.init(root, title=title)
    proj.add_character("thor", description="narrator")
    proj.upsert_shot(ShotSpec(id="s01", start_s=0, end_s=5))
    # Pretend the song was set:
    (proj.root / "song" / "song.wav").write_bytes(b"WAV")
    # Pretend a render happened:
    (proj.shot_dir("s01") / "output.mp4").write_bytes(b"mp4")
    # Pretend a script exists:
    (proj.root / "script" / "script.md").write_text("# script")
    return proj


def test_clone_preserves_song_and_characters_resets_outputs(tmp_path):
    src = tmp_path / "src"
    _seed_project(src, title="Original")

    dst = tmp_path / "dst_v1"
    summary = clone_project(src, dst, title="V1")

    # Title was set to V1 on the clone.
    assert summary.title == "V1"
    # Song carries over.
    assert (dst / "song" / "song.wav").exists()
    # Characters carry over (preserve default).
    assert (dst / "characters" / "thor").is_dir()
    # Shots are reset to empty.
    assert (dst / "shots").is_dir()
    assert list((dst / "shots").iterdir()) == []
    # Output was reset.
    assert list((dst / "output").iterdir()) == []
    # Script subtree was reset (default).
    assert list((dst / "script").iterdir()) == []
    # Source unchanged.
    assert (src / "shots" / "s01" / "output.mp4").exists()


def test_clone_default_title_uses_dst_folder_name(tmp_path):
    src = tmp_path / "src"
    _seed_project(src, title="Original")

    dst = tmp_path / "the_bells_v1_lipsync"
    summary = clone_project(src, dst)
    assert summary.title == "the_bells_v1_lipsync"


def test_clone_refuses_existing_dst(tmp_path):
    src = tmp_path / "src"
    _seed_project(src, title="o")
    dst = tmp_path / "dst"
    clone_project(src, dst)
    with pytest.raises(FileExistsError):
        clone_project(src, dst)


def test_clone_force_overwrites(tmp_path):
    src = tmp_path / "src"
    _seed_project(src, title="o")
    dst = tmp_path / "dst"
    clone_project(src, dst, title="first")
    clone_project(src, dst, title="second", force=True)
    assert Project(dst).read_spec().title == "second"


def test_clone_custom_preserve_and_reset(tmp_path):
    src = tmp_path / "src"
    _seed_project(src, title="o")

    dst = tmp_path / "dst"
    # Preserve the script too; reset only output.
    clone_project(src, dst, preserve=("song", "characters", "script"), reset=("output",))
    assert (dst / "script" / "script.md").exists()  # preserved
    assert list((dst / "output").iterdir()) == []   # reset
    # Shots were NOT in the reset set, so they aren't present at all
    # (because they weren't preserved either) — directory may not exist.


def test_clone_logs_decision(tmp_path):
    src = tmp_path / "src"
    _seed_project(src, title="o")
    dst = tmp_path / "dst"
    clone_project(src, dst)
    decisions_file = dst / ".nw" / "decisions.jsonl"
    assert decisions_file.exists()
    text = decisions_file.read_text()
    assert "clone_project" in text
    assert str(src) in text


# --- apply_to_projects -------------------------------------------------------


def test_apply_to_projects_sequential(tmp_path):
    roots = []
    for i, t in enumerate(["A", "B", "C"]):
        root = tmp_path / f"p{i}"
        _seed_project(root, title=t)
        roots.append(root)

    titles = apply_to_projects(roots, lambda p: p.read_spec().title)
    assert titles == ["A", "B", "C"]


def test_apply_to_projects_parallel(tmp_path):
    roots = []
    for i in range(4):
        root = tmp_path / f"p{i}"
        _seed_project(root, title=f"P{i}")
        roots.append(root)

    titles = apply_to_projects(roots, lambda p: p.read_spec().title, parallel=True)
    # Order is preserved even with parallel=True (matches roots iteration order).
    assert titles == ["P0", "P1", "P2", "P3"]


def test_summarize_all_returns_summaries(tmp_path):
    roots = []
    for i in range(2):
        root = tmp_path / f"p{i}"
        _seed_project(root, title=f"P{i}")
        roots.append(root)

    summaries = summarize_all(roots)
    assert len(summaries) == 2
    assert summaries[0].title == "P0"
    assert summaries[1].title == "P1"
    assert all(s.character_count == 1 for s in summaries)
    assert all(s.rendered_shot_count == 1 for s in summaries)
