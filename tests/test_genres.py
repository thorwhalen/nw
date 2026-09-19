"""Tests for :mod:`nw.genres` — the production-genre registry.

Covers the pure-data :class:`nw.Genre` descriptor (construction + validation)
and the shared registry facade (register / get / list, conflict policy, and
the substrate-readiness helpers). The registry is process-global, so tests
that mutate it snapshot and restore it via the ``clean_registry`` fixture.
"""

from pathlib import Path

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
    for name in (
        "genre_catalog",
        "describe_genre",
        "recommend_genre",
        "resolve_defaults",
    ):
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
        "slug",
        "title",
        "description",
        "status",
        "ready",
        "intake_kinds",
        "cost_profile",
        "defaults",
        "templates",
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
    assert scratch == {
        "genre": "res_demo",
        "template": None,
        "params": {"format_id": "solo"},
    }
    picked = resolve_defaults("res_demo", "duo")
    assert picked == {
        "genre": "res_demo",
        "template": "duo",
        "params": {"format_id": "duo"},
    }
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
        for reg, before in (
            (genres, genres_before),
            (genre_resolvers, resolvers_before),
        ):
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
        "caller": "u@x.com",
        "project_id": "myproj",
        "title": "My Proj",
        "params": {"format_id": "duo"},
    }
    assert seeded == {"params": {"format_id": "duo"}}  # initializer ran
    # returns the factory info (minus the live project) + the resolved envelope
    assert out == {
        "project_id": "myproj",
        "title": "My Proj",
        "genre": "pf_disp",
        "template": "duo",
        "params": {"format_id": "duo"},
    }


def test_create_genre_project_title_defaults_to_project_id(clean_factories):
    register_genre(_av_genre(slug="pf_title"))
    got = {}
    register_genre_project_factory(
        "pf_title",
        lambda caller, pid, *, title, template, params: (
            got.update(title=title) or {"project": None}
        ),
    )
    create_genre_project("pf_title", "u@x.com", "pid1")
    assert got["title"] == "pid1"


def test_create_genre_project_rolls_back_on_initializer_failure(
    tmp_path, clean_factories
):
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
        "GenreProjectFactory",
        "genre_project_factories",
        "register_genre_project_factory",
        "has_genre_project_factory",
        "create_genre_project",
    ):
        assert hasattr(nw, name), name


# --- placement: a factory places a project where its CALLER asks ------------
#
# The rule these pin: *a genre project factory places a project where its caller
# asks; it does not own the location.* Before ``projects_dir`` the factory decided
# storage, so a host that had to SERVE the project got one under the guest app's own
# data home — a sibling of nothing the host could address. The tests below are
# ordered by what each one would miss if it were the only one.


class _RootOnly:
    """The minimum a factory's returned project has to be for nw: a ``root``."""

    def __init__(self, root):
        self.root = root


def _placing_factory(record):
    """A factory that honours placement, recording what it was handed."""

    def factory(caller, project_id, *, title, template, params, projects_dir=None):
        record["projects_dir"] = projects_dir
        root = (
            Path(projects_dir) if projects_dir else Path(record["fallback"])
        ) / project_id
        root.mkdir(parents=True, exist_ok=True)

        class _Proj:
            pass

        p = _Proj()
        p.root = str(root)
        return {"project": p, "project_id": project_id}

    return factory


def test_can_place_genre_project_reads_the_signature(clean_factories):
    def old(caller, project_id, *, title, template, params):
        return {"project": None}

    def new(caller, project_id, *, title, template, params, projects_dir=None):
        return {"project": None}

    def kwargsy(caller, project_id, **kwargs):
        return {"project": None}

    register_genre_project_factory("pl_old", old)
    register_genre_project_factory("pl_new", new)
    register_genre_project_factory("pl_kw", kwargsy)
    assert nw.can_place_genre_project("pl_old") is False
    assert nw.can_place_genre_project("pl_new") is True
    # ``**kwargs`` is the only honest reading of the signature — the OUTCOME check
    # is what catches one that takes the argument and ignores it.
    assert nw.can_place_genre_project("pl_kw") is True
    assert nw.can_place_genre_project("pl_unregistered") is False


