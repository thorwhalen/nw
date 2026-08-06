"""Freshness with **early cutoff** — what is *actually* out of date.

``descendants_of`` answers a reachability question: "what is downstream of
this?". :func:`stale_after` answers a freshness question: "what did this
change actually invalidate?". Those are different questions, and until this
module existed nw answered the second with the first — a one-line alias, so
editing one beat in a 200-shot project reported every descendant stale
whether or not anything about it had changed.

In *Build Systems à la Carte* terms that is the **Make** cell: a dirty-bit
rebuilder, "early cutoff: no". This module upgrades it to a **verifying
trace** rebuilder (Ninja, Shake, rustc/Salsa) using Salsa's *backdating*
idea: compare the value you have against the value the consumer recorded,
and stop when they agree. One 32-byte digest comparison replaces loading a
40 MB video, which is what makes cutoff free rather than pointless.

The rule, stated exactly
------------------------
An annotation ``X`` reachable from ``changed_id`` is **stale** when any of
these holds, and **fresh** only when none does:

- no verifying trace was recorded for ``X`` (:mod:`nw.bodies.verifying_trace`),
- the trace was written under a different digest scheme,
- the trace's upstream set is not exactly ``X.provenance.was_derived_from``,
- a recorded upstream annotation no longer exists,
- a recorded upstream is **itself stale** — its value is about to change,
- a recorded upstream's *current* value digest differs from the recorded one.

Two consequences worth stating, because both are easy to get backwards:

**The comparison lives on the edge, not on the node.** It is tempting to
classify ``X`` as fresh and then prune the walk there. That is wrong: ``X``
having up-to-date *inputs* says nothing about whether ``X``'s own *value*
still equals what its children recorded. Rewriting ``X`` in place makes
``X`` fresh and its children stale at the same instant. So every reachable
node is classified against **its own** recorded digests; the walk prunes
nothing.

**Unverifiable means stale.** Every branch above defaults to stale, which is
why this change needs no data migration: an annotation written before traces
existed reads as ``no-trace`` and behaves exactly as it did under pure
reachability. Over-reporting wastes a recompute — which the content-addressed
``falaw`` cache makes close to free. Under-reporting serves a stale artifact
as if it were current, so every ambiguous case resolves the other way.

What this does **not** catch
-----------------------------
Stated so nobody reads more into the number than is there:

- **A changed Transform.** The trace records upstream *values*, not the
  producing code. Bumping a Transform's implementation or prompt does not
  move any digest. ``stale_after`` answers "what did this *annotation* change
  invalidate", not "what did this *code* change invalidate".
- **A hand-edited output.** Editing ``X``'s body directly leaves its trace
  matching its parents, so ``X`` reads fresh. That is the intended reading —
  a deliberate override is not stale relative to its inputs — but it does
  mean "fresh" is not "would regenerate identically".
- **The plan → execute window.** The trace is written when the output is
  *persisted*, so an upstream mutated between planning and writing is
  recorded at its newer value. That needs a concurrent edit during a render.
- **The artifact tier.** Deliberately out of scope: ``lacing.Provenance.was_derived_from``
  is ``list[UUID]`` and cannot hold a 64-hex ``asset_id``, so artifact →
  artifact lineage is unrepresentable (thorwhalen/lacing#14). This module is
  the **annotation** tier only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import UUID

from lacing import Annotation
from lacing.digest import VALUE_DIGEST_SCHEME, annotation_value_digest

from .bodies.verifying_trace import (
    VERIFYING_TRACE_BODY_SCHEMA_URI,
    VerifyingTraceBodyV1,
)
from .graph import iter_all_annotations


# --- verdict reasons ---------------------------------------------------------
# Stable strings: they surface in reelee's freshness UI and in test assertions.

REASON_FRESH = "verified-fresh"
REASON_NO_TRACE = "no-trace"
REASON_SCHEME_CHANGED = "digest-scheme-changed"
REASON_TRACE_PARENTS_DIFFER = "trace-parents-differ"
REASON_TRACE_UNREADABLE = "trace-unreadable"
REASON_UPSTREAM_MISSING = "upstream-missing"
REASON_UPSTREAM_STALE = "upstream-stale"
REASON_UPSTREAM_CHANGED = "upstream-changed"
REASON_PROVENANCE_CYCLE = "provenance-cycle"

STALE_REASONS: tuple[str, ...] = (
    REASON_NO_TRACE,
    REASON_SCHEME_CHANGED,
    REASON_TRACE_PARENTS_DIFFER,
    REASON_TRACE_UNREADABLE,
    REASON_UPSTREAM_MISSING,
    REASON_UPSTREAM_STALE,
    REASON_UPSTREAM_CHANGED,
    REASON_PROVENANCE_CYCLE,
)
"""Every reason that resolves to *stale*. :data:`REASON_FRESH` is the only
verdict that does not, which is the invariant that keeps "unverifiable means
stale" true by construction rather than by review."""


