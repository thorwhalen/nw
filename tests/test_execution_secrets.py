"""A secret handed to ``execute`` lives in memory and nowhere else.

The per-caller credential seam (:mod:`nw.secrets`, braidio#58): ``execute(...,
secrets=)`` is passed accepts-it-or-not by :func:`nw.fan_out_execute` and by
:func:`nw.jobs.enqueue`'s dispatch, and must never be persisted — not in the
graph document, the plan, the cache key, the run record, the job index or a
log line. These tests run a sentinel secret through every one of those
surfaces and grep for it, which is the only kind of proof a "never" deserves.
"""

from __future__ import annotations

import copy
import importlib
import json
import logging
import pickle
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from falaw import Plan, current_fal_key, plan_to_dict
from lacing import Annotation

import nw
import nw.jobs as jobs
from nw import (
    BaseTransform,
    Secrets,
    TransformInputs,
    TransformResult,
    WorkItem,
    fan_out_execute,
    fan_out_plan,
)
from nw.bodies import SectionBodyV1
from nw.secrets import FAL_SECRET, as_secrets, using_secrets
from nw.transforms import cache_key, stamp_transform_identity
from nw.transforms._provenance import derive_provenance

SENTINEL = "sk-SENTINEL-4d1f9c7e-must-never-persist"
SECTION_URI = "annot://schema/section/v1"
RENDER_URI = "annot://schema/render-result/v1"


# ---------------------------------------------------------------------------
# fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_runtimes():
    jobs._reset_runtimes()
    yield
    jobs._reset_runtimes()


@pytest.fixture
def project(tmp_path):
    return nw.Project.init(tmp_path / "proj", title="Secrets Test")


def _sections(project, *ids):
    """Seed authored sections and return their Annotations, in order."""
    from lacing import TimeInterval

    from nw.graph import annotations_at_tier

    for i, section_id in enumerate(ids):
        project.graph.upsert_section(
            SectionBodyV1(section_id=section_id, label=section_id),
            interval=TimeInterval.from_seconds(i, i + 1),
        )
    by_id = {
        a.body["section_id"]: a for a in annotations_at_tier(project.root, "section")
    }
    return [by_id[s] for s in ids]


class _SpendsASecret(BaseTransform):
    """A local-render Transform (zero-call plan) whose execute reads a secret —
    the shape of braidio's ``narration_render.tts``. Records what it saw."""

    name = "test.spends_a_secret"
    input_kinds = (SECTION_URI,)
    output_kind = RENDER_URI
    generate_when = "static"

    def __init__(self):
        self.seen: list = []

    def plan(self, project, inputs, *, params=None):
        parent = inputs.primary[0]
        skeleton = Annotation(
            id=uuid4(),
            tier="render-result",
            reference=parent.reference,
            body={
                "shot_id": parent.body["section_id"],
                "url": None,
                "cache_key": cache_key(self, "render", parent.body["section_id"]),
            },
            body_schema_uri=RENDER_URI,
            provenance=derive_provenance(self, inputs, attributed_to="agent:test"),
        )
        return Plan(calls=()), (skeleton,)

    def execute(
        self,
        project,
        plan,
        skeleton,
        *,
        use_cache=True,
        force=False,
        on_failure="halt",
        unit_instance_id=None,
        secrets=None,
    ):
        self.seen.append(secrets.get("elevenlabs") if secrets else None)
        # The kind of line a render logs. `%r` of a Secrets is redacted.
        logging.getLogger("nw.test").debug(
            "rendering %s with secrets=%r (%s)", skeleton[0].id, secrets, secrets
        )
        completed = skeleton[0].model_copy(
            update={"body": {**skeleton[0].body, "url": "file:///rendered.mp3"}}
        )
        project.graph.add_annotation(completed, instance_id=unit_instance_id)
        return TransformResult(annotations=(completed,))


class _Legacy(_SpendsASecret):
    """An execute that predates the seam: no ``secrets`` keyword."""

    name = "test.legacy"

    def execute(self, project, plan, skeleton, *, use_cache=True, force=False):
        self.seen.append("<not offered>")
        completed = skeleton[0].model_copy(
            update={"body": {**skeleton[0].body, "url": "file:///rendered.mp3"}}
        )
        project.graph.add_annotation(completed)
        return TransformResult(annotations=(completed,))


