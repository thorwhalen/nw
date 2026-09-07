"""One place that maps nw's ``(use_cache, force)`` onto falaw's ``(use_cache, refresh)``.

nw exposes two cache knobs on ``Transform.execute``: ``use_cache`` ("may this
run touch the cache at all?") and ``force`` (the "regenerate this" affordance).
falaw's executor exposes the same policy as a *read* half and a *write* half
(falaw#49): ``use_cache`` gates both, ``refresh`` suppresses only the read.

The historical translation was ``use_cache=use_cache and not force``, which is
the double-spend nw#72 names: it turned "ignore the cached answer" into "and
throw away the fresh one", so a forced run paid for a result it discarded and
the next consumer re-billed it. ``force`` is a *read* instruction, so it maps
to ``refresh`` and nothing else:

============================  ==========  ===========
nw ``(use_cache, force)``     cache read  cache write
============================  ==========  ===========
``(True, False)`` — default   yes         yes
``(True, True)``              no          yes
``(False, False)``            no          no
============================  ==========  ===========

``(False, True)`` is the fourth corner and has no meaning — there is no key to
write under when the cache is off, which is why falaw raises on it. Before
nw#72 it silently collapsed to "no cache at all"; forwarding it unexamined
would now surface falaw's ``ValueError`` from two frames down, naming keywords
(``refresh``) the nw caller never passed. :func:`resolve_cache_mode` refuses it
here instead, in nw's own vocabulary.
"""

from __future__ import annotations


class CacheModeConflict(ValueError):
    """``use_cache=False`` and ``force=True`` were passed together.

    A ``ValueError`` subclass so callers that already catch ``ValueError``
    (and falaw's own refusal of the same corner) keep working, while a caller
    that wants to distinguish this one specific contradiction can.
    """


def resolve_cache_mode(*, use_cache: bool, force: bool) -> tuple[bool, bool]:
    """Return the ``(use_cache, refresh)`` pair falaw's executor should get.

    Args:
        use_cache: May this run touch the cache at all?
        force: Ignore any cached answer and re-run — while **keeping** what
            the re-run pays for.

    Returns:
        The ``(use_cache, refresh)`` pair to forward to ``execute_plan`` /
        ``execute_plan_isolated``.

    Raises:
        CacheModeConflict: if ``force=True`` is paired with ``use_cache=False``.
    """
    if force and not use_cache:
        raise CacheModeConflict(
            "use_cache=False and force=True are contradictory. force means "
            "'skip the cache read but keep the cache write', so it needs the "
            "cache on; use_cache=False means 'do not touch the cache at all', "
            "so there is no key to write the forced result under. Pass "
            "use_cache=True, force=True to re-run and keep what the re-run "
            "pays for, or use_cache=False, force=False to bypass the cache "
            "entirely (and pay again on the next run)."
        )
    return use_cache, force
