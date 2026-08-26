"""Render strategies — pluggable, plan-producing, **shot-only**.

**Scope.** A Strategy is the plug-in point of the *shot* render unit
(:mod:`nw.workflow`), not of the general engine. It is typed to a
:class:`nw.workflow.ShotPreparation` in and an ``output.mp4`` out, so it cannot
express a non-video render. A new render kind — audio weave, slideshow,
anything — registers a :class:`nw.transforms.Transform` instead; that registry
is the render-kind-agnostic one. See nw#9.

A *strategy* knows how to turn a :class:`nw.workflow.ShotPreparation` into:

1. A :class:`falaw.Plan` (pure data — :meth:`Strategy.plan`).
2. A final ``output.mp4`` path, given the Plan's executed Artifacts
   (:meth:`Strategy.materialize`).

Strategies are registered with an :class:`xdol.Registry` keyed by name. Apps
can register their own strategies (e.g. ``composite_lipsync``, ``slideshow``,
``panel``) without modifying nw.

Built-in strategies (registered at import):

- ``lipsync``            — character anchor + audio → talking video (omnihuman)
- ``image_to_video``     — env / fresh storyboard still → animated clip
- ``text_to_video``      — prompt-only short clip
- ``still``              — image looped over audio (no video gen)
- ``composite_lipsync``  — character + environment + audio → composite-then-talk
                           ("Thor in a bell tower playing piano, lipsynced")
"""

from __future__ import annotations

from typing import Protocol

from xdol import Registry

from falaw import Plan
from lacing import Artifact


class Strategy(Protocol):
    """Render-strategy contract."""

    name: str

    def plan(
        self,
        prep,  # nw.workflow.ShotPreparation — avoid import cycle
        *,
        quality: str = "balanced",
        model_overrides: dict[str, str] | None = None,
    ) -> Plan:
        """Build a :class:`falaw.Plan` for the prepared shot. No fal calls."""
        ...

    def materialize(
        self,
        prep,  # nw.workflow.ShotPreparation
        plan: Plan,
        artifacts: list[Artifact],
    ) -> "Path":  # noqa: F821
        """Turn executed Artifacts into ``shot_dir/output.mp4``. May download +
        run ffmpeg, but no fal calls."""
        ...


# The strategy registry.  ``on_conflict="error"`` so a misconfigured plugin
# can't silently shadow a built-in.
strategies: Registry = Registry(name="nw.renderers", on_conflict="error")
"""Public registry — apps add strategies via ``strategies.register("name", impl)``."""


def get_strategy(name: str) -> Strategy:
    """Look up a strategy by name; raises if unknown."""
    if name not in strategies:
        known = sorted(strategies.keys())
        raise KeyError(
            f"No render strategy {name!r}; registered: {known}. "
            "Apps can register custom strategies via "
            "`nw.renderers.strategies.register(name, impl)`."
        )
    return strategies[name]


def list_strategies() -> list[str]:
    """Return all registered strategy names (sorted)."""
    return sorted(strategies.keys())


def register_strategy(name: str, impl: Strategy) -> Strategy:
    """Register a strategy. Returns ``impl`` so it can be used inline."""
    return strategies.register(name, impl)


# --- import the built-ins so they self-register on package import -----------

from . import lipsync as _lipsync  # noqa: E402,F401
from . import image_to_video as _image_to_video  # noqa: E402,F401
from . import text_to_video as _text_to_video  # noqa: E402,F401
from . import still as _still  # noqa: E402,F401
from . import composite_lipsync as _composite_lipsync  # noqa: E402,F401
