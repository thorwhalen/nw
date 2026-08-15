"""The resolved genre envelope is persisted on the project (nw#32).

Before nw#32, ``create_genre_project`` resolved ``{genre, template, params}``,
used it to seed the project, returned it to the caller — and stored nothing.
A project's genre was knowable only at the moment of creation, by whoever
called the constructor. These tests pin the fix at its contract:

- **the round-trip that failed before**: create → reopen → envelope identical;
- both creation paths persist it — host-creates-then-``initialize_genre``
  (the path reelee actually uses, which never saw the envelope at all) and
  ``create_genre_project``;
- persistence is *after* the seed, so a recorded envelope certifies a
  completed initialization (all-or-nothing with the rollback in
  ``create_genre_project``);
- the envelope is bookkeeping: it must never masquerade as the user's last
  authored change, and a spec write must not eat it.
"""

from __future__ import annotations

import pytest

import nw
from nw.bodies import GENRE_ENVELOPE_TIER
from nw.genres import (
    Genre,
    Template,
    genre_initializers,
    genre_project_factories,
    genres,
    register_genre,
    register_genre_initializer,
    register_genre_project_factory,
)
from nw.schema import SectionSpec


@pytest.fixture
def clean_genre_registries():
    """Snapshot + restore every genre registry a test may touch."""
    snaps = [
        (genres, dict(genres)),
        (genre_initializers, dict(genre_initializers)),
        (genre_project_factories, dict(genre_project_factories)),
    ]
    try:
        yield
    finally:
        for reg, before in snaps:
            for key in list(reg.keys()):
                del reg[key]
            for key, value in before.items():
                reg.register(key, value)


def _demo_genre(**kw) -> Genre:
    base = dict(
        slug="env_demo",
        title="Envelope Demo",
        defaults={"format_id": "solo"},
        templates=(
            Template(slug="duo", title="Duo", params={"format_id": "duo"}),
        ),
    )
    base.update(kw)
    return register_genre(Genre(**base))


def _envelope_annotations(proj) -> list:
    return [
        a
        for a in nw.iter_all_annotations(proj.root)
        if a.tier == GENRE_ENVELOPE_TIER
    ]


# --- the round-trip that failed before nw#32 ---------------------------------


def test_host_path_round_trip_create_reopen_envelope_identical(
    tmp_path, clean_genre_registries
):
    """Host creates the project, calls ``initialize_genre`` — reelee's path."""
    _demo_genre()
    proj = nw.Project.init(tmp_path / "p")
    nw.initialize_genre("env_demo", proj)  # no initializer registered

    reopened = nw.Project(tmp_path / "p")
    assert reopened.resolved_genre() == {
        "genre": "env_demo",
        "template": None,
        "params": {"format_id": "solo"},
    }


def test_template_choice_is_recorded_with_its_resolved_params(
    tmp_path, clean_genre_registries
):
    _demo_genre()
    proj = nw.Project.init(tmp_path / "p")
    nw.initialize_genre("env_demo", proj, template="duo")

    assert nw.Project(tmp_path / "p").resolved_genre() == {
        "genre": "env_demo",
        "template": "duo",
        "params": {"format_id": "duo"},
    }


def test_create_genre_project_persists_what_it_returns(
    tmp_path, clean_genre_registries
):
    """The factory path: the envelope handed to the caller is also on disk."""
    _demo_genre()
    register_genre_project_factory(
        "env_demo",
        lambda caller, pid, *, title, template, params: {
            "project": nw.Project.init(tmp_path / caller / pid),
            "project_id": pid,
        },
    )

    result = nw.create_genre_project("env_demo", "u@x.com", "p1", template="duo")

    stored = nw.Project(tmp_path / "u@x.com" / "p1").resolved_genre()
    assert stored == {
        "genre": result["genre"],
        "template": result["template"],
        "params": result["params"],
    }
    assert stored["params"] == {"format_id": "duo"}


def test_no_envelope_reads_as_none(tmp_path):
    assert nw.Project.init(tmp_path / "p").resolved_genre() is None


# --- singleton + replacement semantics ---------------------------------------


def test_reinitializing_replaces_the_envelope_in_place(
    tmp_path, clean_genre_registries
):
    _demo_genre()
    proj = nw.Project.init(tmp_path / "p")
    nw.initialize_genre("env_demo", proj)
    nw.initialize_genre("env_demo", proj, template="duo")

    (only,) = _envelope_annotations(proj)  # singleton, not an accumulating log
    assert proj.resolved_genre()["params"] == {"format_id": "duo"}


# --- all-or-nothing with the seed --------------------------------------------


def test_a_failed_initializer_records_no_envelope(tmp_path, clean_genre_registries):
    """Written after the seed: an envelope certifies a *completed* init."""
    _demo_genre()

    def _boom(genre, template, project, params):
        raise RuntimeError("boom")

    register_genre_initializer("env_demo", _boom)
    proj = nw.Project.init(tmp_path / "p")
    with pytest.raises(RuntimeError, match="boom"):
        nw.initialize_genre("env_demo", proj)

    assert proj.resolved_genre() is None


def test_non_project_stand_ins_still_work(clean_genre_registries):
    """The genre machinery runs against doubles (object(), project=None) —
    persistence is duck-typed on ``project.graph``, never a crash."""
    _demo_genre()
    nw.initialize_genre("env_demo", object())  # must not raise


# --- the envelope is bookkeeping, not content --------------------------------


def test_spec_writes_do_not_eat_the_envelope(tmp_path, clean_genre_registries):
    """``write_spec`` reconciles every entity tier on any spec write; the
    envelope is not an entity and must survive."""
    _demo_genre()
    proj = nw.Project.init(tmp_path / "p")
    nw.initialize_genre("env_demo", proj)

    proj.set_title("retitled")

    assert proj.resolved_genre() == {
        "genre": "env_demo",
        "template": None,
        "params": {"format_id": "solo"},
    }


def test_envelope_never_shadows_the_last_authored_change(
    tmp_path, clean_genre_registries
):
    """The envelope is parentless and written at creation — without the
    bookkeeping exclusion it would *be* the newest authored annotation and
    the resumption brief's downstream walk would start from it (empty)."""
    _demo_genre()
    proj = nw.Project.init(tmp_path / "p")
    proj.upsert_section(SectionSpec(id="v", start_s=0.0, end_s=4.0))
    section_id = str(proj.graph.sections()[0].annotation_id)

    nw.initialize_genre("env_demo", proj)  # written AFTER the section

    brief = proj.resumption_brief()
    assert brief.last_authored_change_id == section_id
