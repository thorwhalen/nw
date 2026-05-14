"""Built-in Transform adapters — imported for their registration side effects.

Importing this package registers the adapter Transforms with
:data:`nw.transforms.transforms`. Currently:

- :mod:`.render_strategy` — wraps each of the 5 existing
  ``nw.renderers`` strategies as a ``shot_to_render_result.fal.*`` Transform,
  proving the :class:`~nw.transforms.Transform` abstraction against working
  code without duplicating any rendering logic.
"""

from __future__ import annotations

from . import render_strategy as render_strategy  # noqa: F401
