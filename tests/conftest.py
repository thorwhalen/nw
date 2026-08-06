"""Shared fixtures making nw's offline test suite genuinely offline.

Three things every test in this package needs, and none should have to repeat.

**1. A fake asset transport (`fake_assets`).** ``falaw.execute_plan`` reads the
bytes behind every media result so it can content-address it — a fal URL is
neither unique-per-content nor durable, so it cannot be an identity. nw's tests
stub the fal *response* but not the asset transport, so the made-up URLs those
stubs carry (``http://x/img.png``, ``https://example.invalid/panel.png``) were
being resolved for real: a DNS lookup and a connection attempt per test.

They passed anyway, because falaw degrades a failed fetch to a URL-only
artifact with a ``UserWarning`` rather than raising — deliberately, so it does
not turn downstream suites red. So this was never a breakage; it was a
hermeticity problem with three costs: the suite was slower and behaved
differently on a machine with no network; a stub URL that happened to point at
a host returning **200** would have pulled arbitrary internet content into the
cache with the test still green; and the tests were exercising the *degraded*
path, never the content addressing they appear to exercise.

The seam is ``falaw.content._http_chunks`` — the default :data:`falaw.content
.UrlFetcher`, which every entry point resolves at call time
(``execute_plan(asset_fetcher=…)``, ``materialize_asset(fetcher=…)`` and
``content_ref_for_url(fetcher=…)`` all fall back to it). Patching it once
covers every path, including the ones nw reaches *through* its own public API,
which takes no transport argument and should not grow one just for tests.
falaw's own ``tests/conftest.py`` uses exactly this seam.

The blunt alternative, ``FALAW_FETCH_ARTIFACT_BYTES=0``, also silences the
network — by turning content addressing off, so the suite stops testing the
thing that matters. Prefer the fetcher.

**2. An isolated falaw cache (`_isolated_falaw_cache`).** falaw's manifest
cache, content store and url-index all hang off ``$FALAW_CACHE_DIR``. Without
this the suite writes into the developer's real ``~/.config/falaw/cache`` — and
now that (1) makes the fetches *succeed*, it would durably record synthetic
test bytes under real-looking URLs in that cache. Isolation is a precondition
for the fake transport, not a nicety.

**3. A no-outbound-connection guard (`no_outbound_network`).** (1) fixes the
two known leaks; this stops the next one from going unnoticed for a release.
Every non-loopback DNS lookup or socket connect raises, and — the load-bearing
part — the attempt is *recorded* and fails the test at teardown. Raising alone
is not enough: falaw swallows a failed fetch by design, so a guard that only
raised would leave the suite green while the bug was back. Loopback and
``AF_UNIX`` stay open, so a local Postgres (``tests/test_graph_backend_postgres
.py``) still works.

Its one blind spot is a subprocess: ffmpeg opens its own sockets in its own
process, where this guard has no reach.

A test marked ``live_api`` opts out of the fake transport and the guard, and
keeps the cache isolation. nw has no such test today; the hatch exists so that
adding one does not require re-deriving why the suite is hermetic.
"""

from __future__ import annotations

import socket
import urllib.error
from typing import Iterator, Optional

import pytest


# --- a fake asset transport -------------------------------------------------


def _synthetic_bytes(url: str) -> bytes:
    """Deterministic stand-in bytes for an unregistered URL."""
    return f"nw-test-asset::{url}".encode("utf-8")


class FakeAssets:
    """An in-memory ``url -> bytes`` transport standing in for the network."""

    def __init__(self) -> None:
        self.by_url: dict[str, Optional[bytes]] = {}
        self.fetched: list[str] = []

    @staticmethod
    def synthetic(url: str) -> bytes:
        """The bytes an unregistered ``url`` serves."""
        return _synthetic_bytes(url)

    def serve(self, url: str, data: bytes) -> bytes:
        """Make ``url`` serve exactly ``data``.

        How a test makes two *different* URLs serve the *same* bytes — the case
        content addressing exists for — or aligns falaw's view of an asset with
        bytes the test also writes to disk itself.
        """
        self.by_url[url] = data
        return data

    def fail(self, url: str) -> None:
        """Make ``url`` 404, the way an expired fal asset does."""
        self.by_url[url] = None

    def chunks(self, url: str, *, chunk_size: int = 1 << 16) -> Iterator[bytes]:
        self.fetched.append(url)
        data = self.by_url.get(url, _synthetic_bytes(url))
        if data is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
        for offset in range(0, len(data), chunk_size):
            yield data[offset : offset + chunk_size]


