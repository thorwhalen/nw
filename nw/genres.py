"""nw.genres — production *genres*: the reusable specialization of a project.

A **Genre** is a pure-data descriptor of a *kind* of audiovisual production
(music video, narrative video, commentary weave, music visualizer, ...). It is
the first-class formalization of what nw informally called an "app": a bundle
declared *over the substrate that already exists*, carrying no engine of its
own.

A genre bundles, by reference:

- ``body_schema_uris`` — the lacing body schemas (``annot://schema/<kind>/vN``)
  its artifacts are validated against;
- ``transform_names`` — the :mod:`nw.transforms` entries forming its pipeline DAG;
- ``strategy_names`` — the optional :mod:`nw.renderers` strategies it dispatches to;
- ``projection_entrypoint`` — the final assemble/render step that turns the
  graph into the delivered artifact (e.g. ``clips_to_animatic``);
- ``folder_conventions`` — optional project-folder layout hints.

:class:`nw.Project`, the ``prepare -> plan -> execute`` split, ``stale_after``
freshness, ``nw.jobs`` and the cost gate are all genre-agnostic and serve every
genre unchanged — so *adding a genre is a one-file registration*, the same
open-closed shape as :mod:`nw.renderers` and :mod:`nw.transforms`.

Genres live in the :data:`genres` registry (an :class:`xdol.Registry` with
``on_conflict="error"``). nw ships **no** built-in genres: concrete genres
register themselves from their own packages (``muvid``, ``braidio``) or from
the studio host (``reelee``), which keeps app-layer concerns (output intents,
flavors, prompt packs, cost profiles) out of the substrate. A named preset
*within* a genre (a filled-in default configuration) is a :class:`Template`.
The substrate owns a Template's *identity* (slug/title/description) and carries
a genre-defined ``params`` payload it does **not** interpret — the app that owns
the genre validates and resolves those params (reelee reads ``output_intent`` /
``flavor``; braidio reads a ``format_id``). This keeps the genre *self-describing*
for any consumer (a CLI, an HTTP catalog, an MCP connector) while app-specific
meaning stays in the app.

Naming rationale (Genre vs the alternatives ``kind`` / ``format`` / ...) is in
GitHub issue thorwhalen/nw#10; the "nw owns the engine, each app supplies its
schemas + Transforms" stance is in
``misc/docs/Rendering Provenance and Partial Re-render.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Optional

from xdol import Registry

if TYPE_CHECKING:  # runtime-free: only for the GenreInitializer type hint
    from .project import Project


#: Genre lifecycle statuses. ``available`` = usable now; ``experimental`` =
#: usable but unstable; ``planned`` = declared for discovery, not yet ready.
GENRE_STATUSES = ("available", "experimental", "planned")
DFLT_GENRE_STATUS = "available"


def _validate_slug(slug: object, *, what: str) -> None:
    """Assert ``slug`` is a registry-safe key: a non-empty, whitespace-free string.

    The one place the slug contract for :class:`Genre`, :class:`Template`, and a
    genre resolver is defined — so every registrable thing keyed by a slug fails
    fast on a bad key instead of becoming silent dead state.
    """
    if not isinstance(slug, str) or not slug.strip():
        raise ValueError(f"{what} slug must be a non-empty string")
    if any(ch.isspace() for ch in slug):
        raise ValueError(f"{what} slug {slug!r} must not contain whitespace")


@dataclass(frozen=True)
class Template:
    """A named preset ("subgenre") *within* a genre — a filled-in default config.

    AV-general: the substrate owns the Template's identity (``slug``/``title``/
    ``description``) and carries an opaque ``params`` payload it does **not**
    interpret. The app that owns the genre puts meaning in ``params`` (reelee:
    ``{"output_intent": ..., "flavor": ...}``; braidio: ``{"format_id": ...}``)
    and validates/resolves it. Frozen + hashable (``params`` is excluded from
    identity and normalized to an immutable mapping), so a Template can live in a
    ``Genre.templates`` tuple without breaking the genre's ``__hash__``.

    >>> t = Template(slug="cinematic_clip", title="Cinematic clip",
    ...              params={"flavor": "fal.cinematic"})
    >>> t.params["flavor"]
    'fal.cinematic'
    >>> t.to_dict()["params"]
    {'flavor': 'fal.cinematic'}
    """

    slug: str
    title: str
    description: str = ""
    # opaque, genre-defined preset payload — see class docstring. ``compare=False``
    # keeps the frozen Template hashable (a Mapping field would break __hash__).
    params: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        _validate_slug(self.slug, what="Template")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError(
                f"Template {self.slug!r}: title must be a non-empty string"
            )
        if not isinstance(self.params, MappingProxyType):
            object.__setattr__(self, "params", MappingProxyType(dict(self.params)))

    def to_dict(self) -> dict:
        """A JSON-able catalog entry: ``{slug, title, description, params}``."""
        return {
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "params": dict(self.params),
        }


@dataclass(frozen=True)
class Genre:
    """A reusable definition of a *production kind* over the nw substrate.

    Pure data: it *references* substrate pieces by name rather than owning
    them, so declaring a genre never touches the engine.

    >>> slideshow = Genre(
    ...     slug="slideshow",
    ...     title="Slideshow",
    ...     description="Stills over narration, assembled to a video.",
    ...     transform_names=("clips_to_animatic.ffmpeg",),
    ...     projection_entrypoint="clips_to_animatic.ffmpeg",
    ... )
    >>> slideshow.title
    'Slideshow'
    >>> slideshow.status
    'available'

    ``projection_entrypoint``, when given, must be one of the genre's own
    declared transforms or strategies:

    >>> Genre(slug="bad", title="Bad", projection_entrypoint="nope")
    Traceback (most recent call last):
        ...
    ValueError: Genre 'bad': projection_entrypoint 'nope' is not among its transform_names or strategy_names
    """

    slug: str
    title: str
    description: str = ""
    body_schema_uris: tuple[str, ...] = ()
    transform_names: tuple[str, ...] = ()
    strategy_names: tuple[str, ...] = ()
    projection_entrypoint: str | None = None
    # ``compare=False`` keeps the descriptor hashable (a dict field would break
    # the frozen dataclass's generated ``__hash__``) and keeps folder layout —
    # incidental metadata — out of genre *identity*. Normalized to an immutable
    # mapping in ``__post_init__`` so a "frozen" Genre is genuinely frozen.
    folder_conventions: Mapping[str, str] = field(default_factory=dict, compare=False)
    status: str = DFLT_GENRE_STATUS
    #: Named presets ("subgenres") within this genre — see :class:`Template`.
    templates: tuple[Template, ...] = ()
    #: Intake "what are you making?" answers that select this genre (the edge
    #: :func:`recommend_genre` walks). App data (e.g. reelee's intake form) owns
    #: the vocabulary; the genre just declares which answers it covers.
    intake_kinds: tuple[str, ...] = ()
    #: A short discriminator slug routing the cost gate to the right estimator
    #: (e.g. ``"tts"`` = per-character audio, ``"per_clip"`` = per-render video).
    #: The real numbers stay in the app; this is only the routing tag.
    cost_profile: str | None = None
    #: The "start from scratch" params for this genre (same opaque shape as a
    #: :class:`Template`'s ``params``) — used when no template is chosen.
    defaults: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        # Normalize every sequence field to a tuple up front — symmetrically with
        # the Mapping-field normalization below — so a "frozen" Genre built with a
        # list (e.g. ``templates=[Template(...) for ...]``) is genuinely immutable
        # AND hashable, rather than a mutable list silently breaking ``__hash__``
        # only at the first set/dict-key use far from here.
        for _name in (
            "body_schema_uris",
            "transform_names",
            "strategy_names",
            "templates",
            "intake_kinds",
        ):
            _value = getattr(self, _name)
            if not isinstance(_value, tuple):
                object.__setattr__(self, _name, tuple(_value))
        _validate_slug(self.slug, what="Genre")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError(f"Genre {self.slug!r}: title must be a non-empty string")
        if self.status not in GENRE_STATUSES:
            raise ValueError(
                f"Genre {self.slug!r}: status {self.status!r} not in {GENRE_STATUSES}"
            )
        pe = self.projection_entrypoint
        if (
            pe is not None
            and pe not in self.transform_names
            and pe not in self.strategy_names
        ):
            raise ValueError(
                f"Genre {self.slug!r}: projection_entrypoint {pe!r} is not among "
                "its transform_names or strategy_names"
            )
        if not isinstance(self.folder_conventions, MappingProxyType):
            object.__setattr__(
                self,
                "folder_conventions",
                MappingProxyType(dict(self.folder_conventions)),
            )
        if not all(isinstance(t, Template) for t in self.templates):
            raise ValueError(f"Genre {self.slug!r}: templates must all be nw.Template")
        slugs = [t.slug for t in self.templates]
        if len(slugs) != len(set(slugs)):
            raise ValueError(
                f"Genre {self.slug!r}: template slugs must be unique, got {slugs}"
            )
        if any((not isinstance(k, str) or not k.strip()) for k in self.intake_kinds):
            raise ValueError(
                f"Genre {self.slug!r}: intake_kinds must be non-empty strings"
            )
        if self.cost_profile is not None and (
            not isinstance(self.cost_profile, str) or not self.cost_profile.strip()
        ):
            raise ValueError(
                f"Genre {self.slug!r}: cost_profile must be a non-empty string or None"
            )
        if not isinstance(self.defaults, MappingProxyType):
            object.__setattr__(self, "defaults", MappingProxyType(dict(self.defaults)))

    def template(self, slug: str) -> Template:
        """Look up one of this genre's :class:`Template`\\ s by slug (KeyError if absent)."""
        for candidate in self.templates:
            if candidate.slug == slug:
                return candidate
        known = [t.slug for t in self.templates]
        raise KeyError(f"Genre {self.slug!r} has no template {slug!r}; has: {known}")

    def list_templates(self) -> list[str]:
        """This genre's Template slugs, in declared order."""
        return [t.slug for t in self.templates]

    def to_dict(self) -> dict:
        """A JSON-able catalog entry — the shape apps serve to a frontend / MCP client.

        Templates are emitted with their opaque ``params`` (not flattened), and
        ``intake_kinds``/``cost_profile``/``defaults`` ride at the genre level, so a
        consumer needs no app-specific knowledge to render the catalog.
        """
        return {
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "ready": self.is_ready(),
            "intake_kinds": list(self.intake_kinds),
            "cost_profile": self.cost_profile,
            "defaults": dict(self.defaults),
            "templates": [t.to_dict() for t in self.templates],
        }

    def missing_transforms(self) -> list[str]:
        """Declared ``transform_names`` not (yet) present in ``nw.transforms``."""
        from .transforms import transforms as _transforms

        return [n for n in self.transform_names if n not in _transforms]

    def missing_strategies(self) -> list[str]:
        """Declared ``strategy_names`` not (yet) present in ``nw.renderers``."""
        from .renderers import strategies as _strategies

        return [n for n in self.strategy_names if n not in _strategies]

    def is_ready(self) -> bool:
        """True iff every declared transform and strategy is registered.

        A ``planned`` genre may legitimately be *not* ready; an ``available``
        one that isn't ready is a wiring bug worth catching in a test.
        """
        return not self.missing_transforms() and not self.missing_strategies()


