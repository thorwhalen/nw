"""Execution secrets — credentials that reach ``execute`` and nothing else.

A caller's bring-your-own API key has to reach the one place that spends it
(:meth:`nw.Transform.execute`, or the render callable behind a
:func:`nw.jobs.enqueue`) **without** going through the graph: a key must
never be persisted in a node body, provenance, a :class:`falaw.Plan`, a cache
key, a run record, the job index or a log line. This module is the seam that
carries it, and :class:`Secrets` is what makes the invariant enforced rather
than promised.

**The shape.** ``execute(..., *, secrets=...)`` is a keyword-only argument,
passed accepts-it-or-not by :func:`nw.fan_out_execute` and by
:func:`nw.jobs.enqueue`'s dispatch — the same seam ``on_failure`` (nw#25) and
``unit_instance_id`` (nw#44) use — so a Transform that spends a caller's
credential declares the keyword, and one that does not never sees it.
Secrets are keyed by **provider name** (``"fal"``, ``"elevenlabs"``, …): nw
owns :data:`FAL_SECRET`, an app owns the names of the providers it calls.

**Why a type, not a dict.** A :class:`Secrets` is a read-only
:class:`~collections.abc.Mapping` whose ``repr``/``str`` redact every value,
that refuses to be pickled, and that is deliberately *not* a ``dict`` — so
``json.dumps`` (and pydantic) of anything that accidentally holds one raises
instead of writing the key. Absent values are dropped at construction, so a
boundary can pass an optional header value straight through:
``Secrets(elevenlabs=request_header)`` is empty — and falsy — when the header
was not sent, which every consumer reads as "use the process environment".

>>> s = Secrets(elevenlabs="sk-live-…", fal=None)
>>> sorted(s)
['elevenlabs']
>>> s
Secrets(<1 redacted: elevenlabs>)
>>> bool(Secrets(fal=None))
False
>>> import json
>>> json.dumps({"secrets": s})  # a record can never carry one by accident
Traceback (most recent call last):
    ...
TypeError: Object of type Secrets is not JSON serializable
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, nullcontext
from typing import Any, Optional

FAL_SECRET = "fal"
"""The secret name nw itself consumes: :meth:`nw.BaseTransform.execute` and the
:mod:`nw.jobs` worker bind it as the fal credential
(:func:`falaw.using_fal_credentials`) for the duration of the call."""

_REDACTED_REPR = "Secrets(<{n} redacted: {names}>)"


class Secrets(Mapping[str, str]):
    """A read-only ``{provider_name: key}`` mapping that never prints or persists.

    Construct from a mapping, keywords, or both; ``None``/empty values are
    dropped (absent means "not supplied"), a non-``str`` key or value is a
    ``TypeError`` — a secret is text, and an int or a bytes object here is a
    caller bug worth failing on.
    """

    __slots__ = ("_items",)

    def __init__(
        self,
        mapping: Optional[Mapping[str, Optional[str]]] = None,
        /,
        **named: Optional[str],
    ) -> None:
        items: dict[str, str] = {}
        for source in (mapping or {}, named):
            for name, value in source.items():
                if value is None or value == "":
                    continue
                if not isinstance(name, str) or not name:
                    raise TypeError(
                        f"Secrets: a secret name must be a non-empty str, got {name!r}"
                    )
                if not isinstance(value, str):
                    raise TypeError(
                        f"Secrets: the {name!r} secret must be a str, got "
                        f"{type(value).__name__}"
                    )
                items[name] = value
        self._items = items

    # --- Mapping ---------------------------------------------------------
    def __getitem__(self, name: str) -> str:
        return self._items[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, name: object) -> bool:
        return name in self._items

    def with_(self, **named: Optional[str]) -> "Secrets":
        """A copy with ``named`` layered on top (a boundary adding a provider)."""
        return Secrets(self._items, **named)

    # --- the invariant, mechanically --------------------------------------
    def __repr__(self) -> str:
        return _REDACTED_REPR.format(
            n=len(self._items), names=", ".join(sorted(self._items))
        )

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError(
            "Secrets cannot be pickled: a credential must not cross a process "
            "boundary or land in a store. Re-bind it on the other side instead."
        )

    def __reduce_ex__(self, protocol):
        return self.__reduce__()

    def __copy__(self) -> "Secrets":
        return self

    def __deepcopy__(self, memo: dict) -> "Secrets":
        return self

    # Identity equality, deliberately: ``Mapping.__eq__`` would compare the
    # *values*, which makes ``secrets == {"fal": guess}`` an oracle.
    def __eq__(self, other: object) -> bool:
        return self is other

    def __hash__(self) -> int:
        return id(self)


def as_secrets(secrets: Optional[Mapping[str, Optional[str]]]) -> Optional[Secrets]:
    """Coerce a caller-supplied mapping to :class:`Secrets`; empty → ``None``.

    The nw entry points — :meth:`nw.BaseTransform.execute`,
    :func:`nw.fan_out_execute`, :func:`nw.jobs.enqueue` — run every incoming
    ``secrets`` through this, so below *them* a Transform only ever sees the
    redacting type. A Transform that **overrides** ``execute`` and is called
    directly gets whatever the caller passed: an override that logs or
    formats its ``secrets`` should ``as_secrets`` first (or the caller should
    hand it a :class:`Secrets`), because a plain ``dict`` prints its values.

    >>> as_secrets(None) is None
    True
    >>> as_secrets({"fal": None}) is None
    True
    >>> as_secrets({"fal": "k"})
    Secrets(<1 redacted: fal>)
    """
    if secrets is None:
        return None
    if not isinstance(secrets, Mapping):
        raise TypeError(
            f"secrets must be a mapping of provider name -> key, got "
            f"{type(secrets).__name__}"
        )
    coerced = secrets if isinstance(secrets, Secrets) else Secrets(secrets)
    return coerced or None


def using_secrets(
    secrets: Optional[Mapping[str, Optional[str]]],
) -> AbstractContextManager[Any]:
    """Bind the secrets nw itself knows how to use, for the duration of a block.

    Today that is :data:`FAL_SECRET`: when present it becomes the fal
    credential (:func:`falaw.using_fal_credentials`) so every ``call_fal``
    inside the block authenticates with the caller's key instead of the
    server's ``FAL_KEY``. Anything else in ``secrets`` is left for the
    Transform that declared it. With no fal secret this is a ``nullcontext``,
    so the ``with`` shape stays uniform.
    """
    coerced = as_secrets(secrets)
    key = coerced.get(FAL_SECRET) if coerced else None
    if not key:
        return nullcontext()
    from falaw import using_fal_credentials

    return using_fal_credentials(key)


def redact(text: str, secrets: Optional[Mapping[str, Optional[str]]]) -> str:
    """``text`` with every secret value replaced by ``<redacted:name>``.

    For the places nw persists free text it did not author — an exception
    message, a failure reason — while holding the values that must not land
    there. Cheap, exact-substring, and a no-op with no secrets.

    >>> redact("boom: key sk-1 rejected", {"fal": "sk-1"})
    'boom: key <redacted:fal> rejected'
    >>> redact("nothing here", None)
    'nothing here'
    """
    coerced = as_secrets(secrets)
    if not coerced or not text:
        return text
    for name, value in coerced.items():
        text = text.replace(value, f"<redacted:{name}>")
    return text


def redact_exception(
    error: BaseException, secrets: Optional[Mapping[str, Optional[str]]]
) -> BaseException:
    """Scrub secret values out of ``error``'s message and notes, in place.

    Keeps the exception's *type* — the thing callers classify on — and returns
    the same object so it can be re-raised. Applied where nw lets an
    exception escape toward a store it does not own (the job worker) or files
    it into a record it does (a fan-out unit's ``reason``).
    """
    coerced = as_secrets(secrets)
    if not coerced:
        return error
    error.args = tuple(
        redact(a, coerced) if isinstance(a, str) else a for a in error.args
    )
    notes = getattr(error, "__notes__", None)
    if notes:
        error.__notes__ = [redact(n, coerced) for n in notes]
    return error


__all__ = [
    "Secrets",
    "FAL_SECRET",
    "as_secrets",
    "using_secrets",
    "redact",
    "redact_exception",
]