# --- the no-outbound-connection guard ---------------------------------------


class OutboundNetworkBlocked(RuntimeError):
    """Raised in place of an outbound connection attempt in the offline suite."""


_LOCAL_HOSTS = frozenset({"", "localhost", "::1", "::", "0.0.0.0"})


def _host_is_local(host) -> bool:
    """True for loopback / unspecified hosts, which stay reachable."""
    if host is None:
        return True
    text = host.decode("utf-8", "replace") if isinstance(host, bytes) else str(host)
    text = text.strip("[]").lower()
    return (
        text in _LOCAL_HOSTS
        or text.endswith(".localhost")
        or text.startswith("127.")
    )


def _address_is_local(address) -> bool:
    """True for an ``AF_UNIX`` path or a loopback ``(host, port, …)`` tuple."""
    if isinstance(address, (str, bytes)):
        return True  # AF_UNIX socket path — local by construction
    if isinstance(address, tuple) and address:
        return _host_is_local(address[0])
    return False  # an address shape we do not recognize is not demonstrably local


@pytest.fixture(autouse=True)
def _isolated_falaw_cache(tmp_path, monkeypatch):
    """Point every falaw on-disk store at a throwaway directory."""
    monkeypatch.setenv("FALAW_DATA_DIR", str(tmp_path / "falaw-data"))
    monkeypatch.setenv("FALAW_CACHE_DIR", str(tmp_path / "falaw-cache"))
    yield


@pytest.fixture(autouse=True)
def fake_assets(request, monkeypatch):
    """Replace :func:`falaw.content._http_chunks` with an in-memory transport."""
    if request.node.get_closest_marker("live_api") is not None:
        yield None
        return
    assets = FakeAssets()
    monkeypatch.setattr("falaw.content._http_chunks", assets.chunks)
    yield assets


@pytest.fixture(autouse=True)
def no_outbound_network(request, monkeypatch):
    """Block — and *report* — every non-loopback connection attempt.

    Yields the list of blocked attempts, so a test that means to provoke one
    can drain it (``blocked.clear()``) instead of failing at teardown.
    """
    if request.node.get_closest_marker("live_api") is not None:
        yield []
        return

    blocked: list[str] = []
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def refuse(what: str) -> OutboundNetworkBlocked:
        blocked.append(what)
        return OutboundNetworkBlocked(
            f"nw's offline test suite attempted {what}. Stub the transport "
            "instead — `fake_assets` (tests/conftest.py) serves falaw's asset "
            "fetches, and a test needing its own HTTP should monkeypatch the "
            "call it makes."
        )

    def guarded_getaddrinfo(host, port, *args, **kwargs):
        if not _host_is_local(host):
            raise refuse(f"a DNS lookup for {host!r}")
        return real_getaddrinfo(host, port, *args, **kwargs)

    def guarded_connect(self, address, *args, **kwargs):
        if not _address_is_local(address):
            raise refuse(f"a connection to {address!r}")
        return real_connect(self, address, *args, **kwargs)

    def guarded_connect_ex(self, address, *args, **kwargs):
        if not _address_is_local(address):
            raise refuse(f"a connection to {address!r}")
        return real_connect_ex(self, address, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)

    yield blocked

    if blocked:
        # Teardown, not the call site, on purpose: falaw degrades a failed
        # asset fetch to a warning, so a guard that only raised would let the
        # regression back in with the suite still green.
        pytest.fail(
            "Outbound network access attempted by an offline test: "
            + "; ".join(dict.fromkeys(blocked)),
            pytrace=False,
        )
