"""An app's own render kinds get partial re-render for free (nw#9, tasks 2 and 3).

nw#9 asked that the render machinery serve *any* render kind — an audio weave as
much as a video shot — by letting an app supply its own render-node schemas and
Transforms, with cache keys and freshness working for them unchanged. braidio
does this in production; this file is the evidence inside nw's own suite, so the
claim no longer rests on prose about another repository.

Everything here an *app* would write lives in this file: two body schemas
(``narration-render``, ``episode-render``), the turn they render from, and a
``turn_to_narration`` Transform. nw supplies only the generic seams —
``register_body_schema`` (lacing), ``register_transform``, ``BaseTransform``,
``ProjectGraph.add_annotation`` and ``stale_after`` — and none of them knows
what audio is.

The pair, per this suite's convention:

* re-narrating one turn with **identical bytes** invalidates nothing — early
  cutoff, so the episode mix is not re-billed;
* re-narrating it with **new bytes** invalidates the episode and *only* the
  episode: the sibling turn's narration stays fresh, which is the partial
  re-render nw#9 is about.
"""

from __future__ import annotations

import sys
import types
from typing import Optional
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel, ConfigDict

import nw
from lacing import (
    Annotation,
    MediaRef,
    Provenance,
    RationalTime,
    TimeInterval,
    hash_bytes,
    register_body_schema,
)
from lacing.schema import get_body_schema
from nw.transforms import BaseTransform, TransformInputs

# --- what the app supplies -----------------------------------------------------

TURN = "annot://schema/test-app-turn/v1"
NARRATION = "annot://schema/test-app-narration-render/v1"
EPISODE = "annot://schema/test-app-episode-render/v1"
TRANSFORM_NAME = "turn_to_narration.test_app.default"


class TurnBodyV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_id: str
    text: str


class NarrationRenderBodyV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_id: str
    voice: str
    artifact_id: Optional[str] = None


class EpisodeRenderBodyV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    narration_artifact_ids: tuple[str, ...]


for _uri, _model in (
    (TURN, TurnBodyV1),
    (NARRATION, NarrationRenderBodyV1),
    (EPISODE, EpisodeRenderBodyV1),
):
    register_body_schema(_uri, _model)


class TurnToNarration(BaseTransform):
    """One TTS call per turn — the app's render step, on nw's generic base."""

    name = TRANSFORM_NAME
    input_kinds = (TURN,)
    output_kind = NARRATION
    generate_when = "static"

    def plan(self, project, inputs: TransformInputs, *, params=None):
        (turn,) = inputs.primary
        return self.plan_for(project, turn)

    def plan_for(self, project, turn: Annotation, *, ann_id: UUID | None = None):
        from falaw import CallPlan, Plan

        plan = Plan(
            calls=(
                CallPlan(
                    tool="text_to_speech",
                    application="fal-ai/test-tts",
                    arguments={"text": turn.body["text"], "voice": "narrator"},
                    output_kind="audio",
                    estimated_cost_usd=0.01,
                ),
            )
        )
        skeleton = Annotation(
            id=ann_id or uuid4(),
            tier="narration-render",
            reference=MediaRef(asset_id=project.graph.asset_id, interval=_iv(0, 0)),
            body={"turn_id": turn.body["turn_id"], "voice": "narrator"},
            body_schema_uri=NARRATION,
            provenance=Provenance(
                was_generated_by=f"transform:{self.name}@{self.impl_version}",
                was_attributed_to="agent:test-app",
                was_derived_from=[turn.id],
                generated_at_time=RationalTime.now(),
                activity="derive",
            ),
        )
        return plan, (skeleton,)


@pytest.fixture
def narrate():
    """Register the app's Transform the way an app does, at import; unregister after."""
    nw.register_transform(TRANSFORM_NAME, TurnToNarration())
    try:
        yield nw.get_transform(TRANSFORM_NAME)
    finally:
        nw.transforms.pop(TRANSFORM_NAME, None)


# --- a fal_client stub whose audio URLs the test controls ------------------------


class _FakeFal:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.next_audio_url = "http://cdn/tts-1.mp3"

    def subscribe(self, application, *, arguments, with_logs, on_queue_update):
        self.calls.append({"application": application, "arguments": dict(arguments)})
        return {"audio": {"url": self.next_audio_url, "content_type": "audio/mpeg"}}


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


AUDIO_1 = b"ID3-pretend-narration-turn-1" * 4
AUDIO_2 = b"ID3-pretend-narration-turn-2" * 4
AUDIO_1_REDONE = b"ID3-pretend-narration-turn-1-new-voice" * 4


# --- scaffolding -----------------------------------------------------------------


def _iv(a: float, b: float) -> TimeInterval:
    return TimeInterval.from_seconds(a, b)


def _turn(proj, turn_id: str, text: str) -> Annotation:
    ann = Annotation(
        id=uuid4(),
        tier="turn",
        reference=MediaRef(asset_id=proj.graph.asset_id, interval=_iv(0, 0)),
        body={"turn_id": turn_id, "text": text},
        body_schema_uri=TURN,
        provenance=Provenance(
            was_generated_by="agent:author",
            was_attributed_to="agent:author",
            generated_at_time=RationalTime.now(),
            activity="author",
        ),
    )
    proj.graph.add_annotation(ann)
    return ann