def test_no_placement_asked_leaves_a_pre_placement_factory_untouched(
    tmp_path, clean_factories
):
    """The compatibility guarantee: every existing caller AND factory keeps working.

    A pre-placement factory is called with the pre-placement argument list — passing
    ``projects_dir=None`` to it would be a ``TypeError`` on the create path, which is
    the break this adaptation exists to avoid.
    """
    register_genre(_av_genre(slug="pl_compat"))
    got = {}

    def old(caller, project_id, *, title, template, params):
        got.update(caller=caller, project_id=project_id)
        return {"project": None, "project_id": project_id}

    register_genre_project_factory("pl_compat", old)
    out = create_genre_project("pl_compat", "u@x.com", "p1")
    assert out["project_id"] == "p1"
    assert got == {"caller": "u@x.com", "project_id": "p1"}


def test_placement_reaches_the_factory_and_the_project_lands_there(
    tmp_path, clean_factories
):
    register_genre(_av_genre(slug="pl_here"))
    record = {"fallback": tmp_path / "guest_app_home"}
    register_genre_project_factory("pl_here", _placing_factory(record))
    host_dir = tmp_path / "host" / "projects" / "u@x.com"
    host_dir.mkdir(parents=True)

    out = create_genre_project("pl_here", "u@x.com", "p1", projects_dir=host_dir)
    assert out["project_id"] == "p1"
    assert record["projects_dir"] == host_dir
    # The point: a DIRECT child of the directory the host enumerates.
    assert (host_dir / "p1").is_dir()


def test_a_pre_placement_factory_refuses_a_placement_before_touching_disk(
    tmp_path, clean_factories
):
    """Refusal, never silent misplacement — and refusal BEFORE any side effect.

    The negative control for the whole feature: a version that simply dropped the
    argument would pass every other test here, create the project in the guest app's
    own home, and report success — which is exactly the two-worlds failure.
    """
    register_genre(_av_genre(slug="pl_refuse"))
    guest_home = tmp_path / "guest_app_home"
    made = []

    def old(caller, project_id, *, title, template, params):
        root = guest_home / project_id
        root.mkdir(parents=True)
        made.append(root)
        return {"project": None}

    register_genre_project_factory("pl_refuse", old)
    host_dir = tmp_path / "host"
    host_dir.mkdir()
    # The message is part of the contract, not decoration: without the explicit
    # check the call still fails — with Python's raw binding TypeError, which tells
    # a host nothing about what to do. Matching on the actionable half is what
    # distinguishes nw's refusal from an accident that happens to abort.
    with pytest.raises(TypeError, match="can_place_genre_project"):
        create_genre_project("pl_refuse", "u@x.com", "p1", projects_dir=host_dir)
    assert made == []  # never ran
    assert not guest_home.exists()


def test_a_factory_that_accepts_a_placement_and_ignores_it_is_refused(
    tmp_path, clean_factories
):
    """Acceptance is not the guarantee; the outcome is.

    A ``**kwargs`` factory, or one that declares the keyword and forgets to use it,
    produces a project that is fine on disk and invisible to the host that asked for
    it — indistinguishable, on every host surface, from a create that did nothing.

    The misplaced project is deliberately **left on disk**: see
    ``test_a_misplaced_project_outside_the_placement_is_not_deleted``.
    """
    register_genre(_av_genre(slug="pl_liar"))
    elsewhere = tmp_path / "elsewhere"

    def liar(caller, project_id, *, title, template, params, projects_dir=None):
        root = elsewhere / project_id
        root.mkdir(parents=True)
        return {"project": _RootOnly(root)}

    register_genre_project_factory("pl_liar", liar)
    host_dir = tmp_path / "host"
    host_dir.mkdir()
    with pytest.raises(RuntimeError, match="did not honour projects_dir"):
        create_genre_project("pl_liar", "u@x.com", "p1", projects_dir=host_dir)