#: The genre registry. ``on_conflict="error"`` so a misconfigured plugin can't
#: silently shadow another package's genre.
genres: Registry = Registry(name="nw.genres", on_conflict="error")
"""Public registry of :class:`Genre` instances, keyed by ``genre.slug``.
Apps add genres via :func:`register_genre` (or ``nw.register_genre``)."""


def register_genre(genre: Genre) -> Genre:
    """Register a :class:`Genre` under its ``slug``; returns it for inline use.

    >>> g = register_genre(Genre(slug="doctest_demo", title="Demo"))
    >>> get_genre("doctest_demo").title
    'Demo'
    >>> "doctest_demo" in list_genres()
    True
    >>> del genres["doctest_demo"]  # keep the shared registry clean
    """
    if not isinstance(genre, Genre):
        raise TypeError(f"register_genre expects a Genre, got {type(genre).__name__}")
    genres.register(genre.slug, genre)
    return genre


def get_genre(slug: str) -> Genre:
    """Look up a genre by slug; raises :class:`KeyError` with the known slugs."""
    if slug not in genres:
        known = sorted(genres.keys())
        raise KeyError(
            f"No genre {slug!r}; registered: {known}. Apps register genres via "
            "`nw.register_genre(Genre(...))`."
        )
    return genres[slug]


