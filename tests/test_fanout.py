"""Tests for nw#26 — the WorkItem fan-out primitive.

What these pin, each one a rule the issue states as load-bearing:

* ``mapping_key`` must be deterministic AND semantic — bare integers and
  UUIDs (every spelling ``uuid.UUID`` accepts) are refused at validation;
* instance ids are a pure function of ``(transform_name, mapping_key)`` —
  stable across runs, distinct across transforms, and **stable under
  insertion** (the property ordinals lack);
* ``generate_when`` defaults to ``"dynamic"`` (fail expensive-looking),
  is validated at registration, and reaches the capability catalog;
* the fan-out plan refuses duplicate keys and carries falaw#18's honest
  cost arithmetic (known sum + unknown count, never a coerced $0.00);
* execute isolates per unit — one raising unit never discards the others'
  results — and ``"halt"`` marks unattempted units ``blocked``, not absent;
* a pre-nw#25 ``execute`` override (no ``on_failure`` keyword) still runs;
* the run record is JSON-serializable and work items never touch the graph
  document.
"""

from __future__ import annotations

import json
import uuid

import pytest

from nw.transforms import (
    BaseTransform,
    DFLT_GENERATE_WHEN,
    FanOutPlan,
    TransformInputs,
    TransformResult,
    WorkItem,
    fan_out_execute,
    fan_out_plan,
    transform_catalog,
    work_item_instance_id,
)
from nw.transforms.fanout import _check_mapping_key


# ---------------------------------------------------------------------------
# WorkItem validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        " padded ",
        "12",
        "0",
        "-3",
        "+7",
        "550e8400-e29b-41d4-a716-446655440000",
        "550e8400e29b41d4a716446655440000",  # bare-hex UUID spelling
        "urn:uuid:550e8400-e29b-41d4-a716-446655440000",
        "{550e8400-e29b-41d4-a716-446655440000}",  # braced spelling
        "a\x00b",
    ],
)
def test_mapping_key_rejects_nonsemantic_ids(bad):
    with pytest.raises(ValueError):
        WorkItem(mapping_key=bad)


@pytest.mark.parametrize(
    "good",
    ["scene_12/shot_04", "intro", "beat-7b", "12b", "v2", "act1/scene2/shot3"],
)
def test_mapping_key_accepts_semantic_keys(good):
    assert WorkItem(mapping_key=good).mapping_key == good


def test_parent_key_gets_the_same_validation():
    with pytest.raises(ValueError):
        WorkItem(mapping_key="scene_1/shot_1", parent_key="42")
    ok = WorkItem(mapping_key="scene_1/shot_1", parent_key="scene_1")
    assert ok.parent_key == "scene_1"


def test_work_item_is_frozen_and_json_round_trips():
    from lacing import RationalTime, TimeInterval

    wi = WorkItem(
        mapping_key="s/1",
        attributes={"seed": 7},
        scope_interval=TimeInterval(RationalTime(0), RationalTime(24000)),
    )
    with pytest.raises(Exception):
        wi.mapping_key = "other"  # type: ignore[misc]
    dumped = json.dumps(wi.model_dump(mode="json"))  # must not raise
    back = WorkItem.model_validate(json.loads(dumped))
    assert back == wi


def test_work_item_has_no_transform_free_instance_id():
    wi = WorkItem(mapping_key="s/1")
    with pytest.raises(AttributeError, match="pure function"):
        wi.instance_id


# ---------------------------------------------------------------------------
# Instance identity
# ---------------------------------------------------------------------------


def test_instance_id_is_pure_and_discriminates_both_inputs():
    a = work_item_instance_id("panel_to_image.fal", "scene_1/panel_2")
    assert a == work_item_instance_id("panel_to_image.fal", "scene_1/panel_2")
    assert a != work_item_instance_id("panel_to_voiceover", "scene_1/panel_2")
    assert a != work_item_instance_id("panel_to_image.fal", "scene_1/panel_3")
    assert isinstance(a, uuid.UUID) and a.version == 5


def test_instance_id_is_stable_under_insertion():
    """The anti-ordinal property: inserting an item changes no other id."""
    keys = ["scene_1/shot_1", "scene_1/shot_2", "scene_2/shot_1"]
    before = {k: work_item_instance_id("t", k) for k in keys}
    keys.insert(1, "scene_1/shot_1b")  # the inserted scene
    after = {k: work_item_instance_id("t", k) for k in keys}
    for k, v in before.items():
        assert after[k] == v


def test_instance_id_separator_prevents_concatenation_collisions():
    """('ab', 'c') and ('a', 'bc') must not share an id."""
    assert work_item_instance_id("ab", "c") != work_item_instance_id("a", "bc")