def test_a_misplaced_project_outside_the_placement_is_not_deleted(
    tmp_path, clean_factories
):
    """The rollback is BOUNDED by the placement, and that is the point.

    A placement adds a second, much easier trigger for the all-or-nothing rollback —
    and it fires *precisely when nw has concluded it does not know what the factory
    did*. Recursively deleting a path nw does not understand, inside the **host's**
    tree, is not a rollback. The shape that makes this concrete: a factory that
    places correctly on disk and returns the wrong ``root`` (here, the host's whole
    per-caller projects directory).
    """
    register_genre(_av_genre(slug="pl_offbyone"))
    host_dir = tmp_path / "host" / "projects" / "u@x.com"
    host_dir.mkdir(parents=True)
    (host_dir / "someone_elses_film").mkdir()

    def off_by_one(caller, project_id, *, title, template, params, projects_dir=None):
        root = Path(projects_dir) / project_id
        root.mkdir(parents=True)
        # correct on disk, wrong in the report: the CONTAINER, not the project
        return {"project": _RootOnly(Path(projects_dir))}

    register_genre_project_factory("pl_offbyone", off_by_one)
    with pytest.raises(RuntimeError, match="did not honour projects_dir"):
        create_genre_project("pl_offbyone", "u@x.com", "p1", projects_dir=host_dir)
    assert host_dir.is_dir()
    assert (host_dir / "someone_elses_film").is_dir()  # NOT collateral damage


def test_a_misplacement_inside_the_placement_is_rolled_back(tmp_path, clean_factories):
    """The other side of the bound: what nw asked for, it may clean up."""
    register_genre(_av_genre(slug="pl_renamer"))
    host_dir = tmp_path / "host"
    host_dir.mkdir()

    def renamer(caller, project_id, *, title, template, params, projects_dir=None):
        root = Path(projects_dir) / f"braidio-{project_id}"
        root.mkdir(parents=True)
        return {"project": _RootOnly(root)}

    register_genre_project_factory("pl_renamer", renamer)
    with pytest.raises(RuntimeError, match="did not honour projects_dir"):
        create_genre_project("pl_renamer", "u@x.com", "p1", projects_dir=host_dir)
    assert not (host_dir / "braidio-p1").exists()
    assert host_dir.is_dir()


def test_the_basename_is_verified_because_the_host_addresses_by_it(
    tmp_path, clean_factories
):
    """Right parent, wrong name is a misplacement.

    ``create_genre_project`` drops the live project from its JSON result, so the host
    addresses the new project as ``projects_dir/<project_id>``. A check on the parent
    alone leaves the half the host actually relies on unverified.
    """
    register_genre(_av_genre(slug="pl_base"))
    host_dir = tmp_path / "host"
    host_dir.mkdir()

    def slugifier(caller, project_id, *, title, template, params, projects_dir=None):
        root = Path(projects_dir) / project_id.replace("_", "-")
        root.mkdir(parents=True)
        return {"project": _RootOnly(root)}

    register_genre_project_factory("pl_base", slugifier)
    with pytest.raises(RuntimeError, match="did not honour projects_dir"):
        create_genre_project("pl_base", "u@x.com", "my_show", projects_dir=host_dir)


def test_a_misplacement_that_shares_the_last_component_is_refused(
    tmp_path, clean_factories
):
    """The realistic misplacement: same tail, different data root.

    ``{braidio_home}/projects/{email}/`` against ``{reelee_home}/projects/{email}/``
    is the actual field shape, and it is exactly what a comparison on the last path
    component would wave through — reporting success while the project lands in the
    other world.
    """
    register_genre(_av_genre(slug="pl_tail"))
    host_dir = tmp_path / "reelee" / "projects" / "u@x.com"
    guest_dir = tmp_path / "braidio" / "projects" / "u@x.com"
    host_dir.mkdir(parents=True)

    def other_root(caller, project_id, *, title, template, params, projects_dir=None):
        root = guest_dir / project_id
        root.mkdir(parents=True)
        return {"project": _RootOnly(root)}

    register_genre_project_factory("pl_tail", other_root)
    with pytest.raises(RuntimeError, match="did not honour projects_dir"):
        create_genre_project("pl_tail", "u@x.com", "ep_01", projects_dir=host_dir)


def test_an_unverifiable_outcome_is_a_failure_not_a_pass(tmp_path, clean_factories):
    """A factory that accepts a placement and returns no project cannot be checked.

    The direction that silence resolves to must not be "success": a factory can
    accept the keyword (``**kwargs`` binds), ignore it, return nothing addressable,
    and otherwise pass every gate — reporting a create the host will never find.
    """
    register_genre(_av_genre(slug="pl_silent"))
    host_dir = tmp_path / "host"
    host_dir.mkdir()

    def silent(caller, project_id, **kwargs):
        return {"project": None, "project_id": project_id}

    register_genre_project_factory("pl_silent", silent)
    with pytest.raises(RuntimeError, match="returned no created project"):
        create_genre_project("pl_silent", "u@x.com", "p1", projects_dir=host_dir)


