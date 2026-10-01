"""Optional extras: how a feature says the dependency it needs is not installed (nw#96).

A feature whose dependency the substrate's contract does not need lives behind an
extra (``nw[storyboard]`` → ``artful``). Its module raises :class:`MissingExtra`
at import when the dependency is absent, so the failure happens at the reach and
names the command that fixes it.

>>> try:
...     raise missing_extra("nw.storyboard", extra="storyboard", package="artful")
... except ImportError as e:
...     print(e)
nw.storyboard needs the optional `artful` package. Install it with:  pip install 'nw[storyboard]'
"""

from __future__ import annotations


class MissingExtra(ImportError):
    """An optional feature's dependency is not installed."""


def missing_extra(feature: str, *, extra: str, package: str) -> MissingExtra:
    """The :class:`MissingExtra` for ``feature``, naming its extra and package."""
    return MissingExtra(
        f"{feature} needs the optional `{package}` package. "
        f"Install it with:  pip install 'nw[{extra}]'",
        name=package,
    )


def is_missing(exc: ModuleNotFoundError, package: str) -> bool:
    """Whether ``exc`` is ``package`` itself being absent, not a broken install.

    Only the top-level package counts. A dependency *of* the extra, or a
    submodule of an installed one, failing to import is a broken install, and is
    re-raised as-is rather than mislabelled as "not installed".

    >>> is_missing(ModuleNotFoundError(name="artful"), "artful")
    True
    >>> is_missing(ModuleNotFoundError(name="artful.exports"), "artful")
    False
    """
    return exc.name == package