def test_instance_id_refuses_an_unnamed_transform():
    with pytest.raises(ValueError, match="non-empty"):
        work_item_instance_id("", "scene_1/shot_1")


def test_check_mapping_key_returns_the_value():
    assert _check_mapping_key("scene_1") == "scene_1"


# ---------------------------------------------------------------------------
# generate_when — declaration, registration, catalog
# ---------------------------------------------------------------------------


def test_generate_when_defaults_to_dynamic():
    assert DFLT_GENERATE_WHEN == "dynamic"
    assert BaseTransform().generate_when == "dynamic"


def test_registration_refuses_an_unrecognised_generate_when():
    from nw.transforms import register_transform

    class Bad(BaseTransform):
        name = "test.bad_generate_when"
        output_kind = "annot://schema/test/v1"
        generate_when = "sometimes"  # type: ignore[assignment]

    with pytest.raises(ValueError, match="generate_when"):
        register_transform("test.bad_generate_when", Bad())


def test_catalog_carries_generate_when(monkeypatch):
    import importlib

    # `nw.transforms` the ATTRIBUTE is the Registry, not the module.
    t = importlib.import_module("nw.transforms")

    class Static(BaseTransform):
        name = "test.static"
        output_kind = "annot://schema/test/v1"
        generate_when = "static"

    from xdol import Registry

    reg = Registry(name="test", on_conflict="error")
    reg.register("test.static", Static())
    reg.register("test.dynamic_by_default", type("D", (BaseTransform,), {
        "name": "test.dynamic_by_default",
        "output_kind": "annot://schema/test/v1",
    })())
    monkeypatch.setattr(t, "transforms", reg)
    entries = {e["name"]: e for e in transform_catalog()}
    assert entries["test.static"]["generate_when"] == "static"
    assert entries["test.dynamic_by_default"]["generate_when"] == "dynamic"


# ---------------------------------------------------------------------------
# Fan-out plan/execute fixtures — a stub Transform over a stub project
# ---------------------------------------------------------------------------

BODY_URI = "annot://schema/test-panel/v1"


def _annotation(key: str):
    from lacing import Annotation, MediaRef, Provenance, RationalTime, TimeInterval

    return Annotation(
        id=uuid.uuid4(),
        tier="panel",
        reference=MediaRef(
            asset_id="a" * 64,
            interval=TimeInterval(RationalTime(0), RationalTime(24000)),
        ),
        body={"key": key, "artifact_id": None},
        body_schema_uri=BODY_URI,
        provenance=Provenance(
            was_generated_by="transform:test@1",
            was_attributed_to="agent:test",
            was_derived_from=[],
            generated_at_time=RationalTime.now(),
            activity="derive",
        ),
    )


class _Graph:
    def __init__(self):
        self.written = []

    def add_annotation(self, ann):
        self.written.append(ann)


class _Project:
    def __init__(self):
        self.graph = _Graph()


class _StubTransform(BaseTransform):
    """A Transform whose execute is scripted per mapping_key.

    ``fail_keys`` raise; ``partial_keys`` return an incomplete result.
    Everything is recorded so tests can assert what ran, with what.
    """

    name = "test.fanout_stub"
    output_kind = BODY_URI
    generate_when = "static"

    def __init__(self, *, fail_keys=(), partial_keys=(), unknown_cost_keys=()):
        self.fail_keys = set(fail_keys)
        self.partial_keys = set(partial_keys)
        self.unknown_cost_keys = set(unknown_cost_keys)
        self.executed = []
        self.execute_kwargs = []

    def plan(self, project, inputs, *, params=None):
        from falaw import CallPlan, Plan

        key = inputs.primary[0].body["key"]
        cost = None if key in self.unknown_cost_keys else 1.0
        plan = Plan(
            calls=(
                CallPlan(
                    tool="generate_image",
                    application="test/app",
                    arguments={"key": key},
                    output_kind="image",
                    estimated_cost_usd=cost,
                ),
            )
        )
        return plan, (inputs.primary[0],)

    def execute(self, project, plan, skeleton, *, use_cache=True, force=False,
                on_failure="halt"):
        key = plan.calls[0].arguments["key"]
        self.executed.append(key)
        self.execute_kwargs.append(
            {"use_cache": use_cache, "force": force, "on_failure": on_failure}
        )
        if key in self.fail_keys:
            raise RuntimeError(f"unit {key} exploded")
        ann = skeleton[0].model_copy(
            update={"body": {**skeleton[0].body, "artifact_id": "b" * 64}}
        )
        project.graph.add_annotation(ann)
        if key in self.partial_keys:
            from nw.transforms import FailedOutput

            return TransformResult(
                annotations=(ann,),
                cost_usd_actual=1.0,
                failed=(
                    FailedOutput(skeleton=skeleton[0], status="failed",
                                 reason="one output dropped"),
                ),
            )
        return TransformResult(annotations=(ann,), cost_usd_actual=1.0)


