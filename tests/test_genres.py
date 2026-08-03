"""Tests for :mod:`nw.genres` — the production-genre registry.

Covers the pure-data :class:`nw.Genre` descriptor (construction + validation)
and the shared registry facade (register / get / list, conflict policy, and
the substrate-readiness helpers). The registry is process-global, so tests
that mutate it snapshot and restore it via the ``clean_registry`` fixture.
"""

import pytest

import nw
from nw.genres import (
    Genre,
    Template,
    genres,
    get_genre,
    list_genres,
    register_genre,
    genre_catalog,
    describe_genre,
    recommend_genre,
    resolve_defaults,
    genre_resolvers,
    register_genre_resolver,
    resolve_genre,
    genre_initializers,
    register_genre_initializer,
    initialize_genre,
    genre_project_factories,
    register_genre_project_factory,
    has_genre_project_factory,
    create_genre_project,
)


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
    assert nw.Template is Template
    for name in ("genre_catalog", "describe_genre", "recommend_genre", "resolve_defaults"):
        assert callable(getattr(nw, name))


# --- Template (AV-general preset within a genre) ----------------------------


def test_template_construction_and_to_dict():
    t = Template(
        slug="cinematic_clip",
        title="Cinematic clip",
        description="filmic look",
        params={"output_intent": "animatic", "flavor": "fal.cinematic"},
    )
    assert t.params["flavor"] == "fal.cinematic"
    assert t.to_dict() == {
        "slug": "cinematic_clip",
        "title": "Cinematic clip",
        "description": "filmic look",
        "params": {"output_intent": "animatic", "flavor": "fal.cinematic"},
    }


def test_template_validation_and_immutability():
    with pytest.raises(ValueError):
        Template(slug="", title="x")
    with pytest.raises(ValueError):
        Template(slug="has space", title="x")
    with pytest.raises(ValueError):
        Template(slug="ok", title="  ")
    t = Template(slug="ok", title="Ok", params={"a": 1})
    with pytest.raises(TypeError):  # params normalized to a read-only mapping
        t.params["b"] = 2
    # frozen + hashable even with a params payload
    assert isinstance(hash(t), int)


# --- Genre: templates / intake_kinds / cost_profile / defaults --------------


def _av_genre(**kw) -> Genre:
    base = dict(
        slug="av_demo",
        title="AV Demo",
        intake_kinds=("podcast",),
        cost_profile="tts",
        defaults={"format_id": "solo"},
        templates=(
            Template(slug="solo", title="Solo", params={"format_id": "solo"}),
            Template(slug="duo", title="Duo", params={"format_id": "duo"}),
        ),
    )
    base.update(kw)
    return Genre(**base)


def test_genre_with_templates_stays_hashable():
    g = _av_genre()
    assert isinstance(hash(g), int) and {g} == {g}
    assert g.list_templates() == ["solo", "duo"]
    assert g.template("duo").params["format_id"] == "duo"
    with pytest.raises(KeyError):
        g.template("missing")


def test_genre_normalizes_list_sequence_fields_to_tuple():
    # A Genre built with LISTS (the natural `templates=[Template(...) for ...]`)
    # must still be genuinely frozen + hashable — sequence fields are coerced to
    # tuple, symmetrically with the Mapping fields.
    g = Genre(
        slug="listy",
        title="Listy",
        transform_names=["t1"],
        templates=[Template(slug="a", title="A"), Template(slug="b", title="B")],
        intake_kinds=["essay"],
    )
    assert isinstance(g.templates, tuple) and isinstance(g.intake_kinds, tuple)
    assert isinstance(g.transform_names, tuple)
    assert isinstance(hash(g), int) and {g} == {g}  # hashable, set-usable


def test_genre_rejects_duplicate_template_slugs():
    with pytest.raises(ValueError):
        Genre(
            slug="d",
            title="D",
            templates=(
                Template(slug="x", title="X"),
                Template(slug="x", title="X2"),
            ),
        )


def test_genre_rejects_non_template_and_bad_intake_and_cost():
    with pytest.raises(ValueError):
        Genre(slug="d", title="D", templates=("not a template",))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Genre(slug="d", title="D", intake_kinds=("ok", "  "))
    with pytest.raises(ValueError):
        Genre(slug="d", title="D", cost_profile="   ")


def test_genre_defaults_are_immutable():
    g = _av_genre()
    assert g.defaults["format_id"] == "solo"
    with pytest.raises(TypeError):
        g.defaults["x"] = 1


