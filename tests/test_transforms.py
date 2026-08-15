"""Tests for nw.transforms — the Transform abstraction + render-strategy adapter.

The Transform contract is verified against the 5 existing render strategies:
each is wrapped as a ``shot_to_render_result.fal.*`` Transform, and its
``plan`` phase is exercised offline (``upload=False``, no fal contact) — the
same "planning only" approach ``test_workflow.py`` uses for end-to-end checks.
A full ``execute`` round-trip is covered for the one strategy that needs no
network: ``still`` with an anchor produces a zero-call Plan.
"""

from __future__ import annotations

import importlib
import struct
import uuid
from shutil import which

import pytest

from nw import (
    DFLT_IMPL_VERSION,
    BaseTransform,
    Project,
    SectionSpec,
    ShotSpec,
    Transform,
    TransformInputs,
    annotations_at_tier,
    get_transform,
    list_transforms,
    plan_render_shot,
    prepare_shot,
    register_transform,
    stamp_transform_identity,
    transforms,
)
from nw.bodies import RENDER_RESULT_BODY_SCHEMA_URI, SHOT_BODY_SCHEMA_URI
from nw.transforms import TransformResult
from nw.transforms._adapters.render_strategy import RenderStrategyParams
from nw.transforms._provenance import derive_provenance