def _items(*keys):
    return tuple(WorkItem(mapping_key=k) for k in keys)


def _inputs_for_factory():
    def inputs_for(item: WorkItem) -> TransformInputs:
        return TransformInputs(primary=(_annotation(item.mapping_key),))

    return inputs_for


# ---------------------------------------------------------------------------
# fan_out_plan
# ---------------------------------------------------------------------------


def test_fan_out_plan_builds_one_unit_per_item_in_order():
    t = _StubTransform()
    items = _items("s1/p1", "s1/p2", "s2/p1")
    fo = fan_out_plan(t, _Project(), items, inputs_for=_inputs_for_factory())
    assert [u.item.mapping_key for u in fo.units] == ["s1/p1", "s1/p2", "s2/p1"]
    assert fo.transform_name == "test.fanout_stub"
    for u in fo.units:
        assert u.instance_id == work_item_instance_id(t.name, u.item.mapping_key)
        assert len(u.plan.calls) == 1 and len(u.skeleton) == 1


def test_fan_out_plan_refuses_duplicate_mapping_keys():
    with pytest.raises(ValueError, match="duplicate mapping_key"):
        fan_out_plan(
            _StubTransform(),
            _Project(),
            _items("s1/p1", "s1/p1"),
            inputs_for=_inputs_for_factory(),
        )


def test_fan_out_plan_refuses_a_nameless_transform():
    t = _StubTransform()
    t.name = ""
    with pytest.raises(ValueError, match="no `name`"):
        fan_out_plan(t, _Project(), _items("s1/p1"), inputs_for=_inputs_for_factory())


def test_fan_out_plan_cost_arithmetic_is_falaw18_honest():
    """Known sum + unknown count — an unpriced call is never a free call."""
    t = _StubTransform(unknown_cost_keys={"s1/p2"})
    fo = fan_out_plan(
        t, _Project(), _items("s1/p1", "s1/p2", "s2/p1"),
        inputs_for=_inputs_for_factory(),
    )
    assert fo.known_cost_usd == 2.0
    assert fo.has_unknown_costs is True
    assert fo.unknown_call_count == 1


def test_fan_out_plan_stamps_impl_version_at_plan_time():
    t = _StubTransform()
    t.impl_version = "2"
    fo = fan_out_plan(t, _Project(), _items("s1/p1"), inputs_for=_inputs_for_factory())
    call = fo.units[0].plan.calls[0]
    assert call.key_extra.get("transform_impl") == "2"


def test_fan_out_plan_makes_no_billable_calls():
    """Pure data: nothing executed, nothing written to the graph."""
    t = _StubTransform()
    project = _Project()
    fan_out_plan(t, project, _items("s1/p1", "s1/p2"), inputs_for=_inputs_for_factory())
    assert t.executed == []
    assert project.graph.written == []


# ---------------------------------------------------------------------------
# fan_out_execute
# ---------------------------------------------------------------------------


def test_execute_all_units_succeed():
    t = _StubTransform()
    project = _Project()
    fo = fan_out_plan(t, project, _items("a/1", "a/2"), inputs_for=_inputs_for_factory())
    result = fan_out_execute(t, project, fo)
    assert result.is_complete
    assert [r.status for r in result.items] == ["succeeded", "succeeded"]
    assert len(result.items) == len(fo.units)
    assert result.cost_usd_actual == 2.0
    assert len(project.graph.written) == 2


def test_execute_isolates_a_raising_unit():
    """One exploding unit: siblings still run, still write, still report."""
    t = _StubTransform(fail_keys={"a/2"})
    project = _Project()
    fo = fan_out_plan(
        t, project, _items("a/1", "a/2", "a/3"), inputs_for=_inputs_for_factory()
    )
    result = fan_out_execute(t, project, fo)
    statuses = [r.status for r in result.items]
    assert statuses == ["succeeded", "failed", "succeeded"]
    assert t.executed == ["a/1", "a/2", "a/3"], "isolation keeps going"
    assert len(project.graph.written) == 2, "paid siblings reach the graph"
    failed = result.items[1]
    assert isinstance(failed.error, RuntimeError)
    assert "exploded" in failed.reason
    assert not result.is_complete


