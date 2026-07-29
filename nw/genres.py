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
*within* a genre (a filled-in default configuration) is a **Template**, and
Templates are an app-layer concept — see ``reelee.GenreProfile``.

Naming rationale (Genre vs the alternatives ``kind`` / ``format`` / ...) is in
GitHub issue thorwhalen/nw#10; the "nw owns the engine, each app supplies its
schemas + Transforms" stance is in
``misc/docs/Rendering Provenance and Partial Re-render.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from xdol import Registry


#: Genre lifecycle statuses. ``available`` = usable now; ``experimental`` =
#: usable but unstable; ``planned`` = declared for discovery, not yet ready.
GENRE_STATUSES = ("available", "experimental", "planned")
DFLT_GENRE_STATUS = "available"


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

    def __post_init__(self) -> None:
        if not isinstance(self.slug, str) or not self.slug.strip():
            raise ValueError("Genre.slug must be a non-empty string")
        if any(ch.isspace() for ch in self.slug):
            raise ValueError(f"Genre.slug {self.slug!r} must not contain whitespace")
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


__all__ = [
    "GENRE_STATUSES",
    "Genre",
    "genres",
    "get_genre",
    "list_genres",
    "register_genre",
]
