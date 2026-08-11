"""Early cutoff on the *costed* path — the completion of nw#24.

``tests/test_freshness.py`` proves the verifying-trace machinery over
synthetic bodies. This file pins the composition that machinery exists for:
a **re-render whose fal URL changed but whose bytes did not** must invalidate
nothing downstream.

That sentence was structurally false before falaw 0.0.24. ``Artifact.asset_id``
was ``sha256(url)`` and fal mints a fresh URL per generation, so a
byte-identical re-render changed the ``artifact_id`` in the render body, which
changed the body's value digest, which invalidated every descendant — nw#24's
exact words: "cutoff never fires for exactly the annotations that cost money."
falaw#14 made ``asset_id`` the SHA-256 of the *bytes*, and
``BaseTransform._complete_annotation`` writes that content id — not the URL —
into the body, which is what these tests hold in place.

The pair, per this suite's convention:

* the cutoff **fires** — new URL, same bytes, downstream stays fresh;
* the cutoff does **not** fire — new URL, *new* bytes, downstream is stale.

Everything runs through the real seams: ``falaw.execute_plan`` (with the fal
response stubbed and the asset transport served from memory by the conftest's
``fake_assets``), ``BaseTransform.execute``, ``ProjectGraph.add_annotation``
(which records the verifying trace), and ``nw.stale_after``.
"""

from __future__ import annotations

import sys
import types
from uuid import UUID, uuid4

import pytest

import nw
from lacing import (
    Annotation,
    MediaRef,
    Provenance,
    RationalTime,
    TimeInterval,
    hash_bytes,
)
from nw.bodies import SectionBodyV1
from nw.freshness import REASON_UPSTREAM_CHANGED
from nw.transforms import BaseTransform


IMG_A = b"\x89PNG-pretend-image-A" * 4
IMG_B = b"\x89PNG-pretend-image-B" * 4

RENDER_SCHEMA = "annot://schema/render-result/v1"


# --- a fal_client stub whose asset URLs the test controls --------------------
# Mirrors falaw's own tests/test_content_addressing.py::FakeFal. The conftest's
# `fake_assets` fixture decides what bytes each URL serves; this stub decides
# which URL the "render" returns — together they express "fal mints a fresh
# URL for the same (or different) bytes".


class _FakeFal:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.next_image_url = "http://cdn/img-1.png"

    def subscribe(self, application, *, arguments, with_logs, on_queue_update):
        self.calls.append({"application": application, "arguments": dict(arguments)})
        return {"images": [{"url": self.next_image_url, "content_type": "image/png"}]}


@pytest.fixture
def fal(monkeypatch):
    stub = _FakeFal()
    monkeypatch.setitem(
        sys.modules,
        "fal_client",
        types.SimpleNamespace(
            InProgress=type("IP", (), {"__init__": lambda s, logs: None}),
            subscribe=stub.subscribe,
        ),
    )
    return stub


# --- scaffolding -------------------------------------------------------------


def _iv(a: float, b: float) -> TimeInterval:
    return TimeInterval.from_seconds(a, b)


def _image_plan():
    from falaw import CallPlan, Plan

    return Plan(
        calls=(
            CallPlan(
                tool="generate_image",
                application="fal-ai/flux/dev",
                arguments={"prompt": "a tiger"},
                output_kind="image",
                estimated_cost_usd=0.02,
            ),
        )
    )


def _skeleton(proj, parents, *, ann_id: UUID | None = None) -> Annotation:
    """A plan-time render skeleton: ``artifact_id`` empty until execute.

    Deliberately *not* written to the graph — ``BaseTransform.execute`` writes
    the completed annotation, exactly as the render-strategy adapters do.
    """
    return Annotation(
        id=ann_id or uuid4(),
        tier="render-result",
        reference=MediaRef(asset_id=proj.graph.asset_id, interval=_iv(0, 0)),
        body={"shot_id": "s01", "strategy": "test", "artifact_id": None},
        body_schema_uri=RENDER_SCHEMA,
        provenance=Provenance(
            was_generated_by="transform:test@1",
            was_attributed_to="agent:test",
            was_derived_from=list(parents),
            generated_at_time=RationalTime.now(),
            activity="derive",
        ),
    )