def test_execute_halt_blocks_units_after_the_first_failure():
    t = _StubTransform(fail_keys={"a/2"})
    project = _Project()
    fo = fan_out_plan(
        t, project, _items("a/1", "a/2", "a/3", "a/4"),
        inputs_for=_inputs_for_factory(),
    )
    result = fan_out_execute(t, project, fo, on_failure="halt")
    assert [r.status for r in result.items] == [
        "succeeded", "failed", "blocked", "blocked",
    ]
    assert t.executed == ["a/1", "a/2"], "halt stops submitting"
    assert "a/2" in result.items[2].reason, "blocked names its cause"
    assert result.items[2].result is None


def test_execute_reports_a_partial_unit_without_halting():
    t = _StubTransform(partial_keys={"a/1"})
    project = _Project()
    fo = fan_out_plan(
        t, project, _items("a/1", "a/2"), inputs_for=_inputs_for_factory()
    )
    result = fan_out_execute(t, project, fo, on_failure="halt")
    assert [r.status for r in result.items] == ["partial", "succeeded"]
    assert "failed" in result.items[0].reason


def test_execute_passes_policy_and_cache_flags_through():
    t = _StubTransform()
    project = _Project()
    fo = fan_out_plan(t, project, _items("a/1"), inputs_for=_inputs_for_factory())
    fan_out_execute(t, project, fo, use_cache=False, force=True, on_failure="isolate")
    assert t.execute_kwargs == [
        {"use_cache": False, "force": True, "on_failure": "isolate"}
    ]


def test_execute_refuses_an_unknown_policy():
    t = _StubTransform()
    fo = fan_out_plan(t, _Project(), _items("a/1"), inputs_for=_inputs_for_factory())
    with pytest.raises(ValueError, match="on_failure"):
        fan_out_execute(t, _Project(), fo, on_failure="explode")  # type: ignore[arg-type]


def test_execute_tolerates_a_pre_nw25_override_without_on_failure():
    """The federation has ~18 execute overrides that predate the keyword."""

    class Legacy(_StubTransform):
        def execute(self, project, plan, skeleton, *, use_cache=True, force=False):
            return super().execute(
                project, plan, skeleton, use_cache=use_cache, force=force
            )

    t = Legacy()
    project = _Project()
    fo = fan_out_plan(t, project, _items("a/1", "a/2"), inputs_for=_inputs_for_factory())
    result = fan_out_execute(t, project, fo, on_failure="isolate")
    assert result.is_complete, "no TypeError — the keyword is withheld"
    # ... and a raising legacy unit is still isolated at the fan-out level:
    t2 = Legacy(fail_keys={"a/1"})
    project2 = _Project()
    fo2 = fan_out_plan(
        t2, project2, _items("a/1", "a/2"), inputs_for=_inputs_for_factory()
    )
    r2 = fan_out_execute(t2, project2, fo2, on_failure="isolate")
    assert [r.status for r in r2.items] == ["failed", "succeeded"]


# ---------------------------------------------------------------------------
# The run record
# ---------------------------------------------------------------------------


def test_run_record_is_json_serializable_and_carries_identity():
    t = _StubTransform(fail_keys={"a/2"}, unknown_cost_keys={"a/3"})
    project = _Project()
    fo = fan_out_plan(
        t, project, _items("a/1", "a/2", "a/3"), inputs_for=_inputs_for_factory()
    )
    record = fan_out_execute(t, project, fo).to_record()
    dumped = json.dumps(record)  # must not raise
    back = json.loads(dumped)
    assert back["transform_name"] == "test.fanout_stub"
    assert back["complete"] is False
    rows = {row["item"]["mapping_key"]: row for row in back["items"]}
    assert rows["a/1"]["status"] == "succeeded"
    assert rows["a/1"]["annotation_ids"], "successes reference their annotations"
    assert rows["a/2"]["status"] == "failed"
    assert rows["a/2"]["annotation_ids"] == []
    assert rows["a/1"]["instance_id"] == str(
        work_item_instance_id(t.name, "a/1")
    ), "the record carries the pure-function identity"


def test_work_items_never_reach_the_graph_document():
    """The record is the only place instances are materialised."""
    t = _StubTransform()
    project = _Project()
    fo = fan_out_plan(t, project, _items("a/1"), inputs_for=_inputs_for_factory())
    fan_out_execute(t, project, fo)
    (written,) = project.graph.written
    body_text = json.dumps(written.body)
    assert "mapping_key" not in body_text
    assert str(work_item_instance_id(t.name, "a/1")) not in body_text