def list_genres() -> list[str]:
    """Return all registered genre slugs (sorted)."""
    return sorted(genres.keys())


def genre_catalog() -> list[dict]:
    """Every registered genre as a JSON-able catalog entry (sorted by slug).

    This is the generic, app-agnostic catalog an HTTP route / MCP tool serves; see
    :meth:`Genre.to_dict` for the entry shape.
    """
    return [get_genre(slug).to_dict() for slug in list_genres()]


def describe_genre(slug: str) -> dict:
    """One genre's catalog entry (raises :class:`KeyError` if the slug is unknown)."""
    return get_genre(slug).to_dict()


def recommend_genre(kind: str | None) -> str | None:
    """The slug of the genre whose ``intake_kinds`` contains ``kind`` (first in slug
    order), or ``None`` when ``kind`` is falsy / unmatched.

    >>> g = register_genre(Genre(slug="_rec_demo", title="Rec", intake_kinds=("essay",)))
    >>> recommend_genre("essay")
    '_rec_demo'
    >>> recommend_genre("nope") is None and recommend_genre(None) is None
    True
    >>> del genres["_rec_demo"]
    """
    if not kind:
        return None
    for slug in list_genres():
        if kind in get_genre(slug).intake_kinds:
            return slug
    return None


def resolve_defaults(genre: str, template: str | None = None) -> dict:
    """Resolve a genre (+ optional template) to the params for a new project.

    Returns ``{"genre": slug, "template": template_or_None, "params": {...}}`` — the
    chosen :class:`Template`'s ``params`` when ``template`` is given, else the genre's
    ``defaults``. Raises :class:`KeyError` on an unknown genre or template. The caller
    (app) interprets ``params`` (reelee reads ``output_intent``/``flavor``; braidio a
    ``format_id``).
    """
    g = get_genre(genre)
    params = dict(g.defaults) if template is None else dict(g.template(template).params)
    return {"genre": genre, "template": template, "params": params}


