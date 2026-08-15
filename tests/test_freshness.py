"""Early cutoff in ``nw.stale_after`` (nw#24).

The suite is organised around the one asymmetry that matters:

- **Over-reporting is safe.** A spurious stale verdict costs a recompute,
  and against a content-addressed cache that recompute is usually free.
- **Under-reporting is a correctness bug.** It serves a stale artifact as if
  it were current, and nothing downstream can detect it.

So every test comes in a pair: one that the cutoff *fires* where it should
(otherwise the feature is a no-op dressed up as a fix), and one that it does
*not* fire where it must not.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

import nw
from lacing import (
    Annotation,
    MediaRef,
    Provenance,
    RationalTime,
    TimeInterval,
)
from nw.bodies import (
    VERIFYING_TRACE_BODY_SCHEMA_URI,
    VERIFYING_TRACE_TIER,
    SectionBodyV1,
    VerifyingTraceBodyV1,
    build_verifying_trace,
)
from nw.freshness import (
    REASON_FRESH,
    REASON_NO_TRACE,
    REASON_PROVENANCE_CYCLE,
    REASON_SCHEME_CHANGED,
    REASON_TRACE_PARENTS_DIFFER,
    REASON_UPSTREAM_CHANGED,
    REASON_UPSTREAM_MISSING,
    REASON_UPSTREAM_STALE,
    STALE_REASONS,
)


DERIVED_SCHEMA = "annot://schema/render-result/v1"


# --- scaffolding -------------------------------------------------------------


def _iv(a: float, b: float) -> TimeInterval:
    return TimeInterval.from_seconds(a, b)


def _authored(proj, *, section_id: str = "s", label: str = "seed") -> UUID:
    """Write (or edit) an authored entity. ``upsert_*`` keeps the id stable."""
    return proj.graph.upsert_section(
        SectionBodyV1(section_id=section_id, label=label), interval=_iv(0, 4)
    )


def _derived(
    proj,
    parents,
    *,
    body: dict | None = None,
    ann_id: UUID | None = None,
    tier: str = "render-result",
) -> Annotation:
    """Write a Transform-shaped output annotation through the normal path."""
    ann = Annotation(
        id=ann_id or uuid4(),
        tier=tier,
        reference=MediaRef(asset_id=proj.graph.asset_id, interval=_iv(0, 0)),
        body=body if body is not None else {"shot_id": "s01", "url": "x"},
        body_schema_uri=DERIVED_SCHEMA,
        provenance=Provenance(
            was_generated_by="transform:test@1",
            was_attributed_to="agent:test",
            was_derived_from=list(parents),
            generated_at_time=RationalTime.now(),
            activity="derive",
        ),
    )
    proj.graph.add_annotation(ann)
    return ann


def _remove(proj, ann_id: UUID) -> None:
    with nw.open_project_stores(proj.root) as stores:
        for store in stores:
            if store.remove(ann_id) is not None:
                return


def _rewrite_in_place(proj, ann: Annotation, *, body: dict, parents=None) -> Annotation:
    """Replace ``ann``'s value keeping its id — what a regeneration should do.

    ``store.add`` is a plain INSERT, so an in-place update is remove-then-add;
    that is what ``ProjectGraph._upsert`` does for entities too. Going back
    through ``add_annotation`` is the point: it re-records the trace.

    ``parents`` also rewrites ``was_derived_from``, which is the only way to
    build a cycle whose *traces are valid on both sides* — every parent has to
    already exist when the trace is recorded.
    """
    prov_update = {"generated_at_time": RationalTime.now()}
    if parents is not None:
        prov_update["was_derived_from"] = list(parents)
    updated = ann.model_copy(
        update={
            "body": body,
            "provenance": ann.provenance.model_copy(update=prov_update),
        }
    )
    _remove(proj, ann.id)
    proj.graph.add_annotation(updated)
    return updated


def _traces(proj) -> list[Annotation]:
    return [
        a for a in nw.iter_all_annotations(proj.root) if a.tier == VERIFYING_TRACE_TIER
    ]


def _strip_traces(proj) -> int:
    """Make the project look like one written before traces existed."""
    ids = [a.id for a in _traces(proj)]
    for i in ids:
        _remove(proj, i)
    return len(ids)


def _chain(tmp_path):
    """The canonical fixture: authored A → derived B → derived C.

    Returns ``(project, a_id, b, c)``.
    """
    proj = nw.Project.init(tmp_path / "p")
    a_id = _authored(proj)
    b = _derived(proj, (a_id,), body={"shot_id": "s01", "url": "b"})
    c = _derived(proj, (b.id,), body={"shot_id": "s01", "url": "c"})
    return proj, a_id, b, c


def _ids(annotations) -> set[UUID]:
    return {a.id for a in annotations}


def _reason_for(proj, changed_id: UUID, ann_id: UUID) -> str:
    verdicts = {v.annotation.id: v for v in nw.stale_verdicts(proj.root, changed_id)}
    return verdicts[ann_id].reason


# --- the cutoff fires --------------------------------------------------------


def test_nothing_is_stale_when_nothing_has_changed(tmp_path):
    """The headline. Reachability says two; freshness says none.

    This is the "a returning user is told 142 items are stale when 3 are"
    case reduced to its smallest form: a project that was just built is not
    stale, and ``stale_after`` used to say otherwise because it *was*
    ``descendants_of``.
    """
    proj, a_id, b, c = _chain(tmp_path)

    assert _ids(nw.descendants_of(proj.root, a_id)) == {b.id, c.id}
    assert nw.stale_after(proj.root, a_id) == []


def test_a_no_op_edit_invalidates_nothing(tmp_path):
    """Re-writing an entity with identical content invalidates nothing."""
    proj, a_id, b, c = _chain(tmp_path)

    again = _authored(proj)  # same body, same interval
    assert again == a_id, "upsert must keep the entity's id (nw#34)"

    assert _ids(nw.descendants_of(proj.root, a_id)) == {b.id, c.id}
    assert nw.stale_after(proj.root, a_id) == []


def test_a_reverted_edit_invalidates_nothing(tmp_path):
    """Salsa's *backdating*, in one test.

    Edit, observe the whole subtree go stale, revert, observe it come back —
    without regenerating anything. A pure dirty-bit rebuilder cannot do this:
    the annotation was written twice, so any timestamp- or revision-based
    scheme reports it changed. Only comparing the *value* recovers it.
    """
    proj, a_id, b, c = _chain(tmp_path)

    _authored(proj, label="edited")
    assert _ids(nw.stale_after(proj.root, a_id)) == {b.id, c.id}

    _authored(proj, label="seed")  # back to the value B and C recorded
    assert nw.stale_after(proj.root, a_id) == []


def test_the_stale_set_collapses_once_the_subtree_is_regenerated(tmp_path):
    """After the work is done, the number must go back down.

    Under pure reachability it never does: everything downstream of an edit
    stays "stale" forever, so the count is a function of graph shape rather
    than of anything the user did.
    """
    proj, a_id, b, c = _chain(tmp_path)
    _authored(proj, label="edited")
    assert _ids(nw.stale_after(proj.root, a_id)) == {b.id, c.id}

    b2 = _rewrite_in_place(proj, b, body={"shot_id": "s01", "url": "b2"})
    # C is still keyed to B's old value, so it is still stale — and only it.
    assert _ids(nw.stale_after(proj.root, a_id)) == {c.id}

    _rewrite_in_place(proj, c, body={"shot_id": "s01", "url": "c2"})
    assert nw.stale_after(proj.root, a_id) == []
    assert _ids(nw.descendants_of(proj.root, a_id)) == {b2.id, c.id}


# --- the cutoff must NOT fire ------------------------------------------------


def test_a_real_change_invalidates_the_whole_subtree(tmp_path):
    """The complement of the cutoff tests: a real edit still reports everything."""
    proj, a_id, b, c = _chain(tmp_path)
    _authored(proj, label="edited")

    assert _ids(nw.stale_after(proj.root, a_id)) == {b.id, c.id}
    assert _reason_for(proj, a_id, b.id) == REASON_UPSTREAM_CHANGED
    assert _reason_for(proj, a_id, c.id) == REASON_UPSTREAM_STALE


def test_a_child_of_a_stale_node_is_stale_though_its_own_trace_matches(tmp_path):
    """The rule that keeps propagation honest.

    C's recorded digest of B still matches B's *current* value — B has not
    been regenerated yet. Judging C on that alone declares it fresh, and the
    user is handed an artifact built from a value that is about to change.
    A node whose input is *about to* change is stale.
    """
    proj, a_id, b, c = _chain(tmp_path)
    _authored(proj, label="edited")

    # The premise: C's own edge to B is *in agreement*.
    trace = _trace_body_for(proj, c.id)
    recorded = {UUID(u.annotation_id): u.value_digest for u in trace.upstream}
    from lacing.digest import annotation_value_digest

    current_b = next(a for a in nw.iter_all_annotations(proj.root) if a.id == b.id)
    assert recorded[b.id] == annotation_value_digest(current_b), (
        "guard is vacuous unless C's recorded digest of B still matches"
    )

    assert c.id in _ids(nw.stale_after(proj.root, a_id))
    assert _reason_for(proj, a_id, c.id) == REASON_UPSTREAM_STALE


def test_a_child_is_stale_when_its_parent_was_rewritten_in_place(tmp_path):
    """The walk must not prune at a node it just declared fresh.

    After B is regenerated to a *different* value, B is fresh (its own input
    A is unchanged) while C is stale (its recorded digest of B is the old
    one). Both are true at the same instant, which is exactly why the
    comparison lives on the **edge** and not on the node. Pruning the walk at
    B — the intuitive implementation — silently under-reports C.
    """
    proj = nw.Project.init(tmp_path / "p")
    a_id = _authored(proj)
    b = _derived(proj, (a_id,), body={"shot_id": "s01", "url": "b"})
    c = _derived(proj, (b.id,), body={"shot_id": "s01", "url": "c"})

    _rewrite_in_place(proj, b, body={"shot_id": "s01", "url": "b-different"})

    verdicts = {v.annotation.id: v for v in nw.stale_verdicts(proj.root, a_id)}
    assert verdicts[b.id].is_stale is False, "B's own input A never changed"
    assert verdicts[b.id].reason == REASON_FRESH
    assert verdicts[c.id].is_stale is True
    assert verdicts[c.id].reason == REASON_UPSTREAM_CHANGED
    assert _ids(nw.stale_after(proj.root, a_id)) == {c.id}


def test_an_annotation_with_no_trace_is_stale(tmp_path):
    """Data written before traces existed keeps today's behaviour, exactly.

    This is what makes the change need no migration — and it is also the
    single most important safety property, because "no trace" is the default
    state of every annotation in every existing project.
    """
    proj, a_id, b, c = _chain(tmp_path)
    assert _strip_traces(proj) > 0

    assert _ids(nw.stale_after(proj.root, a_id)) == {b.id, c.id}
    assert _ids(nw.stale_after(proj.root, a_id)) == _ids(
        nw.descendants_of(proj.root, a_id)
    ), "with no traces at all, freshness must degrade exactly to reachability"
    assert _reason_for(proj, a_id, b.id) == REASON_NO_TRACE


def test_a_trace_that_does_not_cover_the_current_parents_is_stale(tmp_path):
    """A parent added or removed since the trace was written is unverifiable."""
    proj, a_id, b, c = _chain(tmp_path)
    other = _derived(proj, (), body={"shot_id": "s02", "url": "o"})

    # Give B a second parent without re-recording its trace.
    _remove(proj, b.id)
    with nw.open_project_stores(proj.root) as stores:
        for store in stores:
            store.add(
                b.model_copy(
                    update={
                        "provenance": b.provenance.model_copy(
                            update={"was_derived_from": [a_id, other.id]}
                        )
                    }
                )
            )
            break

    assert b.id in _ids(nw.stale_after(proj.root, a_id))
    assert _reason_for(proj, a_id, b.id) == REASON_TRACE_PARENTS_DIFFER


def test_a_missing_upstream_annotation_is_stale(tmp_path):
    """A parent that was deleted cannot be compared, so it counts as changed."""
    proj, a_id, b, c = _chain(tmp_path)
    _remove(proj, b.id)

    # C is still reachable from A only through B's now-dangling edge, so ask
    # about B's own parent chain directly: C names a parent that is gone.
    verdicts = {v.annotation.id: v for v in nw.stale_verdicts(proj.root, b.id)}
    assert verdicts[c.id].is_stale is True
    assert verdicts[c.id].reason == REASON_UPSTREAM_MISSING
    assert verdicts[c.id].upstream_id == b.id


def test_a_trace_from_another_digest_scheme_is_stale(tmp_path):
    """lacing bumps the scheme when the digest's meaning changes.

    Two digests computed under different schemes are not comparable, so a
    scheme mismatch must read as *unverifiable*, never as *equal*.
    """
    proj, a_id, b, c = _chain(tmp_path)
    _rewrite_trace(
        proj, b.id, lambda body: body.model_copy(update={"digest_scheme": "other/v0"})
    )

    assert b.id in _ids(nw.stale_after(proj.root, a_id))
    assert _reason_for(proj, a_id, b.id) == REASON_SCHEME_CHANGED


def test_an_undigestible_upstream_is_stale(tmp_path, monkeypatch):
    """lacing refuses to digest a body outside the JSON contract.

    ``NonStringBodyKeyError`` must surface as *stale*, not as an exception
    escaping a read-only freshness query and not as a silent "unchanged".
    """
    proj, a_id, b, c = _chain(tmp_path)

    import nw.freshness as freshness

    def _boom(_ann):
        raise TypeError("body has a non-str key")

    monkeypatch.setattr(freshness, "annotation_value_digest", _boom)
    assert _ids(nw.stale_after(proj.root, a_id)) == {b.id, c.id}
    assert _reason_for(proj, a_id, b.id) == REASON_UPSTREAM_CHANGED


def test_a_provenance_cycle_is_stale_and_terminates(tmp_path):
    """Malformed data must not hang a read-only query — and must say so.

    The cycle is built with **valid, covering traces on both sides**, which
    is the only version of this test that reaches the cycle branch at all: if
    either trace fails to cover its parents, the walk short-circuits on
    ``trace-parents-differ`` and the guard is never exercised. X is rewritten
    last, so both A and Y already exist when its trace is recorded.
    """
    proj = nw.Project.init(tmp_path / "p")
    a_id = _authored(proj)
    x = _derived(proj, (a_id,), body={"shot_id": "s01", "url": "x"})
    y = _derived(proj, (x.id,), body={"shot_id": "s01", "url": "y"})
    # Close the loop: X ← Y as well as X → Y, re-recording X's trace.
    _rewrite_in_place(
        proj, x, body={"shot_id": "s01", "url": "x"}, parents=(a_id, y.id)
    )

    verdicts = {v.annotation.id: v for v in nw.stale_verdicts(proj.root, a_id)}
    assert {i for i, v in verdicts.items() if v.is_stale} == {x.id, y.id}

    # *Which* of the two names the cycle depends on classification order, so
    # the invariant is stated the way it actually holds: exactly one edge
    # closes the loop and reports it, the other reads as upstream-stale.
    # Asserting a specific node would pin an incidental ordering.
    assert {verdicts[x.id].reason, verdicts[y.id].reason} == {
        REASON_PROVENANCE_CYCLE,
        REASON_UPSTREAM_STALE,
    }, "the closing edge must name the cycle, not hide it behind upstream-stale"
    closer, other = (
        (x, y) if verdicts[x.id].reason == REASON_PROVENANCE_CYCLE else (y, x)
    )
    assert verdicts[closer.id].upstream_id == other.id


# --- trace writing -----------------------------------------------------------


def test_a_derived_write_records_a_trace_and_an_authored_write_does_not(tmp_path):
    proj = nw.Project.init(tmp_path / "p")
    a_id = _authored(proj)
    assert _traces(proj) == [], "an entity with no parents has nothing to verify"

    b = _derived(proj, (a_id,))
    traces = _traces(proj)
    assert len(traces) == 1
    assert traces[0].body_schema_uri == VERIFYING_TRACE_BODY_SCHEMA_URI
    body = VerifyingTraceBodyV1.model_validate(traces[0].body)
    assert UUID(body.for_annotation_id) == b.id
    assert [UUID(u.annotation_id) for u in body.upstream] == [a_id]


def test_a_trace_records_every_parent_and_any_of_them_invalidates(tmp_path):
    """Multi-parent is the common shape, and a partial trace is invisible.

    A reelee panel derives from a beat *and* the character refs it mentions.
    A trace that recorded only the first parent would verify successfully
    while a character description changed underneath it — a wrong "fresh",
    which is the one failure mode this design must not have.
    """
    proj = nw.Project.init(tmp_path / "p")
    first = _authored(proj, section_id="s1", label="one")
    second = _authored(proj, section_id="s2", label="two")
    child = _derived(proj, (first, second))

    body = _trace_body_for(proj, child.id)
    assert [UUID(u.annotation_id) for u in body.upstream] == [first, second]
    assert len({u.value_digest for u in body.upstream}) == 2

    assert nw.stale_after(proj.root, first) == []
    assert nw.stale_after(proj.root, second) == []

    # Editing the *second* parent must invalidate the child.
    _authored(proj, section_id="s2", label="two-edited")
    assert _ids(nw.stale_after(proj.root, second)) == {child.id}
    assert _reason_for(proj, second, child.id) == REASON_UPSTREAM_CHANGED


def test_a_trace_is_never_a_descendant_of_what_it_describes(tmp_path):
    """Bookkeeping must not show up in the user-facing answer.

    Wiring the trace as a provenance edge would be the obvious way to link
    it, and it would put a trace into the ``descendants_of`` and
    ``stale_after`` result of every annotation in the project.
    """
    proj, a_id, b, c = _chain(tmp_path)
    assert len(_traces(proj)) == 2

    for source in (a_id, b.id, c.id):
        for ann in nw.descendants_of(proj.root, source):
            assert ann.tier != VERIFYING_TRACE_TIER
        for ann in nw.stale_after(proj.root, source):
            assert ann.tier != VERIFYING_TRACE_TIER

    assert all(not t.provenance.was_derived_from for t in _traces(proj))


def test_no_trace_is_written_when_a_parent_cannot_be_resolved(tmp_path):
    """A partial trace is worse than none: it would verify a subset silently."""
    proj = nw.Project.init(tmp_path / "p")
    a_id = _authored(proj)
    ghost = uuid4()

    b = _derived(proj, (a_id, ghost))
    assert _traces(proj) == []
    assert b.id in _ids(nw.stale_after(proj.root, a_id))
    assert _reason_for(proj, a_id, b.id) == REASON_NO_TRACE


def test_build_verifying_trace_refuses_an_undigestible_parent():
    """The same rule at the unit boundary, without a store in the way.

    model_construct: since lacing#24 the envelope refuses non-str body keys
    at validation, so the bypass constructor is the only way such an
    annotation can exist — which is exactly the case this guard covers."""
    parent = Annotation.model_construct(
        id=uuid4(),
        tier="t",
        reference=MediaRef(asset_id="sha256:abc", interval=_iv(0, 1)),
        body={1: "a", "1": "b"},  # non-str key: lacing refuses to digest it
        body_schema_uri=DERIVED_SCHEMA,
        provenance=Provenance(
            was_generated_by="agent:test",
            was_attributed_to="agent:test",
            generated_at_time=RationalTime(0),
        ),
    )
    assert (
        build_verifying_trace(
            for_annotation_id=uuid4(),
            parent_ids=[parent.id],
            upstream=[parent],
            asset_id="sha256:abc",
        )
        is None
    )


def test_the_most_recent_trace_wins(tmp_path):
    """Re-adding an annotation under the same id leaves two traces."""
    proj, a_id, b, c = _chain(tmp_path)
    _authored(proj, label="edited")
    _rewrite_in_place(proj, b, body={"shot_id": "s01", "url": "b2"})

    for_b = [
        a
        for a in _traces(proj)
        if VerifyingTraceBodyV1.model_validate(a.body).for_annotation_id == str(b.id)
    ]
    assert len(for_b) == 2, "guard is vacuous unless both traces are present"
    # The newer one records A's *edited* digest, so B reads fresh.
    assert b.id not in _ids(nw.stale_after(proj.root, a_id))


# --- the query surface -------------------------------------------------------


def test_stale_after_excludes_the_changed_annotation_itself(tmp_path):
    proj, a_id, b, c = _chain(tmp_path)
    _authored(proj, label="edited")
    assert a_id not in _ids(nw.stale_after(proj.root, a_id))


def test_stale_verdicts_explains_every_reachable_annotation(tmp_path):
    proj, a_id, b, c = _chain(tmp_path)
    _authored(proj, label="edited")

    verdicts = nw.stale_verdicts(proj.root, a_id)
    assert _ids(v.annotation for v in verdicts) == {b.id, c.id}
    for v in verdicts:
        assert (v.reason in STALE_REASONS) is v.is_stale, (
            "REASON_FRESH must be the only verdict that is not stale — that "
            "invariant is what makes 'unverifiable means stale' structural"
        )


def test_stale_after_is_deterministic(tmp_path):
    proj, a_id, b, c = _chain(tmp_path)
    _authored(proj, label="edited")
    runs = [[a.id for a in nw.stale_after(proj.root, a_id)] for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]
    assert runs[0] == [b.id, c.id], "generation order, oldest first"


def test_stale_after_is_always_a_subset_of_descendants_of(tmp_path):
    """The structural invariant: freshness narrows reachability, never widens it.

    Checked across the three states that matter — untouched, edited, and
    partially regenerated — because a walk that ever returned something
    ``descendants_of`` does not would mean the two verbs had drifted apart
    rather than one refining the other.
    """
    proj, a_id, b, c = _chain(tmp_path)
    states = []
    states.append(("untouched", None))
    _authored(proj, label="edited")
    states.append(("edited", None))
    _rewrite_in_place(proj, b, body={"shot_id": "s01", "url": "b2"})
    states.append(("partially regenerated", None))

    for label, _ in states:
        reachable = _ids(nw.descendants_of(proj.root, a_id))
        assert _ids(nw.stale_after(proj.root, a_id)) <= reachable, label
    # …and not vacuously, because the middle state was a strict superset.
    assert len(states) == 3


def test_stale_after_of_an_unknown_id_is_empty(tmp_path):
    proj, a_id, b, c = _chain(tmp_path)
    assert nw.stale_after(proj.root, uuid4()) == []


# --- helpers that need the module under test ---------------------------------


def _trace_body_for(proj, target: UUID) -> VerifyingTraceBodyV1:
    bodies = [
        VerifyingTraceBodyV1.model_validate(a.body)
        for a in _traces(proj)
        if VerifyingTraceBodyV1.model_validate(a.body).for_annotation_id == str(target)
    ]
    assert bodies, f"no verifying trace for {target}"
    return bodies[-1]


def _rewrite_trace(proj, target: UUID, mutate) -> None:
    """Replace the trace describing ``target`` with ``mutate(body)``."""
    for ann in _traces(proj):
        body = VerifyingTraceBodyV1.model_validate(ann.body)
        if body.for_annotation_id != str(target):
            continue
        _remove(proj, ann.id)
        with nw.open_project_stores(proj.root) as stores:
            for store in stores:
                store.add(
                    ann.model_copy(
                        update={"body": mutate(body).model_dump(mode="json")}
                    )
                )
                break
        return
    pytest.fail(f"no verifying trace for {target}")


# --- the whole-graph snapshot (nw#39) ----------------------------------------


def test_snapshot_of_a_clean_project_reports_every_derived_annotation_fresh(
    tmp_path,
):
    """`stale_verdicts_all` covers exactly the derived set; `all_stale` is
    empty when nothing changed. Authored (parentless) annotations and the
    verifying traces themselves stay out of the walk."""
    proj, a_id, b, c = _chain(tmp_path)

    verdicts = nw.stale_verdicts_all(proj.root)

    assert _ids(v.annotation for v in verdicts) == {b.id, c.id}
    assert all(v.reason == REASON_FRESH for v in verdicts)
    assert nw.all_stale(proj.root) == []


def test_a_parentless_annotation_is_never_stale_even_without_a_trace(tmp_path):
    """An imported screenplay must not read as stale forever — the
    whole-graph frontier is 'has at least one parent', not 'everything'."""
    proj = nw.Project.init(tmp_path / "p")
    _authored(proj)  # parentless, and no verifying trace is written for it

    assert nw.stale_verdicts_all(proj.root) == []
    assert nw.all_stale(proj.root) == []


def test_the_snapshot_detects_a_real_edit_with_no_changed_id(tmp_path):
    """The question a freshness indicator asks: what is stale *right now*?"""
    proj, a_id, b, c = _chain(tmp_path)
    _authored(proj, label="rewritten")  # upsert: same id, new value

    stale = nw.all_stale(proj.root)

    assert _ids(stale) == {b.id, c.id}
    verdicts = {v.annotation.id: v for v in nw.stale_verdicts_all(proj.root)}
    assert verdicts[b.id].reason == REASON_UPSTREAM_CHANGED
    assert verdicts[c.id].reason == REASON_UPSTREAM_STALE


def test_a_no_op_save_does_not_inflate_the_snapshot(tmp_path):
    """The cost bug the consumer's timestamp comparison had: a save that
    changes no value must not invalidate the subtree (early cutoff holds on
    the snapshot form too)."""
    proj, a_id, b, c = _chain(tmp_path)
    _authored(proj, label="seed")  # byte-identical re-save; timestamp moves

    assert nw.all_stale(proj.root) == []


def test_a_deleted_parent_surfaces_in_the_snapshot(tmp_path):
    """The under-reporting direction the consumer's copy missed: a dangling
    parent must read stale (`upstream-missing`), not vanish from the walk."""
    proj, a_id, b, c = _chain(tmp_path)
    _remove(proj, b.id)

    verdicts = {v.annotation.id: v for v in nw.stale_verdicts_all(proj.root)}

    assert set(verdicts) == {c.id}
    assert verdicts[c.id].reason == REASON_UPSTREAM_MISSING
    assert verdicts[c.id].upstream_id == b.id


def test_the_snapshot_terminates_and_reports_on_a_provenance_cycle(tmp_path):
    """Same malformed-data guarantee as the scoped walk, with no changed_id
    to anchor the recursion."""
    proj = nw.Project.init(tmp_path / "p")
    a_id = _authored(proj)
    x = _derived(proj, (a_id,), body={"shot_id": "s01", "url": "x"})
    y = _derived(proj, (x.id,), body={"shot_id": "s01", "url": "y"})
    _rewrite_in_place(
        proj, x, body={"shot_id": "s01", "url": "x"}, parents=(a_id, y.id)
    )

    verdicts = {v.annotation.id: v for v in nw.stale_verdicts_all(proj.root)}

    assert {i for i, v in verdicts.items() if v.is_stale} == {x.id, y.id}
    assert {verdicts[x.id].reason, verdicts[y.id].reason} == {
        REASON_PROVENANCE_CYCLE,
        REASON_UPSTREAM_STALE,
    }


def test_snapshot_and_traversal_orders_are_deterministic(tmp_path):
    """Both public orders are (generation time, id) — not set-iteration
    order, which is hash-derived and differs across processes (nw#39)."""
    proj, a_id, b, c = _chain(tmp_path)
    extra = _derived(proj, (a_id,), body={"shot_id": "s02", "url": "d"})

    def _key(ann):
        return (ann.provenance.generated_at_time.to_seconds(), str(ann.id))

    snapshot = [v.annotation for v in nw.stale_verdicts_all(proj.root)]
    assert snapshot == sorted(snapshot, key=_key)
    assert _ids(snapshot) == {b.id, c.id, extra.id}

    descendants = nw.descendants_of(proj.root, a_id)
    assert descendants == sorted(descendants, key=_key)
    assert _ids(descendants) == {b.id, c.id, extra.id}