def _fan_out(transform, project, sections):
    by_key = {s.body["section_id"]: s for s in sections}
    items = tuple(WorkItem(mapping_key=k) for k in by_key)
    return fan_out_plan(
        transform,
        project,
        items,
        inputs_for=lambda item: TransformInputs(primary=(by_key[item.mapping_key],)),
    )


def _grep_tree(root) -> list:
    """Every file under ``root`` whose bytes contain the sentinel."""
    needle = SENTINEL.encode()
    return [
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and needle in p.read_bytes()
    ]


def _wait_terminal(project, job_id, *, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = jobs.get_job(project, job_id)
        if job is not None and job.status in jobs.TERMINAL_STATUSES:
            return job
        time.sleep(0.01)
    raise AssertionError(
        f"job {job_id} did not finish: {jobs.get_job(project, job_id)}"
    )


# ---------------------------------------------------------------------------
# The type: redacting, unpicklable, unserializable, honest about absence
# ---------------------------------------------------------------------------


def test_secrets_redacts_repr_and_str_and_drops_absent_values():
    s = Secrets({"elevenlabs": SENTINEL, "fal": None}, other="")
    assert sorted(s) == ["elevenlabs"]
    assert s["elevenlabs"] == SENTINEL
    assert SENTINEL not in repr(s) and SENTINEL not in str(s)
    assert "elevenlabs" in repr(s)  # the names are fine; the values are not
    assert not Secrets(elevenlabs=None) and as_secrets({"fal": None}) is None
    assert s.with_(fal="k").get("fal") == "k" and "fal" not in s


def test_secrets_refuses_serialization_and_pickling():
    s = Secrets(elevenlabs=SENTINEL)
    with pytest.raises(TypeError):
        json.dumps({"record": s})
    with pytest.raises(TypeError, match="pickled"):
        pickle.dumps(s)
    # Copies are the same object: nothing to serialize, nothing to leak.
    assert copy.copy(s) is s and copy.deepcopy(s) is s
    assert SENTINEL not in f"{s!r}{s}"


@pytest.mark.parametrize("bad", [{"elevenlabs": 42}, {"": "k"}, {3: "k"}])
def test_secrets_rejects_non_text(bad):
    with pytest.raises(TypeError):
        Secrets(bad)


def test_as_secrets_coerces_a_plain_mapping_and_rejects_non_mappings():
    coerced = as_secrets({"elevenlabs": SENTINEL})
    assert isinstance(coerced, Secrets) and coerced["elevenlabs"] == SENTINEL
    assert as_secrets(coerced) is coerced
    with pytest.raises(TypeError):
        as_secrets("sk-not-a-mapping")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The invariant: the sentinel reaches execute and nothing that persists
# ---------------------------------------------------------------------------


def test_fan_out_threads_the_secret_to_execute_and_nowhere_else(
    project, tmp_path, caplog
):
    caplog.set_level(logging.DEBUG)
    t = _SpendsASecret()
    fo = _fan_out(t, project, _sections(project, "intro", "verse"))

    # A plain dict at the top is a Secrets below: the Transform sees the
    # redacting type, not the caller's dict.
    result = fan_out_execute(t, project, fo, secrets={"elevenlabs": SENTINEL})

    assert t.seen == [SENTINEL, SENTINEL]
    assert result.is_complete and len(result.items) == 2

    # 1. the plan (and its stamped/serialized forms)
    for unit in fo.units:
        assert SENTINEL not in json.dumps(plan_to_dict(unit.plan))
        assert SENTINEL not in json.dumps(
            plan_to_dict(stamp_transform_identity(unit.plan, t))
        )
        assert SENTINEL not in json.dumps(
            [a.model_dump(mode="json") for a in unit.skeleton]
        )
    # 2. the cache key: a credential is not an audio-affecting input
    assert cache_key(t, "render", "intro") == fo.units[0].skeleton[0].body["cache_key"]
    # 3. the run record
    assert SENTINEL not in json.dumps(result.to_record())
    # 4. the graph document — every file the project wrote
    assert _grep_tree(tmp_path) == []
    # 5. the log
    assert SENTINEL not in caplog.text
    assert "rendering" in caplog.text  # the line was emitted; the key was not


def test_a_legacy_execute_without_the_keyword_still_runs(project):
    t = _Legacy()
    fo = _fan_out(t, project, _sections(project, "intro"))
    result = fan_out_execute(t, project, fo, secrets={"elevenlabs": SENTINEL})
    assert result.is_complete
    assert t.seen == ["<not offered>"]


def test_no_secrets_means_none_at_execute(project):
    t = _SpendsASecret()
    fo = _fan_out(t, project, _sections(project, "intro"))
    fan_out_execute(t, project, fo)
    fan_out_execute(t, project, fo, secrets={"elevenlabs": None})
    assert t.seen == [None, None]


def test_a_job_threads_the_secret_and_keeps_it_out_of_the_index(
    project, tmp_path, caplog
):
    caplog.set_level(logging.DEBUG)
    t = _SpendsASecret()
    sections = _sections(project, "intro", "verse")
    seen_by_dispatch: list = []

    def weave(proj, params, *, job_id, secrets=None):
        seen_by_dispatch.append(secrets)
        fo = _fan_out(t, proj, sections)
        return fan_out_execute(t, proj, fo, secrets=secrets).to_record()

    job = jobs.enqueue(
        project,
        "weave",
        {"sections": ["intro", "verse"]},
        dispatch={"weave": weave},
        secrets=Secrets(elevenlabs=SENTINEL),
    )
    job = _wait_terminal(project, job.job_id)

    assert job.status == "succeeded", job.error
    assert t.seen == [SENTINEL, SENTINEL]
    (given,) = seen_by_dispatch
    assert isinstance(given, Secrets) and given["elevenlabs"] == SENTINEL
    # the job facade, its dict projection, the index + au store on disk
    assert SENTINEL not in json.dumps(jobs.to_dict(job), default=str)
    assert _grep_tree(tmp_path) == []
    assert SENTINEL not in caplog.text


def test_a_dispatch_callable_without_the_keyword_is_called_as_before(project):
    calls: list = []

    def plain(proj, params):
        calls.append(params)
        return {"ok": True}

    job = jobs.enqueue(
        project, "op", {"a": 1}, dispatch={"op": plain}, secrets={"fal": SENTINEL}
    )
    job = _wait_terminal(project, job.job_id)
    assert job.status == "succeeded", job.error
    assert calls == [{"a": 1}]


# ---------------------------------------------------------------------------
# What nw itself does with a secret: the fal credential, bound for the call
# ---------------------------------------------------------------------------


def test_using_secrets_binds_fal_for_the_block_only():
    assert current_fal_key() is None
    with using_secrets({FAL_SECRET: SENTINEL}):
        assert current_fal_key() == SENTINEL
    assert current_fal_key() is None
    with using_secrets({"elevenlabs": SENTINEL}):  # not nw's to bind
        assert current_fal_key() is None
    with using_secrets(None):
        assert current_fal_key() is None


def test_base_transform_execute_binds_the_fal_secret_around_plan_execution(
    monkeypatch, project
):
    """The default ``execute`` is not an accepted-and-ignored keyword: a
    ``"fal"`` secret is the credential falaw sees while the plan runs."""
    observed: list = []

    def fake_execute_plan_isolated(plan, **kwargs):
        observed.append(current_fal_key())
        return SimpleNamespace(
            outcomes=(),
            produced=(),
            estimated_spend_usd=0.0,
            cache_hit_savings_usd=0.0,
            has_unknown_costs=False,
            artifacts_or_raise=lambda: (),
        )

    # ``nw.transforms`` as an attribute of the package is the Registry; the
    # module itself is what the default execute resolves the engine from.
    transforms_module = importlib.import_module("nw.transforms")
    monkeypatch.setattr(
        transforms_module, "execute_plan_isolated", fake_execute_plan_isolated
    )

    class _Base(BaseTransform):
        name = "test.base"
        output_kind = RENDER_URI

    _Base().execute(project, Plan(calls=()), (), secrets={FAL_SECRET: SENTINEL})
    _Base().execute(project, Plan(calls=()), ())
    assert observed == [SENTINEL, None]
    assert current_fal_key() is None


# ---------------------------------------------------------------------------
# Review findings on the first cut (nw#78): the ambient binding, redaction,
# registry-wide conformance, identity equality
# ---------------------------------------------------------------------------


class _LegacyFalSpender(_Legacy):
    """A pre-seam override that spends fal: it never sees `secrets`, so the
    only way the caller's key can reach its calls is the ambient binding."""

    name = "test.legacy_fal"

    def execute(self, project, plan, skeleton, *, use_cache=True, force=False):
        self.seen.append(current_fal_key())
        return super().execute(project, plan, skeleton)


def test_fan_out_binds_the_fal_secret_even_for_a_non_accepting_override(project):
    t = _LegacyFalSpender()
    fo = _fan_out(t, project, _sections(project, "intro", "verse"))
    result = fan_out_execute(t, project, fo, secrets={FAL_SECRET: SENTINEL})
    assert result.is_complete
    assert [s for s in t.seen if s != "<not offered>"] == [SENTINEL, SENTINEL]
    assert current_fal_key() is None  # released after each unit


def test_every_registered_transform_accepts_secrets():
    """No registered Transform may silently bill the server's key: the ones
    that override `execute` (nw's own shot renderers included) declare the
    keyword, so `fan_out_execute` passes it and a direct call is not a
    TypeError. reelee's pre-seam overrides are covered by the ambient
    binding; nw's own must take the seam properly."""
    import inspect

    missing = [
        name
        for name in nw.list_transforms()
        if "secrets" not in inspect.signature(nw.get_transform(name).execute).parameters
    ]
    assert missing == []


def test_a_dispatch_without_the_keyword_still_runs_under_the_fal_binding(project):
    """The worker binds the secret around the call regardless of the
    callable's signature (replacing that binding with a no-op must fail here)."""
    observed = []

    def plain(proj, params):
        observed.append(current_fal_key())
        return {"ok": True}

    job = jobs.enqueue(
        project, "op", {"a": 1}, dispatch={"op": plain}, secrets={FAL_SECRET: SENTINEL}
    )
    job = _wait_terminal(project, job.job_id)
    assert job.status == "succeeded", job.error
    assert observed == [SENTINEL]
    assert current_fal_key() is None


def test_a_failing_job_never_persists_a_key_quoted_in_its_error(project, tmp_path):
    """A provider echoing the key in an auth error would otherwise put it in
    the au store and the job index, which persist the exception text."""

    def boom(proj, params, *, secrets=None):
        raise RuntimeError(f"401 from provider: key {secrets['elevenlabs']} rejected")

    job = jobs.enqueue(
        project, "op", {"a": 1}, dispatch={"op": boom}, secrets={"elevenlabs": SENTINEL}
    )
    job = _wait_terminal(project, job.job_id)
    assert job.status == "failed"
    assert "<redacted:elevenlabs>" in (job.error or "")
    assert SENTINEL not in json.dumps(jobs.to_dict(job), default=str)
    assert _grep_tree(tmp_path) == []


def test_a_failing_unit_never_files_a_key_quoted_in_its_reason(project, tmp_path):
    class _Echoes(_SpendsASecret):
        name = "test.echoes"

        def execute(self, project, plan, skeleton, *, secrets=None, **kw):
            raise RuntimeError(f"rejected: {secrets['elevenlabs']}")

    t = _Echoes()
    fo = _fan_out(t, project, _sections(project, "intro"))
    result = fan_out_execute(t, project, fo, secrets={"elevenlabs": SENTINEL})
    (item,) = result.items
    assert item.status == "failed"
    assert "<redacted:elevenlabs>" in item.reason
    assert SENTINEL not in str(item.error)  # the exception itself was scrubbed
    assert SENTINEL not in json.dumps(result.to_record())
    assert _grep_tree(tmp_path) == []


def test_secrets_equality_is_identity_not_value():
    a = Secrets(fal=SENTINEL)
    assert a == a
    assert a != Secrets(fal=SENTINEL)
    assert a != {"fal": SENTINEL}  # a value comparison would be an oracle
    assert len({a, Secrets(fal=SENTINEL)}) == 2


# ---------------------------------------------------------------------------
# Second look (nw#78): redaction is of the RENDERED text, and the shot
# renderers bind the key at the falaw boundary
# ---------------------------------------------------------------------------


class _CustomStr(Exception):
    """Renders from a field, and cannot be rebuilt from one positional arg."""

    def __init__(self, detail, code):
        super().__init__("custom", code)
        self.detail = detail

    def __str__(self):
        return f"custom failure: {self.detail}"


def _raising(exc_factory):
    def boom(proj, params, *, secrets=None):
        raise exc_factory(secrets["elevenlabs"])

    return boom


@pytest.mark.parametrize(
    "label, exc_factory",
    [
        ("dict arg", lambda key: RuntimeError({"detail": key})),
        ("custom __str__", lambda key: _CustomStr(key, 500)),
        (
            "OSError triple",
            lambda key: OSError(2, f"no such key {key}", f"/path/{key}"),
        ),
    ],
)
def test_a_failing_job_redacts_the_rendered_text_whatever_built_it(
    project, tmp_path, label, exc_factory
):
    job = jobs.enqueue(
        project,
        "op",
        {"a": 1},
        dispatch={"op": _raising(exc_factory)},
        secrets={"elevenlabs": SENTINEL},
    )
    job = _wait_terminal(project, job.job_id)
    assert job.status == "failed", label
    assert SENTINEL not in (job.error or ""), label
    assert "<redacted:elevenlabs>" in (job.error or ""), label
    assert SENTINEL not in json.dumps(jobs.to_dict(job), default=str), label
    assert _grep_tree(tmp_path) == [], label


def test_redact_exception_rebuilds_or_falls_back_but_never_renders_the_key():
    from nw.secrets import RedactedError, redact_exception

    secrets = Secrets(elevenlabs=SENTINEL)

    plain = RuntimeError(f"bad {SENTINEL}")
    same = redact_exception(plain, secrets)
    assert same is plain and SENTINEL not in str(same)  # args scrub sufficed

    oserr = redact_exception(OSError(2, f"no {SENTINEL}", f"/p/{SENTINEL}"), secrets)
    assert isinstance(oserr, OSError) and SENTINEL not in str(oserr)

    custom = redact_exception(_CustomStr(SENTINEL, 500), secrets)
    assert isinstance(custom, RedactedError)
    assert custom.original_type == "_CustomStr" and SENTINEL not in str(custom)

    # the chain is scrubbed too
    try:
        try:
            raise ValueError(f"inner {SENTINEL}")
        except ValueError as inner:
            raise RuntimeError({"detail": SENTINEL}) from inner
    except RuntimeError as outer:
        scrubbed = redact_exception(outer, secrets)
    assert SENTINEL not in str(scrubbed) and SENTINEL not in str(scrubbed.__cause__)


def test_shot_renderers_bind_the_fal_secret_at_the_falaw_boundary(monkeypatch, project):
    """The registry test pins the signature; this pins the binding: with
    `execute_plan_isolated` stubbed, every registered shot renderer sees the
    caller's key as the fal credential at the moment it would call fal."""
    import nw.workflow as workflow
    from nw.transforms._adapters import render_strategy as rs_module

    observed: list = []

    class _Observed(Exception):
        pass

    def fake_execute_plan_isolated(plan, **kwargs):
        observed.append(current_fal_key())
        raise _Observed()

    monkeypatch.setattr(rs_module, "execute_plan_isolated", fake_execute_plan_isolated)
    monkeypatch.setattr(workflow, "prepare_shot", lambda *a, **k: None)

    names = [n for n in nw.list_transforms() if n.startswith("shot_to_render_result.")]
    assert names, "nw's shot renderers should be registered"
    from lacing import MediaRef, TimeInterval

    ref = MediaRef(
        asset_id=project.graph.asset_id, interval=TimeInterval.from_seconds(0, 0)
    )
    for name in names:
        t = nw.get_transform(name)
        skel = Annotation(
            id=uuid4(),
            tier="render-result",
            reference=ref,
            body={"shot_id": "s01"},
            body_schema_uri=RENDER_URI,
            provenance=derive_provenance(
                t, TransformInputs(primary=()), attributed_to="agent:test"
            ),
        )
        with pytest.raises(_Observed):
            t.execute(project, Plan(calls=()), (skel,), secrets={FAL_SECRET: SENTINEL})
    assert observed == [SENTINEL] * len(names)
    assert current_fal_key() is None