#: A genre resolver: ``(genre, template) -> params`` — an owning-app-defined,
#: JSON-able ``params`` payload (e.g. reelee's ``{output_intent, flavor}``, braidio's
#: ``{format_id}``) for the chosen template (or ``None`` = "start from scratch").
#: :func:`resolve_genre` wraps this in the standard ``{genre, template, params}``
#: envelope — the resolver returns ONLY the bare params. Registered per genre via
#: :func:`register_genre_resolver`.
GenreResolver = Callable[[Genre, Optional[str]], dict]

#: Registry of per-genre resolvers, keyed by genre slug. A genre's **owning app**
#: registers how to turn a ``(genre, template)`` into its params, so a host that
#: aggregates many genres (a CLI, an HTTP API, an MCP connector) can create ANY
#: genre's project via :func:`resolve_genre` without hardcoding which app owns it.
genre_resolvers: Registry = Registry(name="nw.genre_resolvers", on_conflict="error")


def register_genre_resolver(slug: str, resolver: "GenreResolver") -> "GenreResolver":
    """Register a resolver for a genre slug; returns it for inline use.

    Called by the genre's owning app. ``resolver(genre, template) -> params`` maps a
    chosen template (or ``None`` for "start from scratch") to that app's bare params
    payload; :func:`resolve_genre` adds the ``{genre, template, params}`` envelope.
    Independent of genre *registration order* (keyed by the slug string).

    >>> _ = register_genre(Genre(slug="_resolver_demo", title="Demo",
    ...                          defaults={"look": "plain"}))
    >>> _ = register_genre_resolver("_resolver_demo",
    ...     lambda genre, template: {"look": genre.defaults["look"], "via": "resolver"})
    >>> resolve_genre("_resolver_demo")
    {'genre': '_resolver_demo', 'template': None, 'params': {'look': 'plain', 'via': 'resolver'}}
    >>> del genres["_resolver_demo"]; del genre_resolvers["_resolver_demo"]
    """
    _validate_slug(slug, what="genre resolver")
    if not callable(resolver):
        raise TypeError(
            f"register_genre_resolver expects a callable, got {type(resolver).__name__}"
        )
    genre_resolvers.register(slug, resolver)
    return resolver


