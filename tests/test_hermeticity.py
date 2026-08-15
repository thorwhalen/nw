"""The offline suite is offline — and the thing that proves it is armed.

nw#35: falaw content-addresses every media result, so it reads the bytes behind
each fal URL. nw's stubs fake the fal *response* and not the asset transport, so
made-up URLs were being resolved for real. ``tests/conftest.py`` fixes that by
re-exporting :mod:`falaw.testing`'s fixtures (falaw#27); these tests exist
because the fix is only worth anything if its guard actually fires — they pin
the guard properties nw relies on, so a falaw bump that weakened them turns
this suite red rather than silently networked.

The failure mode worth naming: falaw **swallows** a failed asset fetch by design
(it degrades to a URL-only artifact with a warning rather than turning
downstream suites red). A guard that only raised at the call site would
therefore be invisible — the test would still pass. So the guard *records* every
blocked attempt and fails at teardown, and that is what
:func:`test_a_swallowed_connection_attempt_is_still_recorded` pins down.
"""

from __future__ import annotations

import hashlib
import socket
import urllib.request

import pytest

from falaw.testing import OutboundNetworkAttempt


# --- the no-outbound-connection guard ---------------------------------------


def test_outbound_dns_and_connects_are_blocked(no_outbound_network):
    """A non-loopback lookup or connect raises instead of touching the network."""
    with pytest.raises(OutboundNetworkAttempt):
        socket.getaddrinfo("example.invalid", 80)  # RFC 2606 reserved TLD
    with socket.socket() as sock, pytest.raises(OutboundNetworkAttempt):
        # RFC 5737 TEST-NET-1 + a short timeout, so that if this guard is ever
        # removed the test fails in milliseconds against an address that is
        # guaranteed not to be routed, rather than stalling against a live host.
        sock.settimeout(0.05)
        sock.connect(("192.0.2.1", 80))

    assert len(no_outbound_network) == 2
    # Drain: this test *meant* to trip the guard, so it must not also fail at
    # teardown. Every other test leaves the list empty.
    no_outbound_network.clear()


def test_a_swallowed_connection_attempt_is_still_recorded(no_outbound_network):
    """The guard survives a caller that swallows the refusal.

    The shared guard's refusal is a ``BaseException`` precisely so falaw's own
    ``except Exception`` funnels cannot absorb it — but a consumer's broad
    ``except BaseException`` (or a subprocess) still can, which is why the
    *record* is the half no swallow reaches. This test swallows as broadly as
    Python allows and asserts the record survived.
    """
    try:
        # Never leaves the process: the guard refuses before the resolver.
        urllib.request.urlopen("http://x/img.png")  # noqa: S310
    except BaseException:  # noqa: BLE001 — the broadest swallow there is
        pass

    assert no_outbound_network, (
        "a blocked attempt must be recorded, not merely raised: falaw degrades "
        "a failed asset fetch to a warning, so a raise-only guard would leave "
        "the suite green with the regression back"
    )
    no_outbound_network.clear()


@pytest.mark.parametrize(
    "host", ["localhost", "127.0.0.1", "::1", "db.test.localhost"]
)
def test_loopback_stays_reachable(host, no_outbound_network):
    """Local services (e.g. the Postgres backend tests) are not collateral."""
    try:
        socket.getaddrinfo(host, 0)
    except socket.gaierror:
        pass  # resolvable-or-not is the platform's business; unblocked is ours
    assert no_outbound_network == []


# --- the fake asset transport -----------------------------------------------


def test_falaw_asset_fetch_is_served_from_memory(fake_assets):
    """falaw's content addressing runs for real, over injected bytes.

    The fake installs through :func:`falaw.content.using_url_fetcher` — the
    public seam every nw path funnels through: ``execute_plan``,
    ``materialize_asset`` and ``content_ref_for_url`` all resolve the default
    ``UrlFetcher`` at call time.
    """
    from lacing import ArtifactStore
    from falaw.content import content_ref_for_url

    url = "https://fal.media/files/nw-hermeticity-probe.png"
    ref = content_ref_for_url(url, store=ArtifactStore.in_memory())

    expected = hashlib.sha256(fake_assets.synthetic(url)).hexdigest()
    assert ref.content_hash == expected
    assert ref.bytes_size > 0  # a degraded (URL-only) artifact reports 0
    assert fake_assets.fetched == [url]


def test_pinned_bytes_and_404s_are_available_to_tests(fake_assets):
    """``serve`` / ``fail`` are the two knobs a test needs from the transport."""
    from lacing import ArtifactStore
    from falaw.content import content_ref_for_url
    from falaw.errors import FalAssetFetchError

    store = ArtifactStore.in_memory()
    fake_assets.serve("https://fal.media/a.png", b"identical-bytes")
    fake_assets.serve("https://fal.media/b.png", b"identical-bytes")
    a = content_ref_for_url("https://fal.media/a.png", store=store)
    b = content_ref_for_url("https://fal.media/b.png", store=store)
    # Two URLs, one content hash — the case content addressing exists for.
    assert a.content_hash == b.content_hash

    fake_assets.fail("https://fal.media/expired.png")
    with pytest.raises(FalAssetFetchError):
        content_ref_for_url("https://fal.media/expired.png", store=store)


# --- falaw's on-disk state is isolated --------------------------------------


def test_falaw_cache_is_isolated_to_the_test(tmp_path):
    """No test writes synthetic bytes into the developer's real falaw cache."""
    from falaw.cache import _cache_dir

    assert _cache_dir().startswith(str(tmp_path))