def _derived(proj, parents, *, body: dict) -> Annotation:
    """A downstream derived annotation, written through the normal path."""
    ann = Annotation(
        id=uuid4(),
        tier="render-result",
        reference=MediaRef(asset_id=proj.graph.asset_id, interval=_iv(0, 0)),
        body=body,
        body_schema_uri=RENDER_SCHEMA,
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


def _rendered_project(tmp_path, fal, fake_assets, *, run1_bytes=IMG_A):
    """Author a shot, render it once, derive something downstream.

    Returns ``(proj, rendered, downstream)`` where ``rendered`` carries the
    content-addressed ``artifact_id`` of ``run1_bytes`` and ``downstream``'s
    verifying trace records ``rendered`` at that value.
    """
    proj = nw.Project.init(tmp_path / "p")
    shot_id = proj.graph.upsert_section(
        SectionBodyV1(section_id="s", label="the shot"), interval=_iv(0, 4)
    )

    fal.next_image_url = "http://cdn/img-run1.png"
    fake_assets.serve("http://cdn/img-run1.png", run1_bytes)
    result = BaseTransform().execute(
        proj, _image_plan(), (_skeleton(proj, (shot_id,)),)
    )
    rendered = result.annotations[0]

    downstream = _derived(
        proj, (rendered.id,), body={"animatic_of": rendered.body["artifact_id"]}
    )
    return proj, rendered, downstream


def _rerender_in_place(
    proj, fal, fake_assets, rendered: Annotation, *, url: str, data: bytes
) -> Annotation:
    """Force a re-render of ``rendered`` under the same annotation id.

    fal mints a new URL (``url``) serving ``data``; ``force=True`` bypasses the
    falaw cache so the call genuinely re-executes. Remove-then-add is the
    in-place idiom (``store.add`` is a plain INSERT — see
    ``tests/test_freshness.py::_rewrite_in_place``), and going back through
    ``BaseTransform.execute`` → ``add_annotation`` is the point: the completed
    body and the verifying trace are rebuilt by the production path, not by
    the test.
    """
    _remove(proj, rendered.id)
    fal.next_image_url = url
    fake_assets.serve(url, data)
    skeleton = _skeleton(
        proj, tuple(rendered.provenance.was_derived_from), ann_id=rendered.id
    )
    result = BaseTransform().execute(proj, _image_plan(), (skeleton,), force=True)
    return result.annotations[0]


# --- the money pair ----------------------------------------------------------


def test_a_rerender_that_changes_no_bytes_invalidates_nothing_downstream(
    tmp_path, fal, fake_assets
):
    """New URL, same bytes → the body is identical → downstream stays fresh.

    This is the composition nw#24 was filed for: the render body references
    the artifact by *content* (``artifact_id = sha256(bytes)``, falaw#14), so
    a regeneration that changes nothing changes no digest, and the verifying
    trace lets ``stale_after`` stop there instead of re-billing the subtree.
    """
    proj, rendered, downstream = _rendered_project(tmp_path, fal, fake_assets)

    # The falaw#14 contract, asserted at the nw boundary: the body carries the
    # hash of the bytes — not of the URL, which is about to change.
    assert rendered.body["artifact_id"] == hash_bytes(IMG_A)

    rerendered = _rerender_in_place(
        proj, fal, fake_assets, rendered, url="http://cdn/img-run2.png", data=IMG_A
    )

    # The re-render genuinely ran (two fal calls) and genuinely moved the URL.
    assert len(fal.calls) == 2
    assert rerendered.id == rendered.id
    assert rerendered.body == rendered.body

    # Early cutoff fires where the money is: nothing downstream is stale...
    assert nw.stale_after(proj.root, rerendered.id) == []
    # ...while reachability still sees it, so the two verbs stay distinct.
    assert downstream.id in {a.id for a in nw.descendants_of(proj.root, rerendered.id)}


def test_a_rerender_that_changes_the_bytes_invalidates_downstream(
    tmp_path, fal, fake_assets
):
    """New URL, *new* bytes → the safety half: cutoff must not fire.

    Without this, the fix above would be indistinguishable from `stale_after`
    returning [] unconditionally.
    """
    proj, rendered, downstream = _rendered_project(tmp_path, fal, fake_assets)

    rerendered = _rerender_in_place(
        proj, fal, fake_assets, rendered, url="http://cdn/img-run2.png", data=IMG_B
    )

    assert rerendered.body["artifact_id"] == hash_bytes(IMG_B)
    assert rerendered.body != rendered.body

    stale = nw.stale_after(proj.root, rerendered.id)
    assert downstream.id in {a.id for a in stale}
    verdicts = {v.annotation.id: v for v in nw.stale_verdicts(proj.root, rerendered.id)}
    assert verdicts[downstream.id].reason == REASON_UPSTREAM_CHANGED
    assert verdicts[downstream.id].upstream_id == rerendered.id