def resolve_genre(genre: str, template: Optional[str] = None) -> dict:
    """Resolve a genre (+ optional template) to the standard creation envelope.

    Always returns ``{"genre": slug, "template": template_or_None, "params": {...}}`` —
    ONE stable contract for every host, regardless of whether the genre has a resolver.
    ``params`` comes from the genre's registered resolver
    (:func:`register_genre_resolver`) when one exists, else from the generic
    :func:`resolve_defaults` (the template's params, or the genre's ``defaults``).

    Raises :class:`KeyError` on an unknown genre or — **uniformly, resolver or not** —
    an unknown ``template`` slug (the substrate owns template identity; a resolver only
    interprets params, it doesn't get to invent template slugs).

    >>> _ = register_genre(Genre(slug="_rg_demo", title="Demo",
    ...                          defaults={"flavor": "cinematic"}))
    >>> resolve_genre("_rg_demo")  # no resolver registered -> generic params
    {'genre': '_rg_demo', 'template': None, 'params': {'flavor': 'cinematic'}}
    >>> del genres["_rg_demo"]
    """
    g = get_genre(genre)
    chosen = (
        g.template(template) if template is not None else None
    )  # validates the slug
    if genre in genre_resolvers:
        params = genre_resolvers[genre](g, template)
    else:
        params = dict(g.defaults) if chosen is None else dict(chosen.params)
    return {"genre": genre, "template": template, "params": params}


# ---------------------------------------------------------------------------
# Genre INITIALIZERS — the *apply* half of the genre→project contract.
#
# :func:`resolve_genre` is the *pure* half: ``(genre, template) -> params`` (the
# JSON-able creation envelope). A genre initializer is its *side-effecting* twin:
# ``(genre, template, project, params) -> None`` — it **seeds a freshly-created
# project** for the chosen genre/template (reelee writes an output-intent
# annotation; braidio applies its format at *render* time, so it registers none).
# Together they let a host that aggregates many genres (a CLI, an HTTP API, an MCP
# connector) create ANY genre's project — resolve to get the params, create the
# bare project, then initialize — without hardcoding which app owns the genre.
# ---------------------------------------------------------------------------

#: A genre initializer: ``(genre, template, project, params) -> None``. The
#: side-effecting apply-counterpart to a :data:`GenreResolver`'s pure
#: ``(genre, template) -> params``. It receives the full resolver context (the
#: :class:`Genre` object + chosen ``template`` slug) plus the freshly-created
#: ``project`` to seed and the resolved ``params``. reelee's writes the
#: output-intent annotation; a genre that seeds nothing on create registers none.
#:
#: **Contract:** an initializer MUST confine all side effects to ``project``
#: (typically writing annotations under ``project.root``) — so a host can revert a
#: failed create by deleting the project folder — or own its own rollback. Writing
#: to a shared store / external service / global registry breaks that guarantee.
GenreInitializer = Callable[
    ["Genre", Optional[str], "Project", Mapping[str, Any]], None
]