def test_genre_to_dict_shape():
    d = _av_genre().to_dict()
    assert d["slug"] == "av_demo" and d["intake_kinds"] == ["podcast"]
    assert d["cost_profile"] == "tts" and d["defaults"] == {"format_id": "solo"}
    assert [t["slug"] for t in d["templates"]] == ["solo", "duo"]
    assert d["templates"][0]["params"] == {"format_id": "solo"}
    assert set(d) == {
        "slug", "title", "description", "status", "ready",
        "intake_kinds", "cost_profile", "defaults", "templates",
    }


# --- generic catalog / recommend / resolve ----------------------------------


def test_genre_catalog_and_describe(clean_registry):
    register_genre(_av_genre(slug="cat_demo"))
    cat = genre_catalog()
    entry = next(e for e in cat if e["slug"] == "cat_demo")
    assert entry == describe_genre("cat_demo")
    with pytest.raises(KeyError):
        describe_genre("no_such_genre_xyz")


def test_recommend_genre(clean_registry):
    register_genre(_av_genre(slug="rec_demo", intake_kinds=("podcast", "audio-essay")))
    assert recommend_genre("audio-essay") == "rec_demo"
    assert recommend_genre("nope") is None
    assert recommend_genre(None) is None


def test_resolve_defaults_with_and_without_template(clean_registry):
    register_genre(_av_genre(slug="res_demo"))
    scratch = resolve_defaults("res_demo")
    assert scratch == {"genre": "res_demo", "template": None, "params": {"format_id": "solo"}}
    picked = resolve_defaults("res_demo", "duo")
    assert picked == {"genre": "res_demo", "template": "duo", "params": {"format_id": "duo"}}
    with pytest.raises(KeyError):
        resolve_defaults("res_demo", "missing")


# --- genre resolver registry (nw#19) ---------------------------------------


@pytest.fixture
def clean_resolvers():
    """Snapshot + restore BOTH the genre registry and the resolver registry."""
    genres_before = dict(genres)
    resolvers_before = dict(genre_resolvers)
    try:
        yield
    finally:
        for reg, before in ((genres, genres_before), (genre_resolvers, resolvers_before)):
            for key in list(reg.keys()):
                del reg[key]
            for key, value in before.items():
                reg.register(key, value)


def test_resolve_genre_dispatches_and_wraps_in_envelope(clean_resolvers):
    register_genre(_av_genre(slug="disp_demo"))
    seen = {}

    def _resolver(genre, template):
        seen["genre_obj"] = genre
        seen["template"] = template
        return {"custom": "applied"}  # resolvers return ONLY the bare params

    assert register_genre_resolver("disp_demo", _resolver) is _resolver
    # resolve_genre adds the standard {genre, template, params} envelope
    assert resolve_genre("disp_demo", "duo") == {
        "genre": "disp_demo",
        "template": "duo",
        "params": {"custom": "applied"},
    }
    # the resolver received the Genre OBJECT + the template slug
    assert seen["genre_obj"] is get_genre("disp_demo") and seen["template"] == "duo"


def test_resolve_genre_validates_template_uniformly(clean_resolvers):
    # A bogus template must KeyError whether or not a resolver is registered — the
    # substrate owns template identity; a resolver only interprets params.
    register_genre(_av_genre(slug="tval_res"))
    register_genre_resolver("tval_res", lambda g, t: {"picked": t})
    with pytest.raises(KeyError):
        resolve_genre("tval_res", "totally_bogus_template")
    register_genre(_av_genre(slug="tval_nores"))  # no resolver
    with pytest.raises(KeyError):
        resolve_genre("tval_nores", "totally_bogus_template")


def test_resolve_genre_falls_back_to_generic_params(clean_resolvers):
    register_genre(_av_genre(slug="fallback_demo"))  # no resolver registered
    assert resolve_genre("fallback_demo") == {
        "genre": "fallback_demo",
        "template": None,
        "params": {"format_id": "solo"},
    }
    assert resolve_genre("fallback_demo", "duo")["params"] == {"format_id": "duo"}


def test_resolve_genre_unknown_genre_raises():
    with pytest.raises(KeyError):
        resolve_genre("no_such_genre_at_all_xyz")


def test_register_genre_resolver_validates_slug_type_and_conflict(clean_resolvers):
    with pytest.raises(TypeError):
        register_genre_resolver("x", "not callable")  # type: ignore[arg-type]
    for bad in ("", "   ", "has space"):
        with pytest.raises(ValueError):  # slug guard, like Genre/Template
            register_genre_resolver(bad, lambda g, t: {})
    register_genre_resolver("dup_resolver", lambda g, t: {})
    with pytest.raises(Exception):  # xdol RegistryConflict (on_conflict="error")
        register_genre_resolver("dup_resolver", lambda g, t: {})