@dataclass(frozen=True, slots=True)
class FreshnessVerdict:
    """Why one reachable annotation was judged stale (or not).

    Emitted by :func:`stale_verdicts`. ``reason`` is one of the
    ``REASON_*`` constants; ``upstream_id`` names the parent that decided it
    when a single parent did, so "why is this stale?" has an answer that does
    not require re-deriving the walk by hand.
    """

    annotation: Annotation
    is_stale: bool
    reason: str
    upstream_id: Optional[UUID] = None


def stale_verdicts(
    project_root: str | Path, changed_id: UUID
) -> list[FreshnessVerdict]:
    """Classify every annotation downstream of ``changed_id``.

    The explained form of :func:`stale_after`: one verdict per reachable
    annotation, stale or not, in a deterministic order (generation time, then
    id). ``changed_id`` itself is never included — it is the source of the
    change, not a derivative of it.

    Use this when the *number* is being questioned. ``stale_after`` is the
    same walk with the fresh verdicts dropped.
    """
    annotations = list(iter_all_annotations(project_root))
    by_id: dict[UUID, Annotation] = {a.id: a for a in annotations}
    traces = _traces_by_target(annotations)
    children = _children_index(annotations)
    reachable = _reachable_from(changed_id, children)

    digests: dict[UUID, Optional[str]] = {}

    def digest_of(ann: Annotation) -> Optional[str]:
        """Memoized value digest; ``None`` when the body cannot be digested."""
        if ann.id not in digests:
            try:
                digests[ann.id] = annotation_value_digest(ann)
            except Exception:
                # A body outside the JSON contract (lacing raises
                # NonStringBodyKeyError) cannot be compared. Catching is the
                # load-bearing part — a read-only freshness query must not
                # crash on one broken row. The sentinel's *value* is not: any
                # non-digest sentinel compares unequal to a recorded 64-hex
                # digest, so the caller reads it as "changed" either way.
                digests[ann.id] = None
        return digests[ann.id]

    verdicts: dict[UUID, FreshnessVerdict] = {}
    resolving: set[UUID] = set()

    def resolve(node_id: UUID) -> FreshnessVerdict:
        # Re-entry is impossible here: the only recursive caller is the parent
        # loop in `_classify`, which checks `resolving` itself so it can name
        # the cycle on the edge that closes it. Top-level calls always start
        # with `resolving` empty.
        cached = verdicts.get(node_id)
        if cached is not None:
            return cached
        resolving.add(node_id)
        try:
            verdict = _classify(node_id)
        finally:
            resolving.discard(node_id)
        verdicts[node_id] = verdict
        return verdict

    def _classify(node_id: UUID) -> FreshnessVerdict:
        ann = by_id[node_id]
        parents = tuple(dict.fromkeys(ann.provenance.was_derived_from))
        trace = traces.get(node_id)
        if trace is None:
            return FreshnessVerdict(ann, True, REASON_NO_TRACE)
        if trace.digest_scheme != VALUE_DIGEST_SCHEME:
            return FreshnessVerdict(ann, True, REASON_SCHEME_CHANGED)
        try:
            recorded = {
                UUID(entry.annotation_id): entry.value_digest
                for entry in trace.upstream
            }
        except (ValueError, AttributeError, TypeError):
            return FreshnessVerdict(ann, True, REASON_TRACE_UNREADABLE)
        if set(recorded) != set(parents):
            # The trace does not describe this annotation's current parents —
            # a parent added, removed or rewritten since. Nothing to compare.
            return FreshnessVerdict(ann, True, REASON_TRACE_PARENTS_DIFFER)
        for pid in parents:
            parent = by_id.get(pid)
            if parent is None:
                return FreshnessVerdict(ann, True, REASON_UPSTREAM_MISSING, pid)
            if pid in reachable:
                if pid in resolving:
                    # `pid` is an ancestor still being classified, so this edge
                    # closes a provenance cycle — malformed data. Reported on
                    # the edge that closes it rather than swallowed as a
                    # generic "upstream is stale", which is what a re-entry
                    # guard one level up would have produced (and which no
                    # caller could ever observe).
                    return FreshnessVerdict(ann, True, REASON_PROVENANCE_CYCLE, pid)
                if resolve(pid).is_stale:
                    return FreshnessVerdict(ann, True, REASON_UPSTREAM_STALE, pid)
            # ``digest_of`` returns None for a body lacing refuses to digest.
            # None is never equal to a recorded 64-hex digest, so the
            # undigestible case falls to "changed" by construction rather than
            # by a separate branch that no test could distinguish.
            if digest_of(parent) != recorded[pid]:
                return FreshnessVerdict(ann, True, REASON_UPSTREAM_CHANGED, pid)
        return FreshnessVerdict(ann, False, REASON_FRESH)

    ordered = sorted(
        (by_id[i] for i in reachable),
        key=lambda a: (a.provenance.generated_at_time.to_seconds(), str(a.id)),
    )
    return [resolve(ann.id) for ann in ordered]