def test_placement_is_verified_before_the_project_is_seeded(tmp_path, clean_factories):
    """Order: check the outcome, THEN initialize.

    Seeding a misplaced project and deleting it afterwards writes annotations (and
    creates the graph store) somewhere nw is about to declare it does not understand.
    """
    register_genre(_av_genre(slug="pl_order"))
    seeded = []
    elsewhere = tmp_path / "elsewhere"

    def misplacer(caller, project_id, *, title, template, params, projects_dir=None):
        root = elsewhere / project_id
        root.mkdir(parents=True)
        return {"project": _RootOnly(root)}

    register_genre_project_factory("pl_order", misplacer)
    register_genre_initializer("pl_order", lambda g, t, p, params: seeded.append(p))
    host_dir = tmp_path / "host"
    host_dir.mkdir()
    with pytest.raises(RuntimeError, match="did not honour projects_dir"):
        create_genre_project("pl_order", "u@x.com", "p1", projects_dir=host_dir)
    assert seeded == []


@pytest.mark.parametrize("factory_resolves", [True, False], ids=["resolved", "raw"])
def test_a_symlinked_placement_is_not_a_misplacement(
    tmp_path, clean_factories, factory_resolves
):
    """Both sides resolve, and that is load-bearing rather than tidiness.

    ``nw.Project.__init__`` resolves its root, so a factory built on it reports a
    RESOLVED path while a host's ``projects_dir`` typically is not resolved; a
    hand-rolled factory reports back the raw path it was handed. Both spellings name
    the same directory, and without ``.resolve()`` on **both** sides one of the two
    is refused — and, before the rollback was bounded, deleted. Parametrised because
    the two directions are separate mutations: dropping either call alone still
    passes the other case.
    """
    register_genre(_av_genre(slug="pl_link"))
    real = tmp_path / "real_projects"
    real.mkdir()
    link = tmp_path / "link_projects"
    link.symlink_to(real, target_is_directory=True)

    def factory(caller, project_id, *, title, template, params, projects_dir=None):
        root = Path(projects_dir) / project_id
        root.mkdir(parents=True)
        reported = root.resolve() if factory_resolves else root
        return {"project": _RootOnly(reported), "project_id": project_id}

    register_genre_project_factory("pl_link", factory)
    out = create_genre_project("pl_link", "u@x.com", "p1", projects_dir=link)
    assert out["project_id"] == "p1"
    assert (real / "p1").is_dir()


def test_an_empty_placement_is_refused_rather_than_meaning_the_cwd(
    tmp_path, clean_factories
):
    """``Path("")`` is the process CWD, so a blank placement would write user data
    into wherever the server happens to be running — typically the deploy tree."""
    register_genre(_av_genre(slug="pl_blank"))
    register_genre_project_factory(
        "pl_blank", lambda c, pid, **kw: {"project": None, "project_id": pid}
    )
    for blank in ("", "   "):
        with pytest.raises(ValueError, match="is empty"):
            create_genre_project("pl_blank", "u@x.com", "p1", projects_dir=blank)


def test_a_keyword_that_cannot_actually_be_passed_does_not_count_as_accepting(
    clean_factories,
):
    """The probe is a bind, not a name lookup.

    ``projects_dir`` declared positional-only, or as ``*projects_dir``, is a name in
    ``parameters`` that cannot be passed as a keyword — a membership test reads all
    three alike and would report the factory placeable, turning nw's named refusal
    into a raw binding ``TypeError`` from inside the call.
    """
    ns: dict = {}
    exec(
        "def posonly(caller, project_id, projects_dir, /, *, title, template, params):\n"
        "    return {'project': None}\n",
        ns,
    )

    def varargs(caller, project_id, *projects_dir, title, template, params):
        return {"project": None}

    register_genre_project_factory("pl_posonly", ns["posonly"])
    register_genre_project_factory("pl_varargs", varargs)
    assert nw.can_place_genre_project("pl_posonly") is False
    assert nw.can_place_genre_project("pl_varargs") is False


def test_placement_symbols_are_reexported_on_nw():
    for name in ("can_place_genre_project", "PLACEMENT_ARG"):
        assert hasattr(nw, name), name
    assert nw.PLACEMENT_ARG == "projects_dir"
