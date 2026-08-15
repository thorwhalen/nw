"""Shared fixtures making nw's offline test suite genuinely offline.

The fixtures come from :mod:`falaw.testing` (falaw#27): falaw owns the asset
transport, so falaw owns the fake — nw's previous hand-rolled copy of it
(nw#38) is retired. Three autouse fixtures, and why each exists:

**1. ``fake_assets``** — serves falaw's asset fetches from memory, installed
through the public :func:`falaw.content.using_url_fetcher` seam so it covers
every falaw entry point at once, including the ones nw reaches *through* its
own public API. Built via the factory rather than re-exported directly for
one nw-specific choice: the synthetic bytes are labelled ``nw-test-asset``,
so a hexdump in a failing assertion names the suite that invented them.

**2. ``isolated_falaw_cache``** — points falaw's manifest cache, content
store and url-index at a throwaway directory. A precondition for the fake,
not a nicety: once fetches *succeed*, an un-isolated run durably records
synthetic test bytes under real-looking URLs in the developer's real cache.

**3. ``no_outbound_network``** — refuses **and records** every non-loopback
DNS lookup or connect, failing the test at teardown. Recording is the
load-bearing half: falaw degrades a failed fetch to a warning by design, so
a guard that only raised would leave the suite green with a regression back
in. (The shared guard's refusal is a ``BaseException`` — an upgrade over the
retired copy's ``RuntimeError``, which falaw's own fetch funnel swallowed.)
Loopback and non-IP sockets stay open, so a local Postgres
(``tests/test_graph_backend_postgres.py``) still works; a subprocess
(ffmpeg) remains the one documented blind spot.

A test marked ``live_api`` opts out of the fake transport and the guard, and
keeps the cache isolation. The full rationale — including why
``FALAW_FETCH_ARTIFACT_BYTES=0`` is the wrong hermeticity knob — lives with
the implementation, in :mod:`falaw.testing`'s docstrings.
"""

from __future__ import annotations

from falaw.testing import (  # noqa: F401  — pytest picks the fixtures up here
    isolated_falaw_cache,
    make_fake_assets_fixture,
    no_outbound_network,
)

fake_assets = make_fake_assets_fixture(synthetic_prefix="nw-test-asset")
