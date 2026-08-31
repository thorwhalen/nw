"""``nw.transforms.cache_key`` / ``cached_output`` — the non-fal cache identity (nw#54).

The migration constraint is the load-bearing test here: braidio's two PAID
Transforms hand-rolled this digest via a private import from another package,
and adopting the shared helper must produce BYTE-IDENTICAL keys at the default
impl_version — a changed key re-bills every cached paid call for nothing.
"""

from __future__ import annotations

import hashlib

from nw.transforms import DFLT_IMPL_VERSION, cache_key, cached_output


class _T:
    name = "narration_render.tts"
    impl_version = DFLT_IMPL_VERSION


def _legacy_sha256_key(*parts) -> str:
    """The exact digest braidio imported from mixing._cache — pinned here as a
    vector, not imported: reproducing the RULE without the private import is
    the point of nw#54."""
    h = hashlib.sha256()
    for part in parts:
        if part is None:
            part = b""
        elif isinstance(part, str):
            part = part.encode()
        h.update(part)
        h.update(b"\0")
    return h.hexdigest()


def test_at_the_default_version_keys_are_byte_identical_to_the_legacy_digest():
    """Adopting the helper must re-bill NOTHING: every key braidio ever wrote
    for a paid TTS/ffmpeg output still matches."""
    parts = ("narration", "hello world", "voice-7", None, '{"speed": 1.0}')
    assert cache_key(_T(), *parts) == _legacy_sha256_key(*parts)
    assert cache_key(_T(), b"raw", "s", None) == _legacy_sha256_key(b"raw", "s", None)


def test_a_version_bump_salts_the_key_scoped_to_the_transform():
    """Same interface, changed behaviour → the key MUST move (invariant 3) —
    and only for the bumped transform."""

    class Bumped:
        name = "narration_render.tts"
        impl_version = "2"

    class OtherBumped:
        name = "segment_extraction.ffmpeg"
        impl_version = "2"

    base = cache_key(_T(), "x")
    b1 = cache_key(Bumped(), "x")
    b2 = cache_key(OtherBumped(), "x")
    assert base != b1
    assert b1 != b2  # the salt carries the NAME: invalidation is scoped


def test_part_encodings_match_the_legacy_rule_exactly():
    # None vs "" are the same under the legacy rule; the helper must not
    # "fix" that quietly — a fix would move keys on paid calls.
    assert cache_key(_T(), None) == cache_key(_T(), "")
    # Delimiting prevents concatenation collisions.
    assert cache_key(_T(), "ab", "c") != cache_key(_T(), "a", "bc")


def test_cached_output_finds_completed_nodes_only(tmp_path):
    import nw
    from uuid import uuid4

    from lacing import (
        Annotation,
        MediaRef,
        Provenance,
        RationalTime,
        Tier,
        TierStereotype,
        TimeInterval,
    )

    proj = nw.Project.init(tmp_path / "p")
    key = cache_key(_T(), "narration", "text")

    def _node(body):
        return Annotation(
            id=uuid4(),
            tier="narration-renders",
            reference=MediaRef(
                asset_id=proj.graph.asset_id,
                interval=TimeInterval.from_seconds(0, 1),
            ),
            body=body,
            body_schema_uri="annot://schema/narration-render/v1",
            provenance=Provenance(
                was_generated_by="transform:test@1",
                was_attributed_to="agent:test",
                was_derived_from=[],
                generated_at_time=RationalTime.now(),
                activity="derive",
            ),
        )

    with proj.graph._open() as store:
        store.add_tier(Tier(name="narration-renders", stereotype=TierStereotype.NONE))
        # A completed node with the key, an INCOMPLETE one (no artifact_id)
        # with the same key, and a completed one with a different key.
        store.add(_node({"cache_key": key, "artifact_id": "a" * 64}))
        store.add(_node({"cache_key": key}))
        store.add(_node({"cache_key": "other", "artifact_id": "b" * 64}))

    hit = cached_output(proj.root, "narration-renders", key)
    assert hit is not None
    assert hit.body["artifact_id"] == "a" * 64
    assert cached_output(proj.root, "narration-renders", "missing") is None