_STRATEGY_NAMES = ("lipsync", "image_to_video", "text_to_video", "still", "composite_lipsync")
_TRANSFORM_NAMES = tuple(f"shot_to_render_result.fal.{n}" for n in _STRATEGY_NAMES)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Isolate the falaw cache so nothing bleeds across tests."""
    monkeypatch.setenv("FALAW_DATA_DIR", str(tmp_path / "_falaw"))
    monkeypatch.setenv("FALAW_CACHE_DIR", str(tmp_path / "_falaw" / "cache"))


def _minimal_wav_bytes() -> bytes:
    sample_rate, n_frames = 8000, 8000 * 10
    data_size = n_frames
    header = (
        b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate, 1, 8)
        + b"data" + struct.pack("<I", data_size)
    )
    return header + (b"\x80" * data_size)


def _minimal_png_bytes(w: int = 16, h: int = 16) -> bytes:
    """A valid w×h grey PNG, stdlib-only. Even dimensions keep ffmpeg's
    yuv420p happy in the materialize round-trip."""
    import zlib

    def _chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + typ + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit RGB
    raw = b"".join(b"\x00" + b"\x40\x40\x40" * w for _ in range(h))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )


def _seed_project(tmp_path, *, strategy: str, with_environment: bool = True) -> Project:
    """A one-shot project. ``still`` + environment anchor → a zero-call Plan."""
    proj = Project.init(tmp_path / "p")
    song = proj.root / "song" / "song.wav"
    song.write_bytes(_minimal_wav_bytes())
    from nw.schema import SongInfo

    proj.update_spec(
        song=SongInfo(audio_path="song/song.wav", duration_s=10.0, sample_rate=8000, bitrate=64000)
    )
    if with_environment:
        proj.add_environment("bell_tower", description="Gothic bell tower")
        (proj.environment_dir("bell_tower") / "establishing.png").write_bytes(
            _minimal_png_bytes()
        )
    proj.upsert_section(SectionSpec(id="verse", start_s=0.0, end_s=10.0))
    proj.upsert_shot(
        ShotSpec(
            id="s01", start_s=0.0, end_s=8.0, section_id="verse",
            render_strategy=strategy,
            environment="bell_tower" if with_environment else "",
            description="bell tower at moonlight", framing="medium",
        )
    )
    return proj


def _shot_inputs(project: Project) -> TransformInputs:
    shots = annotations_at_tier(project.root, "shot")
    assert len(shots) == 1, f"expected one shot annotation, got {len(shots)}"
    return TransformInputs(primary=(shots[0],))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_render_strategy_transforms_registered():
    names = list_transforms()
    for expected in _TRANSFORM_NAMES:
        assert expected in names


def test_wrapped_transforms_satisfy_protocol_and_declare_kinds():
    for name in _TRANSFORM_NAMES:
        t = get_transform(name)
        assert isinstance(t, Transform)
        assert t.input_kinds == (SHOT_BODY_SCHEMA_URI,)
        assert t.output_kind == RENDER_RESULT_BODY_SCHEMA_URI
        assert t.name == name


def test_render_strategy_transforms_are_one_to_one():
    # a render strategy turns one shot into one clip — not a batch Transform
    for name in _TRANSFORM_NAMES:
        assert get_transform(name).is_batch is False


def test_base_transform_defaults_to_one_to_one():
    assert BaseTransform.is_batch is False


def test_get_transform_unknown_raises():
    with pytest.raises(KeyError, match="No Transform 'nope'"):
        get_transform("nope")


def test_register_transform_direct_form():
    class _Direct(BaseTransform):
        name = "x_to_y.test.direct"
        output_kind = "annot://schema/test-output/v1"

    impl = _Direct()
    try:
        returned = register_transform("x_to_y.test.direct", impl)
        assert returned is impl
        assert get_transform("x_to_y.test.direct") is impl
    finally:
        transforms.pop("x_to_y.test.direct", None)


def test_register_transform_decorator_form():
    @register_transform("x_to_y.test.deco")
    class _Deco(BaseTransform):
        name = "x_to_y.test.deco"
        output_kind = "annot://schema/test-output/v1"

    try:
        # The class is returned unchanged; an *instance* is registered.
        assert _Deco.__name__ == "_Deco"
        assert isinstance(get_transform("x_to_y.test.deco"), _Deco)
    finally:
        transforms.pop("x_to_y.test.deco", None)


# ---------------------------------------------------------------------------
# BaseTransform
# ---------------------------------------------------------------------------


def test_base_transform_plan_is_not_implemented():
    class _T(BaseTransform):
        name = "x_to_y.test.noplan"

    with pytest.raises(NotImplementedError, match="must implement plan"):
        _T().plan(None, TransformInputs(primary=()))


def test_base_transform_complete_annotation_media_sets_artifact_id():
    from lacing import Annotation, Artifact, MediaRef, Provenance, RationalTime, TimeInterval

    iv = TimeInterval(RationalTime(0), RationalTime(24000))
    skel = Annotation(
        id=uuid.uuid4(), tier="panels",
        reference=MediaRef(asset_id="a" * 64, interval=iv),
        body={"panel_id": "p0"}, body_schema_uri="annot://schema/shot/v1",
        provenance=Provenance(
            was_generated_by="transform:t@1", was_attributed_to="agent:test",
            was_derived_from=[], generated_at_time=RationalTime.now(), activity="derive",
        ),
    )
    art = Artifact(
        asset_id="b" * 64, kind="image", bytes_size=10,
        provenance=skel.provenance,
    )
    completed = BaseTransform()._complete_annotation(skel, art)
    assert completed.body["artifact_id"] == "b" * 64
    assert completed.body["panel_id"] == "p0"  # original fields preserved


def _skeleton_and_prov(body: dict):
    """Helper: a skeleton Annotation + its Provenance for _complete_annotation tests."""
    from lacing import Annotation, MediaRef, Provenance, RationalTime, TimeInterval

    iv = TimeInterval(RationalTime(0), RationalTime(24000))
    prov = Provenance(
        was_generated_by="transform:t@1", was_attributed_to="agent:test",
        was_derived_from=[], generated_at_time=RationalTime.now(), activity="derive",
    )
    skel = Annotation(
        id=uuid.uuid4(), tier="t", reference=MediaRef(asset_id="a" * 64, interval=iv),
        body=body, body_schema_uri="annot://schema/shot/v1", provenance=prov,
    )
    return skel, prov


def test_base_transform_complete_annotation_json_merges_materialized_payload(tmp_path):
    """json artifacts: read the materialized file, shallow-merge into the body."""
    from lacing import Artifact

    skel, prov = _skeleton_and_prov({"caption": "<placeholder>", "framing": "medium"})
    json_file = tmp_path / "llm-out.json"
    json_file.write_text('{"caption": "a bell tower at dusk", "camera": "slow push-in"}')
    art = Artifact(
        asset_id="b" * 64, kind="json", path=json_file, bytes_size=json_file.stat().st_size,
        provenance=prov,
    )
    completed = BaseTransform()._complete_annotation(skel, art)
    assert completed.body["caption"] == "a bell tower at dusk"  # LLM value overwrote placeholder
    assert completed.body["camera"] == "slow push-in"  # new key added
    assert completed.body["framing"] == "medium"  # untouched skeleton field preserved


def test_base_transform_complete_annotation_json_without_path_raises(tmp_path):
    from lacing import Artifact

    skel, prov = _skeleton_and_prov({})
    art = Artifact(asset_id="b" * 64, kind="json", bytes_size=2, provenance=prov)
    with pytest.raises(ValueError, match="no `path`"):
        BaseTransform()._complete_annotation(skel, art)


def test_base_transform_complete_annotation_text_still_requires_override(tmp_path):
    from lacing import Artifact

    skel, prov = _skeleton_and_prov({})
    txt = tmp_path / "out.txt"
    txt.write_text("a bare string")
    art = Artifact(asset_id="b" * 64, kind="text", path=txt, bytes_size=13, provenance=prov)
    with pytest.raises(NotImplementedError, match="override _complete_annotation"):
        BaseTransform()._complete_annotation(skel, art)


# ---------------------------------------------------------------------------
# derive_provenance
# ---------------------------------------------------------------------------


def test_derive_provenance_unions_input_ids_and_stamps_transform():
    from lacing import Annotation, MediaRef, Provenance, RationalTime, TimeInterval

    iv = TimeInterval(RationalTime(0), RationalTime(24000))

    def _ann() -> Annotation:
        return Annotation(
            id=uuid.uuid4(), tier="t", reference=MediaRef(asset_id="a" * 64, interval=iv),
            body={}, body_schema_uri="annot://schema/shot/v1",
            provenance=Provenance(
                was_generated_by="x", was_attributed_to="y", was_derived_from=[],
                generated_at_time=RationalTime.now(), activity="create",
            ),
        )

    primary, ctx_a, ctx_b = _ann(), _ann(), _ann()
    inputs = TransformInputs(primary=(primary,), context={"character-ref": (ctx_a, ctx_b)})
    class _BeatToPanel(BaseTransform):
        name = "beat_to_panel.llm.default"
        output_kind = "annot://schema/panel/v1"
        impl_version = "3"

    prov = derive_provenance(_BeatToPanel(), inputs)

    assert prov.was_generated_by == "transform:beat_to_panel.llm.default@3"
    assert prov.was_attributed_to == "agent:beat_to_panel.llm.default"
    assert set(prov.was_derived_from) == {primary.id, ctx_a.id, ctx_b.id}
    assert prov.activity == "derive"


def test_derive_provenance_respects_explicit_attribution():
    inputs = TransformInputs(primary=())
    class _T(BaseTransform):
        name = "t"
        output_kind = "annot://schema/panel/v1"

    prov = derive_provenance(_T(), inputs, attributed_to="agent:claude-sonnet-4-6@abc")
    assert prov.was_attributed_to == "agent:claude-sonnet-4-6@abc"
    assert prov.was_derived_from == []


# ---------------------------------------------------------------------------
# Render-strategy adapter — plan phase (offline, no fal)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", ["still", "text_to_video", "image_to_video"])
def test_adapter_plan_matches_strategy_and_builds_render_result_skeleton(tmp_path, strategy):
    proj = _seed_project(tmp_path, strategy=strategy, with_environment=True)
    transform = get_transform(f"shot_to_render_result.fal.{strategy}")
    inputs = _shot_inputs(proj)

    plan, skeleton = transform.plan(
        proj, inputs, params=RenderStrategyParams(upload=False)
    )

    # The Plan is exactly what the legacy workflow would produce for this shot.
    prep = prepare_shot(proj, "s01", upload=False)
    expected_plan = plan_render_shot(prep)
    assert [c.tool for c in plan.calls] == [c.tool for c in expected_plan.calls]

    # Exactly one skeleton render-result annotation, derived from the shot.
    assert len(skeleton) == 1
    skel = skeleton[0]
    assert skel.body_schema_uri == RENDER_RESULT_BODY_SCHEMA_URI
    assert skel.tier == "render-result"
    assert skel.body["shot_id"] == "s01"
    assert skel.body["strategy"] == strategy
    assert skel.body["artifact_id"] is None  # filled by execute
    # @1: the version now comes off the Transform (impl_version), not a
    # per-callsite label (nw#27).
    assert skel.provenance.was_generated_by == (
        f"transform:shot_to_render_result.fal.{strategy}@1"
    )
    assert inputs.primary[0].id in skel.provenance.was_derived_from


def test_adapter_still_with_anchor_plans_zero_calls(tmp_path):
    """`still` + an environment anchor needs no fal call at all."""
    proj = _seed_project(tmp_path, strategy="still", with_environment=True)
    transform = get_transform("shot_to_render_result.fal.still")
    plan, skeleton = transform.plan(
        proj, _shot_inputs(proj), params=RenderStrategyParams(upload=False)
    )
    assert len(plan.calls) == 0
    assert plan.total_cost_usd == 0.0
    assert skeleton[0].body["total_estimated_cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# Render-strategy adapter — full plan→execute round-trip, network-free
# ---------------------------------------------------------------------------


@pytest.mark.skipif(which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_adapter_still_full_round_trip_offline(tmp_path):
    """`still` + anchor: zero fal calls, so plan→execute runs entirely locally."""
    proj = _seed_project(tmp_path, strategy="still", with_environment=True)
    transform = get_transform("shot_to_render_result.fal.still")
    inputs = _shot_inputs(proj)

    plan, skeleton = transform.plan(proj, inputs, params=RenderStrategyParams(upload=False))
    assert len(plan.calls) == 0  # precondition: no network

    result = transform.execute(proj, plan, skeleton)
    assert isinstance(result, TransformResult)
    assert result.cost_usd_actual == 0.0
    assert len(result.annotations) == 1

    completed = result.annotations[0]
    assert completed.body["output_path"].endswith("output.mp4")
    assert (proj.root / completed.body["output_path"]).exists()

    # The completed annotation was written to the project graph under its tier.
    persisted = annotations_at_tier(proj.root, "render-result")
    assert len(persisted) == 1
    assert persisted[0].body["shot_id"] == "s01"
    # Provenance edge shot → render-result is intact for freshness traversal.
    assert inputs.primary[0].id in persisted[0].provenance.was_derived_from


# ---------------------------------------------------------------------------
# BaseTransform.execute length invariant (nw#25 step 1)
# ---------------------------------------------------------------------------


def _skel(n: int):
    """n minimal skeleton annotations."""
    from lacing import Annotation, MediaRef, Provenance, RationalTime, TimeInterval

    iv = TimeInterval(RationalTime(0), RationalTime(24000))
    prov = Provenance(
        was_generated_by="transform:t@1",
        was_attributed_to="agent:test",
        was_derived_from=[],
        generated_at_time=RationalTime.now(),
        activity="derive",
    )
    return tuple(
        Annotation(
            id=uuid.uuid4(),
            tier="t",
            reference=MediaRef(asset_id="a" * 64, interval=iv),
            body={"i": i},
            body_schema_uri="annot://schema/shot/v1",
            provenance=prov,
        )
        for i in range(n)
    )


def _plan(n: int):
    from falaw import CallPlan, Plan

    return Plan(
        calls=tuple(
            CallPlan(
                tool="generate_image",
                application="test/app",
                arguments={"i": i},
                output_kind="image",
            )
            for i in range(n)
        )
    )


def _report(calls, artifacts, *, failures=()):
    """A real ``falaw.ExecutionReport`` over ``calls``.

    Built rather than faked: the whole point of nw#25 is that ``outcomes`` is
    full-length in plan order **by construction**, so a stub that returns a
    convenient shape would test nw against a contract falaw does not have.
    ``failures`` maps index -> (status, reason).
    """
    from falaw.outcomes import CallOutcome, ExecutionReport

    failures = dict(failures)
    outcomes = []
    produced = list(artifacts)
    for i, call in enumerate(calls):
        if i in failures:
            status, reason = failures[i]
            outcomes.append(
                CallOutcome(
                    index=i,
                    call=call,
                    status=status,
                    error=RuntimeError(reason) if status == "failed" else None,
                    reason=reason,
                )
            )
        else:
            outcomes.append(
                CallOutcome(
                    index=i,
                    call=call,
                    status="succeeded",
                    artifact=produced.pop(0),
                )
            )
    return ExecutionReport(outcomes=tuple(outcomes))


def test_execute_refuses_a_skeleton_plan_length_mismatch():
    """The zip would drop the surplus skeleton silently; refuse instead.

    Raised BEFORE execute_plan, so the mismatch costs nothing: a Transform
    whose plan and skeleton disagree must not bill first and lose the result
    afterwards.
    """
    with pytest.raises(ValueError, match="2 calls but skeleton has 3"):
        BaseTransform().execute(None, _plan(2), _skel(3))

    with pytest.raises(ValueError, match="3 calls but skeleton has 2"):
        BaseTransform().execute(None, _plan(3), _skel(2))


def test_execute_length_check_precedes_any_spending(monkeypatch):
    """Nothing is executed when the invariant fails."""
    # `nw.transforms` the ATTRIBUTE is the Registry, not the module —
    # import_module is the only way to reach the module object here.
    _t = importlib.import_module("nw.transforms")

    called = []
    monkeypatch.setattr(
        _t, "execute_plan_isolated", lambda *a, **k: called.append(1) or _report([], [])
    )
    with pytest.raises(ValueError):
        BaseTransform().execute(None, _plan(1), _skel(2))
    assert called == []


def test_execute_accepts_a_matching_skeleton_and_plan(monkeypatch):
    """The guard does not fire on the normal 1:1 case."""
    # `nw.transforms` the ATTRIBUTE is the Registry, not the module —
    # import_module is the only way to reach the module object here.
    _t = importlib.import_module("nw.transforms")
    from lacing import Artifact

    skeleton = _skel(2)
    artifacts = [
        Artifact(
            asset_id=chr(ord("b") + i) * 64,
            kind="image",
            bytes_size=1,
            provenance=skeleton[i].provenance,
        )
        for i in range(2)
    ]
    plan = _plan(2)
    monkeypatch.setattr(
        _t, "execute_plan_isolated", lambda *a, **k: _report(plan.calls, artifacts)
    )

    class _Graph:
        def __init__(self):
            self.written = []

        def add_annotation(self, ann):
            self.written.append(ann)

    class _Project:
        graph = _Graph()

    project = _Project()
    result = BaseTransform().execute(project, _plan(2), skeleton)
    assert len(result.annotations) == 2
    assert len(project.graph.written) == 2


# ---------------------------------------------------------------------------
# The hardened contract (nw#27)
# ---------------------------------------------------------------------------


def test_registering_without_an_output_kind_is_refused_loudly():
    """An agent's unit of work must have a declared output type — otherwise
    'the job runs successfully but produces nothing retrievable' is
    invisible to every layer that reports success."""

    class _Kindless(BaseTransform):
        name = "x_to_y.test.kindless"

    with pytest.raises(ValueError, match="_Kindless.*output_kind"):
        register_transform("x_to_y.test.kindless", _Kindless())
    assert "x_to_y.test.kindless" not in transforms

    with pytest.raises(ValueError, match="output_kind"):

        @register_transform("x_to_y.test.kindless.deco")
        class _KindlessDeco(BaseTransform):
            name = "x_to_y.test.kindless.deco"

    assert "x_to_y.test.kindless.deco" not in transforms


def test_the_protocol_carries_impl_version_and_params_model():
    """Anything reading a Transform through the Protocol can rely on both
    (the capability catalogue, MCP builders, the CLI)."""
    t = BaseTransform()
    assert isinstance(t, Transform)
    assert t.impl_version == DFLT_IMPL_VERSION == "1"
    assert t.params_model is type(None)


def test_default_impl_version_stamps_nothing():
    """Omit-if-default: every falaw cache key and cassette ever issued
    stays byte-identical until the first real version bump."""
    from falaw import CallPlan, Plan, plan_hash

    plan = Plan(
        calls=(
            CallPlan(
                tool="t", application="m/a", arguments={"p": 1}, output_kind="image"
            ),
        )
    )

    stamped = stamp_transform_identity(plan, BaseTransform())

    assert stamped is plan  # not even a copy: nothing to stamp
    assert plan_hash(stamped) == plan_hash(plan)


def test_a_bumped_impl_version_changes_the_cache_identity_not_the_name():
    from falaw import CallPlan, Plan, plan_hash

    class _V2(BaseTransform):
        name = "x_to_y.test.v2"
        output_kind = "annot://schema/test-output/v1"
        impl_version = "2"

    plan = Plan(
        calls=(
            CallPlan(
                tool="t", application="m/a", arguments={"p": 1}, output_kind="image"
            ),
        )
    )

    stamped = stamp_transform_identity(plan, _V2())

    assert stamped.calls[0].key_extra == {"transform_impl": "2"}
    assert plan_hash(stamped) != plan_hash(plan)
    assert _V2.name == "x_to_y.test.v2"  # the registry key does not change
    # Idempotent: stamping twice writes the same value.
    twice = stamp_transform_identity(stamped, _V2())
    assert plan_hash(twice) == plan_hash(stamped)


def test_derive_provenance_reads_the_version_off_the_transform():
    from nw.transforms._provenance import derive_provenance

    class _V3(BaseTransform):
        name = "x_to_y.test.v3"
        output_kind = "annot://schema/test-output/v1"
        impl_version = "3"

    prov = derive_provenance(_V3(), TransformInputs(primary=()))

    assert prov.was_generated_by == "transform:x_to_y.test.v3@3"
    assert prov.was_attributed_to == "agent:x_to_y.test.v3"
