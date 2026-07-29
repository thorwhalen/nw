"""Tests for :mod:`nw.genres` — the production-genre registry.

Covers the pure-data :class:`nw.Genre` descriptor (construction + validation)
and the shared registry facade (register / get / list, conflict policy, and
the substrate-readiness helpers). The registry is process-global, so tests
that mutate it snapshot and restore it via the ``clean_registry`` fixture.
"""

import pytest

import nw
from nw.genres import Genre, genres, get_genre, list_genres, register_genre


@pytest.fixture
def clean_registry():
    """Snapshot + restore the shared genre registry around a test."""
    before = dict(genres)
    try:
        yield
    finally:
        for key in list(genres.keys()):
            del genres[key]
        for key, value in before.items():
            genres.register(key, value)


# --- Genre: pure-data construction + validation ----------------------------


def test_genre_is_pure_data():
    g = Genre(
        slug="demo",
        title="Demo",
        description="d",
        transform_names=("t1",),
        projection_entrypoint="t1",
    )
    assert g.slug == "demo"
    assert g.status == "available"
    assert g.projection_entrypoint == "t1"


def test_slug_required():
    with pytest.raises(ValueError):
        Genre(slug="", title="x")


def test_bad_status_rejected():
    with pytest.raises(ValueError):
        Genre(slug="d", title="x", status="nope")


def test_projection_entrypoint_must_be_declared():
    with pytest.raises(ValueError):
        Genre(slug="d", title="x", projection_entrypoint="ghost")
    # OK when the entrypoint is one of the declared transforms/strategies.
    Genre(slug="d", title="x", strategy_names=("s",), projection_entrypoint="s")


def test_genre_is_hashable_and_set_usable():
    # A frozen dataclass with a dict field would raise on hash(); the descriptor
    # must stay hashable so it can be deduped / used as a set member.
    g1 = Genre(slug="a", title="A")
    g2 = Genre(slug="a", title="A")
    assert hash(g1) == hash(g2)
    assert {g1, g2} == {g1}
    with_conventions = Genre(
        slug="b", title="B", folder_conventions={"sources": "sources/"}
    )
    assert isinstance(hash(with_conventions), int)


def test_folder_conventions_are_immutable():
    g = Genre(slug="c", title="C", folder_conventions={"sources": "sources/"})
    assert g.folder_conventions["sources"] == "sources/"
    # folder_conventions is normalized to a read-only MappingProxyType.
    with pytest.raises(TypeError):
        g.folder_conventions["injected"] = "nope"


def test_whitespace_slug_rejected():
    with pytest.raises(ValueError):
        Genre(slug="   ", title="x")
    with pytest.raises(ValueError):
        Genre(slug="has space", title="x")


def test_empty_title_rejected():
    with pytest.raises(ValueError):
        Genre(slug="ok", title="   ")


# --- registry facade -------------------------------------------------------


def test_register_get_list(clean_registry):
    g = register_genre(Genre(slug="unit_demo", title="Unit Demo"))
    assert get_genre("unit_demo") is g
    assert "unit_demo" in list_genres()
    assert list_genres() == sorted(list_genres())


def test_register_conflict_raises(clean_registry):
    register_genre(Genre(slug="dupe", title="A"))
    with pytest.raises(Exception):  # xdol RegistryConflict (on_conflict="error")
        register_genre(Genre(slug="dupe", title="B"))


def test_register_type_checked(clean_registry):
    with pytest.raises(TypeError):
        register_genre("not a genre")  # type: ignore[arg-type]


def test_get_unknown_raises():
    with pytest.raises(KeyError):
        get_genre("does_not_exist_xyz")


# --- substrate-readiness helpers -------------------------------------------


def test_missing_transforms_and_readiness():
    unwired = Genre(
        slug="wiring",
        title="Wiring",
        transform_names=("definitely.not.registered",),
    )
    assert unwired.missing_transforms() == ["definitely.not.registered"]
    assert not unwired.is_ready()

    # Positive case: reference a strategy that is actually registered (pick one
    # dynamically so the test doesn't hardcode a built-in name).
    existing = nw.list_strategies()
    assert existing, "expected nw to ship built-in render strategies"
    a_strategy = existing[0]
    real = Genre(
        slug="real",
        title="Real",
        strategy_names=(a_strategy,),
        projection_entrypoint=a_strategy,
    )
    assert real.missing_strategies() == []
    assert real.is_ready()


# --- public surface on the nw namespace ------------------------------------


def test_exposed_on_nw_namespace():
    assert nw.Genre is Genre
    assert nw.register_genre is register_genre
    assert nw.get_genre is get_genre
    assert callable(nw.list_genres)
