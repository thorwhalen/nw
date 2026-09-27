"""Tests for the genre-ops registry in :mod:`nw.genres`.

The registry a host reads to SERVE a genre's project operations without importing
the genre's package: :class:`nw.GenreOp` rows (validated at construction, including
that every parameter is JSON-describable), :func:`nw.register_genre_ops`, the lookups
and the pure-JSON :func:`nw.genre_ops_catalogue`.
"""

import json
from typing import Literal, Optional

import pytest
from pydantic import ValidationError

import nw
from nw.genres import genre_ops_registry


def _status(project) -> dict:
    """Report the project's state."""
    return {"root": project}


def _cut(
    project,
    *,
    edit_id: str,
    index: int,
    clip_id: Optional[str] = None,
    into: Literal["previous", "next"] = "previous",
) -> dict:
    """Change one cut."""
    return {"edit_id": edit_id, "index": index, "clip_id": clip_id, "into": into}


@pytest.fixture
def clean_ops():
    """Snapshot + restore the process-global ops registry."""
    before = dict(genre_ops_registry)
    try:
        yield
    finally:
        for key in list(genre_ops_registry.keys()):
            del genre_ops_registry[key]
        for key, value in before.items():
            genre_ops_registry.register(key, value)


def test_op_row_defaults_and_schema():
    op = nw.GenreOp("set_cut", _cut, title="Change a cut")
    assert op.description == "Change one cut."
    assert (op.effect, op.runs) == ("write", "now")
    schema = op.params_schema
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"edit_id", "index", "clip_id", "into"}
    assert sorted(schema["required"]) == ["edit_id", "index"]
    assert schema["properties"]["into"]["enum"] == ["previous", "next"]


def test_op_call_validates_and_passes_only_given_params():
    op = nw.GenreOp("set_cut", _cut, title="Change a cut")
    assert op.run("P", {"edit_id": "e", "index": "2"}) == {
        "edit_id": "e",
        "index": 2,
        "clip_id": None,
        "into": "previous",
    }
    with pytest.raises(ValidationError):
        op.run("P", {"edit_id": "e"})  # missing index
    with pytest.raises(ValidationError):
        op.run("P", {"edit_id": "e", "index": 1, "colour": "red"})  # unknown param
    with pytest.raises(ValueError):  # ValidationError IS a ValueError
        op.validate_params({"edit_id": "e", "index": 1, "into": "sideways"})


def test_explicit_description_wins_over_docstring():
    op = nw.GenreOp("status", _status, title="Show", description="Model-facing text")
    assert op.description == "Model-facing text"


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"name": "SetCut"}, "snake_case"),
        ({"name": "set-cut"}, "snake_case"),
        ({"title": " "}, "title"),
        ({"effect": "delete"}, "effect"),
        ({"runs": "later"}, "runs"),
    ],
)
def test_bad_rows_refused(kwargs, match):
    row = {"name": "status", "fn": _status, "title": "Show"} | kwargs
    with pytest.raises(ValueError, match=match):
        nw.GenreOp(**row)


def test_destroy_is_an_effect():
    assert "destroy" in nw.GENRE_OP_EFFECTS
    op = nw.GenreOp("status", _status, title="Remove it", effect="destroy")
    assert op.effect == "destroy"


def test_non_json_params_fail_at_registration_not_call_time():
    def _unannotated(project, *, x):
        return {}

    def _varkw(project, **kw: int):
        return {}

    def _opaque(project, *, thing: object.__class__):  # `type` has no JSON schema
        return {}

    def _no_project():
        return {}

    for fn, match in [
        (_unannotated, "no annotation"),
        (_varkw, r"\*\*kw"),
        (_opaque, "not JSON-describable"),
        (_no_project, "first positional"),
    ]:
        with pytest.raises(TypeError, match=match):
            nw.GenreOp("op", fn, title="Op")


def test_register_lookup_and_catalogue(clean_ops):
    ops = nw.register_genre_ops(
        "_demo_genre",
        [
            nw.GenreOp("status", _status, title="Show", effect="read"),
            nw.GenreOp("set_cut", _cut, title="Change a cut", runs="job"),
        ],
    )
    assert isinstance(ops, tuple)
    assert [op.name for op in nw.genre_ops("_demo_genre")] == ["status", "set_cut"]
    assert nw.genre_op("_demo_genre", "status").run("R") == {"root": "R"}
    catalogue = nw.genre_ops_catalogue("_demo_genre")
    assert json.loads(json.dumps(catalogue)) == catalogue  # pure JSON
    assert [row["name"] for row in catalogue] == ["status", "set_cut"]
    assert set(catalogue[1]) == {
        "name",
        "title",
        "description",
        "effect",
        "runs",
        "params_schema",
        "host_params",
    }
    assert catalogue[1]["runs"] == "job"


def test_unknown_genre_and_unknown_op(clean_ops):
    assert nw.genre_ops("_nobody") == ()
    assert nw.genre_ops_catalogue("_nobody") == []
    nw.register_genre_ops(
        "_demo_genre", [nw.GenreOp("status", _status, title="Show", effect="read")]
    )
    with pytest.raises(nw.UnknownGenreOpError) as info:
        nw.genre_op("_demo_genre", "explode")
    assert isinstance(info.value, KeyError)
    assert "explode" in str(info.value) and "['status']" in str(info.value)


def test_register_refuses_duplicates_conflicts_and_non_rows(clean_ops):
    row = nw.GenreOp("status", _status, title="Show")
    with pytest.raises(ValueError, match="duplicate"):
        nw.register_genre_ops("_demo_genre", [row, row])
    with pytest.raises(TypeError, match="GenreOp"):
        nw.register_genre_ops("_demo_genre", [{"name": "status"}])
    nw.register_genre_ops("_demo_genre", [row])
    with pytest.raises(Exception):
        nw.register_genre_ops("_demo_genre", [row])  # on_conflict="error"
    with pytest.raises(ValueError):
        nw.register_genre_ops("bad slug", [row])


def _ingest(project, *, path: str, filename: str = "", name: str = "") -> dict:
    """Take an uploaded file."""
    return {"path": path, "filename": filename, "name": name}


def test_host_params_are_not_client_params():
    op = nw.GenreOp(
        "ingest", _ingest, title="Add a file", host_params=("path", "filename")
    )
    assert set(op.params_schema["properties"]) == {"name"}
    assert op.to_dict()["host_params"] == ["path", "filename"]
    # a client naming a server file is a validation error, not a file read
    with pytest.raises(ValidationError):
        op.run("P", {"path": "/etc/passwd"}, host={"path": "/tmp/up"})
    assert op.run("P", {"name": "n"}, host={"path": "/tmp/up"}) == {
        "path": "/tmp/up",
        "filename": "",
        "name": "n",
    }


def test_host_params_are_checked_at_construction_and_at_run():
    with pytest.raises(TypeError, match="not a keyword parameter"):
        nw.GenreOp("ingest", _ingest, title="Add", host_params=("nope",))
    op = nw.GenreOp("ingest", _ingest, title="Add", host_params=("path",))
    with pytest.raises(TypeError, match="needs host parameter"):
        op.run("P", {})
    with pytest.raises(TypeError, match="takes no host parameter"):
        op.run("P", {}, host={"path": "/tmp/up", "filename": "x"})
    plain = nw.GenreOp("status", _status, title="Show")
    assert plain.host_params == () and plain.to_dict()["host_params"] == []
