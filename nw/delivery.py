"""What a genre hands back when a human wants to *hold* what it made.

Every genre in the federation renders something a person eventually wants to watch,
hear, or send to a client. Getting those bytes from the server to that person is
split along ownership lines, and this module owns the vocabulary in the middle:

- **The genre owns resolution.** Given a caller and an artifact reference, which
  file is it, and is it theirs? Only the genre knows its own workspace layout.
- **The host owns transport.** Signing, expiry, streaming, the watch page. Only
  the host knows its public URL, its secret, and its route.
- **This module owns the noun they exchange** — :class:`Deliverable` — plus the
  two Protocols that pin the seam: :data:`Resolver` and :data:`Lister`.

nw is where this belongs because it is the one package every genre already
reaches and which reaches none of them: ``muvid``, ``braidio`` and ``reelee`` all
depend on nw, and nw imports no genre. A type owned by any one of them would make
the other two depend sideways.

Why this module exists at all
-----------------------------
It is the repair for a defect that shipped, and the shape of the defect is the
argument for the module. reelee typed its resolver seam ``-> Path``; muvid's
resolver returned its own ``ResolvedArtifact`` dataclass carrying the content
type and a human filename. Both were reasonable in isolation, both were tested,
and both test suites were green — because each tested only its own side. In
production the host did ``Path(resolved).suffix`` on muvid's dataclass and raised
``TypeError``, so **every music-video download 500'd, and had since the day it
was registered.** Nobody saw it, because a separate gap meant no caller could
obtain a token for that genre in the first place: the failure was unreachable,
so it was invisible, so it persisted while a paying user rendered five videos he
could never retrieve.

Two rules follow, and they are the module's whole reason to be:

1. **One type, defined once, imported by both sides.** A seam described in two
   places is two seams that agree by luck.
2. **The richer half wins.** muvid returned ``content_type`` and ``filename``
   because the transport genuinely needs both — a bare ``Path`` forces the host
   to re-derive a media type it was already told, and to name the download after
   an opaque id. :class:`Deliverable` keeps them.

The speakable reference
-----------------------
:attr:`Deliverable.ref` is the field a human says out loud. A render id like
``b02fc05417ea`` is unusable in conversation — you cannot ask for "a bit less
reverb on b02fc05417ea" — so a genre assigns each deliverable a short label like
``cut 4``, stable for the life of the artifact, and accepts it anywhere the raw
id is accepted. :func:`parse_ref` is the shared parser so every genre reads the
same spellings (``4``, ``cut 4``, ``cut-4``, ``#4``) rather than each inventing
its own near-miss.

The reference is *per project*, not global: it rides alongside a ``project_id``
everywhere it is used, which is what keeps it short enough to say.

>>> parse_ref("cut 4")
4
>>> parse_ref("cut-12"), parse_ref("#3"), parse_ref("7")
(12, 3, 7)
>>> parse_ref("b02fc05417ea") is None    # a raw id, not an ordinal
True
>>> format_ref(4)
'cut 4'

Authorization is the genre's job, and it is not optional
--------------------------------------------------------
A :data:`Resolver` receives the caller's email and MUST refuse anything that is
not that caller's. It raises ``KeyError`` for "no such artifact" and, where the
distinction is safe to reveal, ``PermissionError`` for "not yours". Where the
workspace is already email-scoped the two are indistinguishable and ``KeyError``
is the right answer for both — saying which would leak the existence of another
tenant's work.

A resolver that forgets this turns a signed-URL route into a cross-tenant read
primitive, which is why the Protocol takes ``email`` as its first argument rather
than letting the host pass it as an afterthought.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

__all__ = [
    "Deliverable",
    "Resolver",
    "Lister",
    "parse_ref",
    "format_ref",
    "REF_WORD",
]

#: The noun a genre uses when it labels a deliverable for a human ("cut 4").
#: One word, so the label stays short enough to say in the middle of a sentence.
REF_WORD = "cut"

#: Spellings of an ordinal reference we accept. Deliberately permissive on input
#: and single-formed on output: a human types what they remember, and we always
#: print it back the one way.
_REF_RE = re.compile(rf"^\s*(?:{REF_WORD}\s*[-_ ]?\s*|#\s*)?(\d{{1,6}})\s*$", re.I)


@dataclass(frozen=True)
class Deliverable:
    """A finished thing a person can watch, hear, or download.

    ``path`` is server-side and never leaves the host; it is what the transport
    streams. Everything else exists so the host does not have to guess:

    - ``content_type`` — what to serve it as. The genre knows; the host would
      otherwise re-derive it from a suffix.
    - ``filename`` — what it should be called when it lands in someone's
      Downloads folder. ``music_video_test_02-cut-4.mp4`` beats ``b02fc05417ea``.
    - ``ref`` — the speakable label (see :func:`format_ref`).
    - ``artifact_id`` — the stable, unambiguous id. ``ref`` is the convenience;
      this is the truth, and it is what a signed token is minted against.

    The optional descriptive fields are what a listing surface renders, and what
    lets a watch page say "10 seconds, 4.4 MB, made yesterday" without opening
    the file.
    """

    path: Path
    content_type: str
    filename: str
    artifact_id: str = ""
    project_id: str = ""
    genre: str = ""
    ref: str | None = None
    title: str | None = None
    duration_s: float | None = None
    size_bytes: int | None = None
    created_at: float | None = None
    #: Genre-specific extras a listing or watch page may show. Free-form on
    #: purpose — the host renders what it recognises and ignores the rest, so a
    #: genre can enrich its own surface without a change here.
    meta: dict = field(default_factory=dict)

    @property
    def kind(self) -> str:
        """``'video'``, ``'audio'``, ``'image'`` or ``'file'`` — how to present it.

        Derived from ``content_type`` so a genre never has to declare it twice.

        >>> Deliverable(Path('a.mp4'), 'video/mp4', 'a.mp4').kind
        'video'
        >>> Deliverable(Path('a.mp3'), 'audio/mpeg', 'a.mp3').kind
        'audio'
        >>> Deliverable(Path('a.pdf'), 'application/pdf', 'a.pdf').kind
        'file'
        """
        major = (self.content_type or "").split("/", 1)[0]
        return major if major in ("video", "audio", "image") else "file"

    @property
    def label(self) -> str:
        """The best short name for a human — the ref if it has one, else the id.

        >>> Deliverable(Path('a.mp4'), 'video/mp4', 'a.mp4', ref='cut 4').label
        'cut 4'
        >>> Deliverable(Path('a.mp4'), 'video/mp4', 'a.mp4', artifact_id='b02f').label
        'b02f'
        """
        return self.ref or self.artifact_id or self.filename


@runtime_checkable
class Resolver(Protocol):
    """``resolve(email, project_id, artifact_id) -> Deliverable`` — a genre's half.

    ``artifact_id`` may be a raw id OR a reference the genre accepts (see
    :func:`parse_ref`); resolving both is the genre's job, because only it knows
    the ordering that gives ``cut 4`` its meaning.

    Raises ``KeyError`` when nothing resolves (the host answers 404) and
    ``PermissionError`` when it resolves but is not the caller's (403). Never
    let a server path escape in the message.
    """

    def __call__(
        self, email: str, project_id: str, artifact_id: str
    ) -> Deliverable: ...


#: ``list_deliverables(email, project_id=None) -> list[Deliverable]`` — the other
#: half of "give me my work". Without it a reference is undiscoverable: the user
#: can only name a deliverable they still remember. ``project_id=None`` means
#: every project of that caller's in this genre.
Lister = Callable[..., "list[Deliverable]"]


def parse_ref(text: str) -> int | None:
    """The ordinal in a spoken reference, or ``None`` if it isn't one.

    ``None`` is the signal to fall through to treating the input as a raw
    artifact id — which is why this never raises: "not an ordinal" is an
    ordinary, expected answer, not an error.

    >>> parse_ref("cut 4"), parse_ref("CUT4"), parse_ref(" cut - 4 ")
    (4, 4, 4)
    >>> parse_ref("#11"), parse_ref("11")
    (11, 11)
    >>> parse_ref("b02fc05417ea") is None, parse_ref("") is None
    (True, True)

    Zero and negatives are not references — deliverables are numbered from 1, so
    accepting ``cut 0`` would resolve to a neighbour under a naive index:

    >>> parse_ref("cut 0") is None
    True
    """
    if not isinstance(text, str):
        return None
    m = _REF_RE.match(text)
    if not m:
        return None
    n = int(m.group(1))
    return n if n >= 1 else None


def format_ref(n: int) -> str:
    """The one spelling we print. Input is permissive; output never varies.

    >>> format_ref(1), format_ref(42)
    ('cut 1', 'cut 42')
    """
    return f"{REF_WORD} {int(n)}"
