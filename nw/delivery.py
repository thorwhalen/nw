"""What a genre hands back when a human wants to *hold* what it made.

Every genre in the federation renders something a person eventually wants to watch,
hear, or send to a client. Getting those bytes from the server to that person is
split along ownership lines, and this module owns the vocabulary in the middle:

- **The genre owns resolution.** Given a caller and an artifact reference, which
  file is it, and is it theirs? Only the genre knows its own workspace layout.
- **The host owns transport.** Signing, expiry, streaming, the watch page. Only
  the host knows its public URL, its secret, and its route.
- **This module owns the noun they exchange** — :class:`Deliverable` (and its
  sibling :class:`ProjectSummary`) — plus the four functions that pin the seam:
  :data:`Resolver`, :data:`Lister`, :data:`ProjectLister` and
  :class:`Organiser`. A genre registers the ones it offers
  (``resolve`` is mandatory; see :func:`check_delivery_source`), and absence
  of the rest is a capability declaration, never an error.

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
    "ProjectSummary",
    "ProjectLister",
    "Organiser",
    "parse_ref",
    "format_ref",
    "check_title",
    "caller_key",
    "safe_message",
    "check_delivery_source",
    "REF_WORD",
    "MAX_TITLE_LEN",
    "DELIVERY_FUNCTIONS",
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


# ---------------------------------------------------------------------------
# Projects and organising — the seam's third and fourth functions
# (reelee#333; the asset-surfaces ADR §3.3 "naming belongs to the genre",
# §3.4 "the read claim stays read-only", §4 "zero organise operations").
# ---------------------------------------------------------------------------


def caller_key(email: str) -> str:
    """The one normalisation of a caller identity, applied at the seam.

    Bucket keys are lowercased OAuth emails, and workspaces are created
    lazily — so a caller who arrives as ``Noel@Example.com`` after months as
    ``noel@example.com`` would silently mint a second, empty bucket, and their
    entire body of work would vanish from every listing with no error. Two
    normalisations that almost agree are two buckets; this is the one.

    Hosts SHOULD route every seam call through it. It is offered, not
    retroactively demanded: existing callers normalise where they already do,
    and converge here as they touch those sites.

    >>> caller_key("  Noel@Example.COM ")
    'noel@example.com'
    """
    return (email or "").strip().lower()


@dataclass(frozen=True)
class ProjectSummary:
    """A project a caller has — whether or not it has ever rendered.

    :data:`Lister` enumerates finished work, which means a footage project
    with a song and twelve clips and no cut yet is invisible to every surface
    in the system (reelee#333). This is the row that makes it findable.

    Every existing workspace lister computes an mtime to sort by and then
    throws it away — forcing a host that merges several genres' listings to
    re-guess an order it was already told. The richer half wins, so
    ``modified_at`` stays, and ``created_at`` with it (every manifest already
    records it; only the row dropped it).

    ``deliverable_count`` is three-valued on purpose: ``None`` means "not
    counted" (counting may cost a walk the genre chose not to pay), ``0``
    means counted and genuinely renderless — the project a surface must show
    as "no cut yet" rather than omit. ``meta`` is the same escape valve
    :attr:`Deliverable.meta` is: the host renders what it recognises and
    ignores the rest. A genre with internal drawers stamps a disambiguator
    there (muvid: ``{"muvid_genre": "footage"}``), because one genre
    registration may span several workspaces and a ``project_id`` may appear
    in more than one — hosts must not key merged rows by
    ``(genre, project_id)`` alone.
    """

    project_id: str
    title: str = ""
    genre: str = ""
    created_at: float | None = None
    modified_at: float | None = None
    deliverable_count: int | None = None
    meta: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        """The best short name for a human — the title if it has one, else the id.

        >>> ProjectSummary("we_ll_see", "We'll See").label
        "We'll See"
        >>> ProjectSummary("we_ll_see").label
        'we_ll_see'
        """
        return self.title or self.project_id


#: ``list_projects(email) -> list[ProjectSummary]`` — every project of this
#: caller's in the genre, newest-modified first, INCLUDING projects that have
#: never rendered. The genre is implicit: registration is per-genre, and the
#: host stamps the registry key onto any row whose ``genre`` arrives blank.
#:
#: The error contract, stated once so no genre re-invents it: an empty list is
#: a POSITIVE CLAIM — "nothing exists under this exact :func:`caller_key`" —
#: and an infrastructure failure must RAISE. The host surfaces a raise as a
#: per-genre problems entry; it never folds one into an empty result, because
#: a listing that silently omits a genre can honestly report "you have made
#: nothing" to a caller with work on disk (the ``except: continue`` defect).
#: A lister that degrades its own errors to ``[]`` rebuilds that defect one
#: layer down, where the host can no longer see it.
#:
#: Two boundaries a lister must keep: it MUST NOT create a workspace directory
#: just to list it — emptiness is the only signal there is, and minting the
#: directory corrupts it (whether an empty answer means "no work" or "never
#: seen this caller" is the HOST's copy to write, with the host's knowledge of
#: the allowlist). And rows are the caller's OWN projects; a genre MAY include
#: rows shared with the caller if it marks them (``meta["access"]="shared"``).
#:
#: The keyword ``after=`` is RESERVED for a future pagination cursor — a genre
#: must not define it to mean anything else.
ProjectLister = Callable[[str], "list[ProjectSummary]"]


#: The longest title :func:`check_title` accepts. Long enough for a real
#: episode title, short enough to render in one listing row.
MAX_TITLE_LEN = 120


def check_title(title: str) -> str:
    """Validate and normalise a human-assigned title; ``ValueError`` if refused.

    The shared half of the naming rule, so every genre refuses the same
    spellings rather than each inventing its own near-miss:

    - **Never ref-shaped.** A title the shared parser reads as an ordinal
      (``"cut 4"``, ``"#7"``, ``"12"``) is refused everywhere — a resolver
      that tries :func:`parse_ref` first (muvid's does today; any genre may
      tomorrow) would shadow it forever, so the user would have renamed their
      work into a name that resolves to a *different* artifact.
    - **Never path-shaped.** No separators, no ``.``/``..``, no control
      characters — a title participates in resolution, so it inherits the
      same hostility to traversal as any other id.

    The OTHER half — "does this collide with an existing artifact_id, title
    or filename in the genre's own namespace?" — is the genre's, because only
    the genre knows its namespace. The contract there: a collision raises
    ``ValueError`` naming the current holder, never silently reassigns.

    This governs titles assigned through :class:`Organiser`; genre CREATE
    paths keep their own (often looser) rules, so a render *created* as
    ``"12"`` may exist that ``organise`` would refuse to assign — asymmetric,
    and deliberate: organise is the door new names arrive through.

    >>> check_title("  The Slow Open ")
    'The Slow Open'
    >>> check_title("cut 4")
    Traceback (most recent call last):
    ...
    ValueError: 'cut 4' reads as a reference; pick a name that is not 'cut <n>', '#<n>' or a bare number
    >>> check_title("a/b")
    Traceback (most recent call last):
    ...
    ValueError: a title cannot contain path separators or control characters
    """
    t = (title or "").strip()
    if not t:
        raise ValueError("a title cannot be empty")
    if parse_ref(t) is not None:
        raise ValueError(
            f"{t!r} reads as a reference; pick a name that is not "
            f"'{REF_WORD} <n>', '#<n>' or a bare number"
        )
    if len(t) > MAX_TITLE_LEN:
        raise ValueError(f"a title is at most {MAX_TITLE_LEN} characters")
    if (
        any(c in t for c in "/\\\0")
        or t in (".", "..")
        or any(ord(c) < 32 for c in t)
    ):
        raise ValueError(
            "a title cannot contain path separators or control characters"
        )
    return t


@runtime_checkable
class Organiser(Protocol):
    """``organise(email, project_id, artifact_id, *, title=…, tags=…, note=…) -> Deliverable``

    The seam's fourth function: rename, tag and annotate a DELIVERABLE, owned
    by whoever assigns :attr:`Deliverable.ref` — never by the host. A
    host-owned label store is refused on the record (the asset-surfaces ADR
    §3.3): it mints a second naming vocabulary the resolvers cannot resolve,
    so the very name a page taught the user would fail in the download tool.
    Keeping naming genre-side is what makes names resolvable, because the same
    code owns the write and the lookup.

    This function arrives ONLY on the authenticated tool path. The signed
    download token stays read-only (ADR §3.4) — it is a forwardable bearer
    credential, safe precisely because it authorises reading one artifact and
    nothing else. Which is why the Protocol takes ``email`` first and
    authorizes exactly as :data:`Resolver` does, before anything is written.

    The durability contract — each guarantee one a real genre can keep:

    - **``artifact_id`` never changes, and files are never renamed or
      moved.** The id is what a signed token is minted against, and some
      locations are load-bearing (braidio's episode path is recorded in the
      annotation graph). A flat-set genre whose id is the file stem persists
      naming in a genre-owned sidecar, NOT by renaming the file — a rename is
      also how naming becomes destruction (``os.rename`` silently replaces an
      existing target).
    - **An accepted title resolves.** After ``organise(..., title=T)``
      succeeds, the genre's own ``resolve`` accepts ``T`` for this
      deliverable. Titles pass :func:`check_title`, and a collision with an
      existing name in the genre's namespace raises ``ValueError`` naming the
      holder. A genre whose ``ref`` IS its title mirrors the accepted title
      into ``ref`` — the label follows the rename; the id still does not.
    - **Partial update, all-or-nothing.** ``None`` means "leave unchanged";
      ``""`` clears the title or note, ``[]`` clears tags (replaced whole,
      never merged — read-modify-write is the caller's). A field the genre
      cannot persist raises ``ValueError`` naming it, and nothing is written.
    - **The return is a receipt, not an echo**: the Deliverable AS RE-READ
      from storage after the write, so the caller sees exactly what every
      later listing will — a genre that cannot re-read its own write has a
      durability bug this makes visible immediately.
    - **``tags`` and ``note`` surface in the returned Deliverable's ``meta``
      under the parameter's own names** (``meta["tags"]``, ``meta["note"]``).
      The spelling is pinned HERE, in the seam, so the write side and every
      listing renderer read one vocabulary.

    Raises ``KeyError`` (host: 404) when nothing resolves, ``PermissionError``
    (403) where "not yours" is safe to reveal, ``ValueError`` (400) when a
    requested change is refused. Deliberately NO delete: retrievability, cost
    and destruction are three separate predicates, and destruction does not
    ride a naming function — it would be a separately gated fifth function.
    """

    def __call__(
        self,
        email: str,
        project_id: str,
        artifact_id: str,
        *,
        title: "str | None" = None,
        tags: "list[str] | None" = None,
        note: "str | None" = None,
    ) -> Deliverable: ...


def safe_message(exc: BaseException) -> str:
    """An exception's message, reduced to what is safe to show a caller.

    The seam's exception vocabulary — ``KeyError`` / ``PermissionError`` /
    ``ValueError`` — passes through, because genre authors keep those
    messages path-free BY CONTRACT (never let a server path escape in the
    message; it is stated on :data:`Resolver` and it binds every seam
    function). Anything else — an ``OSError`` proudly carrying a server path
    — is reduced to its type name: a per-genre problems entry renders in a
    tool response a non-developer reads.

    >>> safe_message(KeyError("no render named 'x'"))
    'KeyError: "no render named \\'x\\'"'
    >>> safe_message(OSError("[Errno 13] /somewhere/private/thing.mp3"))
    'OSError'
    """
    if isinstance(exc, (KeyError, PermissionError, ValueError)):
        return f"{type(exc).__name__}: {exc}"
    return type(exc).__name__


#: The four halves a genre may register, in the order a capability report
#: prints. ``resolve`` is mandatory — a genre that cannot resolve a claim
#: cannot be served at all. The rest are optional, and ABSENCE IS A CAPABILITY
#: DECLARATION, not a hole: the host answers "this genre does not support
#: that" (and may report it), never errors on it, and NEVER falls back to a
#: store of its own.
DELIVERY_FUNCTIONS = ("resolve", "list", "list_projects", "organise")


def check_delivery_source(genre: str, entry: dict) -> dict:
    """Refuse a malformed registration at wiring time, not at first call.

    The registration map — ``{genre: {"resolve": fn, "list": fn,
    "list_projects": fn, "organise": fn}}`` — is assembled by hand in the
    deployment repo, which makes it the luckiest point of the whole seam: a
    key typo'd ``"list_project"`` does not fail, it silently disables the
    capability for that genre, and an unreachable failure is an invisible one
    (this module's founding story). Call this on each entry when building the
    map; it returns the entry unchanged so it composes inline.

    **This is a pure shape check and must stay one** — no I/O, no imports, no
    runtime state — so a deployment repo's CI can run it over the assembled
    map and catch the typo *before* the boot-time raise ever fires. The raise
    is the backstop, not the detector; growing a check here that needs a live
    store would put the whole-connector blast radius back.

    Release-ordering corollary: a new key ships HERE before any genre
    registers it, or an older nw on the box refuses a newer genre's honest
    registration.

    >>> entry = {"resolve": lambda e, p, a: None}
    >>> check_delivery_source("g", entry) is entry
    True
    >>> check_delivery_source("g", {"list_project": None})
    Traceback (most recent call last):
    ...
    ValueError: unknown delivery-source keys for genre 'g': ['list_project'] (known: ['resolve', 'list', 'list_projects', 'organise'])
    >>> check_delivery_source("g", {})
    Traceback (most recent call last):
    ...
    ValueError: delivery source for genre 'g' has no 'resolve' — a genre that cannot resolve a claim cannot be served
    """
    unknown = set(entry) - set(DELIVERY_FUNCTIONS)
    if unknown:
        raise ValueError(
            f"unknown delivery-source keys for genre {genre!r}: "
            f"{sorted(unknown)} (known: {list(DELIVERY_FUNCTIONS)})"
        )
    if "resolve" not in entry:
        raise ValueError(
            f"delivery source for genre {genre!r} has no 'resolve' — "
            "a genre that cannot resolve a claim cannot be served"
        )
    for key, fn in entry.items():
        if not callable(fn):
            raise ValueError(
                f"delivery-source entry {key!r} for genre {genre!r} is not callable"
            )
    return entry