#: Registry of per-genre initializers, keyed by genre slug. A genre's **owning
#: app** registers how to seed a fresh project for that genre; a genre that seeds
#: nothing on create simply registers none (:func:`initialize_genre` no-ops).
genre_initializers: Registry = Registry(
    name="nw.genre_initializers", on_conflict="error"
)


def register_genre_initializer(
    slug: str, initializer: "GenreInitializer"
) -> "GenreInitializer":
    """Register an initializer for a genre slug; returns it for inline use.

    Called by the genre's owning app. ``initializer(genre, template, project,
    params) -> None`` seeds a freshly-created project for the chosen genre/template
    (see :data:`GenreInitializer` for the side-effect contract);
    :func:`initialize_genre` dispatches to it. Independent of genre *registration
    order* (keyed by the slug string). A genre that seeds nothing on create needs
    no initializer at all.

    >>> _ = register_genre(Genre(slug="_init_demo", title="Demo",
    ...                          defaults={"look": "plain"}))
    >>> seen = {}
    >>> def _seed(genre, template, project, params):
    ...     seen["applied"] = (genre.slug, template, params)
    >>> _ = register_genre_initializer("_init_demo", _seed)
    >>> initialize_genre("_init_demo", object())  # params default to the genre's
    >>> seen["applied"]
    ('_init_demo', None, {'look': 'plain'})
    >>> del genres["_init_demo"]; del genre_initializers["_init_demo"]
    """
    _validate_slug(slug, what="genre initializer")
    if not callable(initializer):
        raise TypeError(
            "register_genre_initializer expects a callable, got "
            f"{type(initializer).__name__}"
        )
    genre_initializers.register(slug, initializer)
    return initializer


def initialize_genre(
    genre: str,
    project: "Project",
    *,
    template: Optional[str] = None,
    params: Optional[Mapping[str, Any]] = None,
) -> None:
    """Seed a freshly-created ``project`` for ``genre`` (+ optional ``template``).

    The side-effecting apply-counterpart to :func:`resolve_genre`. Dispatches to
    the genre's registered initializer (:func:`register_genre_initializer`) when
    one exists; **when none is registered this is a no-op** — the correct default
    for a genre that seeds nothing on create (e.g. one whose preset is applied at
    render time).

    ``params`` is the resolved creation params (from :func:`resolve_genre`); when
    ``None`` it is resolved from the genre's ``template``/``defaults`` here, so
    ``initialize_genre(genre, project)`` seeds a project in the genre's defaults in
    one call. Raises :class:`KeyError` on an unknown genre or — **uniformly** — an
    unknown ``template`` slug (matching :func:`resolve_genre`).

    >>> _ = register_genre(Genre(slug="_noinit_demo", title="Demo"))
    >>> initialize_genre("_noinit_demo", object())  # no initializer -> no-op
    >>> del genres["_noinit_demo"]
    """
    g = get_genre(genre)  # validates the genre slug (KeyError otherwise)
    if params is None:
        params = resolve_genre(genre, template)["params"]  # also validates template
    elif template is not None:
        g.template(template)  # validate template uniformly even when params given
    if genre in genre_initializers:
        genre_initializers[genre](g, template, project, dict(params))


__all__ = [
    "GENRE_STATUSES",
    "Genre",
    "Template",
    "genres",
    "get_genre",
    "list_genres",
    "register_genre",
    "genre_catalog",
    "describe_genre",
    "recommend_genre",
    "resolve_defaults",
    "GenreResolver",
    "genre_resolvers",
    "register_genre_resolver",
    "resolve_genre",
    "GenreInitializer",
    "genre_initializers",
    "register_genre_initializer",
    "initialize_genre",
]