def _narrate(proj, transform, fal, fake_assets, turn, *, url, data, ann_id=None):
    fal.next_audio_url = url
    fake_assets.serve(url, data)
    plan, skeleton = transform.plan_for(proj, turn, ann_id=ann_id)
    result = transform.execute(proj, plan, skeleton, force=ann_id is not None)
    (narration,) = result.annotations
    return narration


def _episode(proj, narrations) -> Annotation:
    """The app's mix step, written through the graph's one choke point."""
    ann = Annotation(
        id=uuid4(),
        tier="episode-render",
        reference=MediaRef(asset_id=proj.graph.asset_id, interval=_iv(0, 0)),
        body={"narration_artifact_ids": [n.body["artifact_id"] for n in narrations]},
        body_schema_uri=EPISODE,
        provenance=Provenance(
            was_generated_by="transform:mix.test_app@1",
            was_attributed_to="agent:test-app",
            was_derived_from=[n.id for n in narrations],
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


def _woven_episode(tmp_path, narrate, fal, fake_assets):
    proj = nw.Project.init(tmp_path / "p")
    t1 = _turn(proj, "t1", "In 1965 they went electric.")
    t2 = _turn(proj, "t2", "The crowd did not take it well.")
    n1 = _narrate(
        proj, narrate, fal, fake_assets, t1, url="http://cdn/n1.mp3", data=AUDIO_1
    )
    n2 = _narrate(
        proj, narrate, fal, fake_assets, t2, url="http://cdn/n2.mp3", data=AUDIO_2
    )
    episode = _episode(proj, (n1, n2))
    return proj, t1, n1, n2, episode


def _renarrate(proj, narrate, fal, fake_assets, turn, narration, *, url, data):
    """Re-render one turn's narration in place, through the production path."""
    _remove(proj, narration.id)
    return _narrate(
        proj, narrate, fal, fake_assets, turn, url=url, data=data, ann_id=narration.id
    )


# --- task 2: app-supplied render-node schemas, no nw change ------------------------


def test_an_app_registers_its_own_render_kinds(narrate):
    entry = next(e for e in nw.transform_catalog() if e["name"] == TRANSFORM_NAME)
    assert entry["input_kinds"] == [TURN]
    assert entry["output_kind"] == NARRATION
    assert get_body_schema(NARRATION) is NarrationRenderBodyV1


def test_the_app_render_node_is_written_with_its_content_id(
    tmp_path, narrate, fal, fake_assets
):
    proj, _, n1, n2, episode = _woven_episode(tmp_path, narrate, fal, fake_assets)
    assert n1.body["artifact_id"] == hash_bytes(AUDIO_1)
    assert n2.body["artifact_id"] == hash_bytes(AUDIO_2)
    stored = {a.id: a for a in nw.iter_all_annotations(proj.root)}
    assert stored[n1.id].body_schema_uri == NARRATION
    assert stored[episode.id].body_schema_uri == EPISODE


# --- task 3: cache key and freshness work for those kinds ---------------------------


def test_renarrating_with_identical_bytes_invalidates_nothing(
    tmp_path, narrate, fal, fake_assets
):
    proj, t1, n1, _, episode = _woven_episode(tmp_path, narrate, fal, fake_assets)

    redone = _renarrate(
        proj,
        narrate,
        fal,
        fake_assets,
        t1,
        n1,
        url="http://cdn/n1-again.mp3",
        data=AUDIO_1,
    )

    assert len(fal.calls) == 3  # it genuinely re-ran
    assert redone.body == n1.body
    assert nw.stale_after(proj.root, redone.id) == []
    assert episode.id in {a.id for a in nw.descendants_of(proj.root, redone.id)}


def test_renarrating_with_new_bytes_invalidates_the_episode_and_nothing_else(
    tmp_path, narrate, fal, fake_assets
):
    proj, t1, n1, n2, episode = _woven_episode(tmp_path, narrate, fal, fake_assets)

    redone = _renarrate(
        proj,
        narrate,
        fal,
        fake_assets,
        t1,
        n1,
        url="http://cdn/n1-v2.mp3",
        data=AUDIO_1_REDONE,
    )

    assert redone.body["artifact_id"] == hash_bytes(AUDIO_1_REDONE)
    stale = {a.id for a in nw.all_stale(proj.root)}
    assert episode.id in stale
    assert n2.id not in stale  # the sibling turn is not re-rendered
    assert redone.id not in stale


def test_a_second_identical_request_is_served_from_the_cache(
    tmp_path, narrate, fal, fake_assets
):
    """The falaw cache key covers an app's Transform like any other."""
    proj, t1, *_ = _woven_episode(tmp_path, narrate, fal, fake_assets)
    n_calls = len(fal.calls)

    plan, skeleton = narrate.plan_for(proj, t1)
    result = narrate.execute(proj, plan, skeleton)

    assert len(fal.calls) == n_calls
    assert result.annotations[0].body["artifact_id"] == hash_bytes(AUDIO_1)