def stale_after(project_root: str | Path, changed_id: UUID) -> list[Annotation]:
    """Return every annotation that ``changed_id`` actually invalidated.

    The freshness operation. ``changed_id``'s descendants are walked and each
    is checked against the upstream value digests it recorded when it was
    written (:mod:`nw.bodies.verifying_trace`). A descendant whose recorded
    inputs still match the current ones is **not** returned — that is the
    early cutoff, and it is why this is not ``descendants_of`` under another
    name. The full rule, and the four things it deliberately does not catch,
    are in this module's docstring.

    The returned list does NOT include ``changed_id`` itself (it is the source
    of the change, not a stale derivative).

    ``descendants_of`` is unchanged and still answers the reachability
    question — "what is downstream of this?" is legitimate and the two verbs
    are no longer synonyms. Use :func:`stale_verdicts` when you need the
    *reason* a given annotation is in (or out of) this set.
    """
    return [
        v.annotation for v in stale_verdicts(project_root, changed_id) if v.is_stale
    ]


# --- internals ---------------------------------------------------------------


def _traces_by_target(
    annotations: list[Annotation],
) -> dict[UUID, VerifyingTraceBodyV1]:
    """Index verifying traces by the annotation each describes.

    When more than one trace names the same target (an annotation re-added
    under the same id), the most recently generated one wins — it describes
    the state that is actually on disk.
    """
    best: dict[UUID, tuple[float, VerifyingTraceBodyV1]] = {}
    for ann in annotations:
        if ann.body_schema_uri != VERIFYING_TRACE_BODY_SCHEMA_URI:
            continue
        try:
            body = VerifyingTraceBodyV1.model_validate(ann.body)
            target = UUID(body.for_annotation_id)
        except Exception:
            continue
        at = ann.provenance.generated_at_time.to_seconds()
        current = best.get(target)
        if current is None or at >= current[0]:
            best[target] = (at, body)
    return {target: body for target, (_at, body) in best.items()}


def _children_index(annotations: list[Annotation]) -> dict[UUID, list[UUID]]:
    """``parent_id -> [child_id, ...]`` over ``provenance.was_derived_from``."""
    children: dict[UUID, list[UUID]] = {}
    for ann in annotations:
        for parent in ann.provenance.was_derived_from:
            children.setdefault(parent, []).append(ann.id)
    return children


def _reachable_from(ancestor_id: UUID, children: dict[UUID, list[UUID]]) -> set[UUID]:
    """Transitive closure of ``children`` from ``ancestor_id``, excluding it."""
    seen: set[UUID] = set()
    frontier = list(children.get(ancestor_id, ()))
    while frontier:
        cur = frontier.pop()
        if cur in seen:
            continue
        seen.add(cur)
        frontier.extend(children.get(cur, ()))
    return seen
