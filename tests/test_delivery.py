"""The genre->host delivery seam.

The defect this module exists to prevent was not caught by either side's tests,
because each side tested only itself: reelee asserted its route worked against a
resolver returning ``Path``, muvid asserted its resolver returned a correct
``ResolvedArtifact``, and both were right. So the tests here deliberately assert
the *agreement* — that a genre-shaped resolver satisfies the Protocol the host
type-checks against — rather than either half in isolation.
"""

from pathlib import Path

import pytest

from nw.delivery import Deliverable, Resolver, format_ref, parse_ref


def test_a_genre_shaped_resolver_satisfies_the_host_protocol():
    """The assertion that would have caught the production 500.

    ``Resolver`` is ``runtime_checkable`` precisely so this check is possible;
    a host may use it as a boot-time guard on its resolver registry.
    """

    def resolve(email, project_id, artifact_id):
        return Deliverable(
            path=Path("/tmp/final.mp4"),
            content_type="video/mp4",
            filename="proj-cut-1.mp4",
        )

    assert isinstance(resolve, Resolver)
    got = resolve("a@b.c", "proj", "1")
    # The host reads these three off the return value. If a genre returns a bare
    # Path (the old contract), every one of these raises — which is the bug.
    assert got.path == Path("/tmp/final.mp4")
    assert got.content_type == "video/mp4"
    assert got.filename == "proj-cut-1.mp4"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("cut 4", 4),
        ("cut4", 4),
        ("CUT 4", 4),
        ("cut-4", 4),
        ("cut_4", 4),
        (" cut  -  4 ", 4),
        # The hyphen is a SEPARATOR, not a sign. `cut - 4` has to mean 4, so
        # `cut -1` means 1 by the same rule — and there is no negative ordinal
        # for it to be confused with.
        ("cut -1", 1),
        ("#4", 4),
        ("# 4", 4),
        ("4", 4),
        ("  12  ", 12),
    ],
)
def test_every_spelling_a_human_might_use_parses(text, expected):
    assert parse_ref(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "b02fc05417ea",  # a raw render id — must fall through to id resolution
        "cut",
        "",
        "cut 0",  # deliverables number from 1; 0 would index a neighbour
        "cut 4.5",
        "cut 4 and cut 5",
        "1234567",  # 7 digits: an id-like string, not an ordinal
        None,
        4,  # not a string
    ],
)
def test_non_references_return_none_rather_than_raising(text):
    """``None`` is an ordinary answer, not an error — it means 'treat as an id'.

    A raise here would make every raw-id lookup an exception path.
    """
    assert parse_ref(text) is None


def test_ref_round_trips_through_its_own_formatting():
    """Whatever we print, we must be able to read back."""
    for n in (1, 2, 9, 10, 99, 100, 12345):
        assert parse_ref(format_ref(n)) == n


def test_kind_is_derived_not_declared():
    """A genre never states the kind twice; presentation follows content_type."""
    mk = lambda ct: Deliverable(Path("x"), ct, "x")
    assert mk("video/mp4").kind == "video"
    assert mk("video/quicktime").kind == "video"
    assert mk("audio/mpeg").kind == "audio"
    assert mk("image/png").kind == "image"
    assert mk("application/pdf").kind == "file"
    assert mk("").kind == "file"


def test_label_prefers_the_speakable_reference():
    """What a human is shown, in priority order: ref, then id, then filename."""
    assert Deliverable(Path("x"), "video/mp4", "f.mp4", ref="cut 3").label == "cut 3"
    assert (
        Deliverable(Path("x"), "video/mp4", "f.mp4", artifact_id="b02f").label == "b02f"
    )
    assert Deliverable(Path("x"), "video/mp4", "f.mp4").label == "f.mp4"


def test_deliverable_is_frozen():
    """A resolved deliverable is a fact about the past, not a mutable buffer."""
    d = Deliverable(Path("x"), "video/mp4", "f.mp4")
    with pytest.raises(Exception):
        d.path = Path("y")
