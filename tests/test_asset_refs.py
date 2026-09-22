"""Artifact-tier lineage (nw#55): a declared body schema names its artifacts, and
``derive_provenance`` writes them after the annotation ids."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from lacing import Annotation, MediaRef, Provenance, RationalTime, TimeInterval

from nw.transforms import TransformInputs
from nw.transforms._provenance import derive_provenance
from nw.transforms.asset_refs import (
    AssetRefDeclarationError,
    asset_fields,
    asset_refs_of,
    register_asset_refs,
)

A, B, C = "a" * 64, "b" * 64, "c" * 64
PANEL = "annot://schema/test-panel/v1"
UNDECLARED = "annot://schema/test-undeclared/v1"
register_asset_refs(PANEL, asset_fields("artifact_id", "images[].artifact_id"))
T = SimpleNamespace(name="panel_to_clip.test", impl_version="1")


def _ann(uri, body):
    return Annotation(
        id=uuid4(),
        tier="t",
        reference=MediaRef(asset_id="p", interval=TimeInterval.from_seconds(0, 0)),
        body=body,
        body_schema_uri=uri,
        provenance=Provenance(
            was_generated_by="t",
            was_attributed_to="t",
            generated_at_time=RationalTime.now(),
            activity="derive",
        ),
    )


def test_an_undeclared_input_leaves_provenance_as_it_always_was():
    x = _ann(UNDECLARED, {"artifact_id": A})
    prov = derive_provenance(T, TransformInputs(primary=(x,)))
    assert prov.was_derived_from == [x.id]


def test_declared_inputs_add_their_assets_after_the_annotation_ids():
    p1 = _ann(PANEL, {"artifact_id": A, "images": [{"artifact_id": B}]})
    p2 = _ann(PANEL, {"artifact_id": A, "images": [{"artifact_id": None}]})
    ctx = _ann(PANEL, {"images": [{"artifact_id": C}]})
    prov = derive_provenance(
        T, TransformInputs(primary=(p1, p2), context={"style": (ctx,)})
    )
    assert prov.was_derived_from == [p1.id, p2.id, ctx.id, A, B, C]


def test_explicit_asset_refs_are_merged_and_deduplicated():
    p1 = _ann(PANEL, {"artifact_id": A})
    prov = derive_provenance(T, TransformInputs(primary=(p1,)), asset_refs=[B, A])
    assert prov.was_derived_from == [p1.id, A, B]


def test_a_skeleton_with_no_artifact_yet_names_none():
    assert asset_refs_of(_ann(PANEL, {"artifact_id": None, "images": []})) == ()


@pytest.mark.parametrize("bad", ["sha256:" + A[:57], "https://x/y.png", A.upper(), 42])
def test_a_declared_path_yielding_a_non_asset_id_is_refused_at_plan_time(bad):
    with pytest.raises(AssetRefDeclarationError, match=PANEL):
        derive_provenance(T, TransformInputs(primary=(_ann(PANEL, {"artifact_id": bad}),)))


def test_nw_declares_its_own_render_result_schema():
    from nw.bodies.render_result import RENDER_RESULT_BODY_SCHEMA_URI

    rr = _ann(RENDER_RESULT_BODY_SCHEMA_URI, {"shot_id": "s", "strategy": "x", "artifact_id": A})
    assert asset_refs_of(rr) == (A,)


def test_explicit_asset_refs_are_validated_too():
    p1 = _ann(PANEL, {"artifact_id": A})
    with pytest.raises(TypeError, match="single str"):
        derive_provenance(T, TransformInputs(primary=(p1,)), asset_refs=A)
    with pytest.raises(AssetRefDeclarationError):
        # A UUID string would otherwise become an ANNOTATION parent via lacing's union.
        derive_provenance(T, TransformInputs(primary=(p1,)), asset_refs=[str(uuid4())])