def test_full_genre_public_surface_exposed_on_nw():
    # Every name in the genres module's __all__ is re-exported at the nw top level
    # (catches export-symmetry gaps like GenreResolver / GENRE_STATUSES being
    # module-only). Reach the module via sys.modules — the `nw.genres` *attribute*
    # is the registry (it deliberately shadows the submodule).
    import sys

    _g = sys.modules["nw.genres"]
    missing = [n for n in _g.__all__ if not hasattr(nw, n)]
    assert missing == [], f"nw.genres.__all__ not re-exported by nw: {missing}"
    assert nw.GenreResolver is _g.GenreResolver
    assert nw.register_genre_resolver is register_genre_resolver
    assert nw.resolve_genre is resolve_genre and nw.genre_resolvers is genre_resolvers
    # the initializer twins (nw#229/reelee#230) are re-exported too
    assert nw.GenreInitializer is _g.GenreInitializer
    assert nw.register_genre_initializer is register_genre_initializer
    assert nw.initialize_genre is initialize_genre
    assert nw.genre_initializers is genre_initializers


# --- genre initializer registry (reelee#230) -------------------------------


@pytest.fixture
def clean_initializers():
    """Snapshot + restore BOTH the genre registry and the initializer registry."""
    genres_before = dict(genres)
    inits_before = dict(genre_initializers)
    try:
        yield
    finally:
        for reg, before in (
            (genres, genres_before),
            (genre_initializers, inits_before),
        ):
            for key in list(reg.keys()):
                del reg[key]
            for key, value in before.items():
                reg.register(key, value)


def test_initialize_genre_dispatches_with_full_context(clean_initializers):
    # The initializer is the true twin of the resolver: it receives the Genre
    # OBJECT + template slug (the resolver's context) PLUS the project + resolved
    # params — so a genre can seed folder_conventions / record its template lineage.
    register_genre(_av_genre(slug="init_disp"))
    seen = {}

    def _init(genre, template, project, params):
        seen["args"] = (genre, template, project, params)

    assert register_genre_initializer("init_disp", _init) is _init
    sentinel_project = object()
    initialize_genre(
        "init_disp", sentinel_project, template="duo", params={"format_id": "duo"}
    )
    genre_obj, template, project, params = seen["args"]
    assert genre_obj is get_genre("init_disp")
    assert template == "duo"
    assert project is sentinel_project
    assert params == {"format_id": "duo"}


def test_initialize_genre_noop_when_unregistered(clean_initializers):
    # A genre that seeds nothing on create registers NO initializer -> no-op (the
    # load-bearing default: braidio applies its format at render time, not create).
    register_genre(_av_genre(slug="init_noop"))
    initialize_genre("init_noop", object())  # must not raise, must do nothing
    assert "init_noop" not in genre_initializers


def test_initialize_genre_resolves_params_when_none(clean_initializers):
    # params=None -> resolved from the genre's template/defaults (one-call seed).
    register_genre(_av_genre(slug="init_resolve"))
    seen = {}
    register_genre_initializer(
        "init_resolve", lambda g, t, p, params: seen.update(params=params, t=t)
    )
    initialize_genre("init_resolve", object())  # defaults
    assert seen == {"params": {"format_id": "solo"}, "t": None}
    seen.clear()
    initialize_genre("init_resolve", object(), template="duo")  # from a template
    assert seen == {"params": {"format_id": "duo"}, "t": "duo"}


def test_initialize_genre_validates_genre_and_template(clean_initializers):
    with pytest.raises(KeyError):  # unknown genre
        initialize_genre("no_such_genre_xyz", object())
    register_genre(_av_genre(slug="init_tval"))
    register_genre_initializer("init_tval", lambda g, t, p, params: None)
    # unknown template KeyErrors uniformly — whether params is resolved here (None)
    # or supplied by the caller (already-resolved).
    with pytest.raises(KeyError):
        initialize_genre("init_tval", object(), template="bogus")
    with pytest.raises(KeyError):
        initialize_genre(
            "init_tval", object(), template="bogus", params={"format_id": "x"}
        )


