"""Which artifacts an annotation names — the declared contract behind artifact lineage.

Since lacing#14, ``Provenance.was_derived_from`` holds annotation ids **and** artifact
asset ids (64-hex SHA-256). :func:`~nw.transforms._provenance.derive_provenance`
writes the annotation half from the Transform's inputs. The artifact half needs to
know *which artifact each input names*, and annotations name theirs under different
keys: nw's own Transforms write ``body["artifact_id"]``, a storyboard panel carries
``images[].artifact_id``, a source-media body carries ``asset_id``. That is a fact
about a **body schema**, so it is declared once, next to the schema, by whoever owns
the schema — never guessed by sweeping body keys (a ``url`` or a prefixed
``sha256:…`` id would be picked up and then refused by lacing).

    >>> from uuid import uuid4
    >>> from lacing import Annotation, MediaRef, Provenance, RationalTime, TimeInterval
    >>> _ = register_asset_refs(
    ...     "annot://schema/doc-example/v1", asset_fields("artifact_id", "extras[].id")
    ... )
    >>> ann = Annotation(
    ...     id=uuid4(),
    ...     tier="t",
    ...     reference=MediaRef(asset_id="p", interval=TimeInterval.from_seconds(0, 0)),
    ...     body={"artifact_id": "a" * 64, "extras": [{"id": "b" * 64}, {"id": None}]},
    ...     body_schema_uri="annot://schema/doc-example/v1",
    ...     provenance=Provenance(
    ...         was_generated_by="t", was_attributed_to="t",
    ...         generated_at_time=RationalTime.now(), activity="derive",
    ...     ),
    ... )
    >>> [ref[:4] for ref in asset_refs_of(ann)]
    ['aaaa', 'bbbb']

An undeclared schema names no artifacts — the default, which leaves every existing
producer's provenance byte-identical:

    >>> asset_refs_of(ann.model_copy(update={"body_schema_uri": "annot://schema/other/v1"}))
    ()

A declaration is a contract, so a value that is not a bare 64-hex asset id is a loud
error naming the schema, raised at plan time where it costs nothing — never
a silently dropped edge:

    >>> asset_refs_of(ann.model_copy(update={"body": {"artifact_id": "sha256:abc"}}))
    Traceback (most recent call last):
    ...
    nw.transforms.asset_refs.AssetRefDeclarationError: annot://schema/doc-example/v1: 'sha256:abc' is not a bare 64-hex asset id

nw declares only the schema it owns and whose artifact key it writes itself:
``render-result/v1`` → ``artifact_id``. Apps declare theirs.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from lacing import Annotation

AssetRefResolver = Callable[[dict], Iterable[Any]]
"""``body -> iterable of asset ids`` (``None`` entries are skipped)."""

_ASSET_ID = re.compile(r"[0-9a-f]{64}")  # always .fullmatch: `$` matches before a trailing "\n"

#: ``{body_schema_uri: resolver}``. Written by :func:`register_asset_refs`.
_RESOLVERS: dict[str, AssetRefResolver] = {}


class AssetRefDeclarationError(ValueError):
    """A declared asset-ref path produced something that is not a bare asset id."""


def register_asset_refs(
    body_schema_uri: str, resolver: AssetRefResolver
) -> AssetRefResolver:
    """Declare how to read the artifacts an annotation of ``body_schema_uri`` names.

    One declaration per schema; registering again replaces it (a schema's owner
    re-declaring on reload is not an error). Returns ``resolver`` so it can be used
    as a decorator.
    """
    _RESOLVERS[body_schema_uri] = resolver
    return resolver


def asset_fields(*paths: str) -> AssetRefResolver:
    """A resolver reading dotted ``paths`` from a body; ``[]`` steps into a list.

    >>> read = asset_fields("artifact_id", "images[].artifact_id", "cover.id")
    >>> list(read({"artifact_id": "x", "images": [{"artifact_id": "y"}, {}],
    ...            "cover": {"id": "z"}}))
    ['x', 'y', 'z']
    >>> list(read({}))
    []
    """
    parsed = [tuple(path.split(".")) for path in paths]

    def resolve(body: dict) -> Iterator[Any]:
        for steps in parsed:
            yield from _walk(body, steps)

    resolve.__doc__ = f"Asset ids at {', '.join(paths)}."
    return resolve


def _walk(node: Any, steps: tuple[str, ...]) -> Iterator[Any]:
    if not steps:
        yield node
        return
    step, rest = steps[0], steps[1:]
    is_list = step.endswith("[]")
    key = step[:-2] if is_list else step
    if not isinstance(node, dict) or key not in node:
        return
    value = node[key]
    if is_list:
        for item in value if isinstance(value, list) else ():
            yield from _walk(item, rest)
    else:
        yield from _walk(value, rest)


def asset_refs_of(annotation: Annotation) -> tuple[str, ...]:
    """The asset ids ``annotation`` names, per its schema's declaration.

    Deduplicated, in declaration order; ``None`` values skipped (a skeleton whose
    artifact is not rendered yet names none). ``()`` for an undeclared schema or a
    non-dict body.

    Raises:
        AssetRefDeclarationError: a declared path yielded a value that is not a
            bare 64-hex asset id.
    """
    uri = annotation.body_schema_uri
    resolver = _RESOLVERS.get(uri) if uri else None
    if resolver is None or not isinstance(annotation.body, dict):
        return ()
    refs: dict[str, None] = {}
    for value in resolver(annotation.body):
        if value is None:
            continue
        if not isinstance(value, str) or not _ASSET_ID.fullmatch(value):
            raise AssetRefDeclarationError(
                f"{uri}: {value!r} is not a bare 64-hex asset id"
            )
        refs[value] = None
    return tuple(refs)


def _declare_nw_owned() -> None:
    from nw.bodies.render_result import RENDER_RESULT_BODY_SCHEMA_URI

    register_asset_refs(RENDER_RESULT_BODY_SCHEMA_URI, asset_fields("artifact_id"))


_declare_nw_owned()


__all__ = [
    "AssetRefDeclarationError",
    "AssetRefResolver",
    "asset_fields",
    "asset_refs_of",
    "register_asset_refs",
]