def test_register_genre_initializer_validates_slug_type_and_conflict(
    clean_initializers,
):
    with pytest.raises(TypeError):
        register_genre_initializer("x", "not callable")  # type: ignore[arg-type]
    for bad in ("", "   ", "has space"):
        with pytest.raises(ValueError):  # slug guard, like Genre/Template/resolver
            register_genre_initializer(bad, lambda g, t, p, params: None)
    register_genre_initializer("dup_init", lambda g, t, p, params: None)
    with pytest.raises(Exception):  # xdol RegistryConflict (on_conflict="error")
        register_genre_initializer("dup_init", lambda g, t, p, params: None)


# --- genre project-factory registry (braidio#18) ---------------------------


@pytest.fixture
def clean_factories():
    """Snapshot + restore the genre + project-factory + initializer registries."""
    snaps = [
        (genres, dict(genres)),
        (genre_project_factories, dict(genre_project_factories)),
        (genre_initializers, dict(genre_initializers)),
    ]
    try:
        yield
    finally:
        for reg, before in snaps:
            for key in list(reg.keys()):
                del reg[key]
            for key, value in before.items():
                reg.register(key, value)


def test_create_genre_project_orchestrates_resolve_factory_initialize(clean_factories):
    register_genre(_av_genre(slug="pf_disp"))  # params {format_id} from templates
    seen = {}
    seeded = {}

    def _factory(caller, project_id, *, title, template, params):
        seen.update(caller=caller, project_id=project_id, title=title, params=params)
        return {"project": None, "project_id": project_id, "title": title}

    register_genre_project_factory("pf_disp", _factory)
    register_genre_initializer(
        "pf_disp", lambda g, t, p, params: seeded.update(params=params)
    )
    out = create_genre_project(
        "pf_disp", "u@x.com", "myproj", title="My Proj", template="duo"
    )
    assert seen == {
        "caller": "u@x.com", "project_id": "myproj",
        "title": "My Proj", "params": {"format_id": "duo"},
    }
    assert seeded == {"params": {"format_id": "duo"}}  # initializer ran
    # returns the factory info (minus the live project) + the resolved envelope
    assert out == {
        "project_id": "myproj", "title": "My Proj",
        "genre": "pf_disp", "template": "duo", "params": {"format_id": "duo"},
    }


def test_create_genre_project_title_defaults_to_project_id(clean_factories):
    register_genre(_av_genre(slug="pf_title"))
    got = {}
    register_genre_project_factory(
        "pf_title",
        lambda caller, pid, *, title, template, params: got.update(title=title) or {"project": None},
    )
    create_genre_project("pf_title", "u@x.com", "pid1")
    assert got["title"] == "pid1"


def test_create_genre_project_rolls_back_on_initializer_failure(tmp_path, clean_factories):
    # All-or-nothing: a failing initializer removes the just-created project (root).
    register_genre(_av_genre(slug="pf_boom"))
    proj_root = tmp_path / "made"
    proj_root.mkdir()

    class _Proj:
        root = str(proj_root)

    register_genre_project_factory(
        "pf_boom", lambda caller, pid, *, title, template, params: {"project": _Proj()}
    )
    register_genre_initializer(
        "pf_boom", lambda g, t, p, params: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    with pytest.raises(RuntimeError, match="boom"):
        create_genre_project("pf_boom", "u@x.com", "made")
    assert not proj_root.exists()  # rolled back


def test_create_genre_project_unknown_or_no_factory_raises(clean_factories):
    with pytest.raises(KeyError):
        create_genre_project("no_such_genre_xyz", "u@x.com", "p")
    register_genre(_av_genre(slug="pf_nofactory"))  # registered genre, NO factory
    with pytest.raises(KeyError, match="no project factory"):
        create_genre_project("pf_nofactory", "u@x.com", "p")


def test_register_genre_project_factory_validates_and_has(clean_factories):
    with pytest.raises(TypeError):
        register_genre_project_factory("x", "not callable")  # type: ignore[arg-type]
    for bad in ("", "  ", "has space"):
        with pytest.raises(ValueError):
            register_genre_project_factory(bad, lambda *a, **k: {})
    assert has_genre_project_factory("pf_absent") is False
    register_genre_project_factory("pf_present", lambda *a, **k: {"project": None})
    assert has_genre_project_factory("pf_present") is True
    with pytest.raises(Exception):  # RegistryConflict
        register_genre_project_factory("pf_present", lambda *a, **k: {})


def test_project_factory_symbols_reexported_on_nw():
    for name in (
        "GenreProjectFactory", "genre_project_factories",
        "register_genre_project_factory", "has_genre_project_factory",
        "create_genre_project",
    ):
        assert hasattr(nw, name), name
