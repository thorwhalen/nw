"""What ``import nw`` costs, and what an optional dependency's absence breaks (nw#96).

nw is a **substrate**, not an aggregator (``misc/docs/What nw is — a substrate,
not an aggregator.md``): its required dependencies are the two halves it joins
(``lacing`` and ``falaw``) plus the small libraries those contracts are written
in. A feature that needs a dependency of its own — the storyboard bridge to
``artful``, the job facade over ``au`` — loads on first touch, never at
``import nw``.

These tests run in a subprocess, because the property under test is the state
of ``sys.modules`` after a *fresh* import, which no in-process test can see once
the suite has imported everything.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _installed(package: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(package) is not None
    except (ImportError, ValueError):
        return False


needs_artful = pytest.mark.skipif(
    not _installed("artful"), reason="needs the nw[storyboard] extra"
)


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )


def _ok(code: str) -> str:
    proc = _run(code)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


# --- import footprint ---------------------------------------------------------


def test_import_nw_does_not_load_feature_dependencies():
    """``import nw`` loads the contract, not the features built on it.

    ``au``'s package import pulls its HTTP surface (fastapi, flask) whenever
    those are installed, which made ``nw.jobs`` the single largest share of
    ``import nw`` — paid by every caller that never enqueues a job.
    """
    out = _ok(
        """
        import sys
        import nw
        heavy = ("artful", "au", "fastapi", "flask", "nw.jobs", "nw.storyboard")
        print(sorted(m for m in heavy if sys.modules.get(m) is not None))
        """
    )
    assert out.strip() == "[]", f"loaded at `import nw`: {out.strip()}"


@needs_artful
def test_feature_modules_load_on_first_touch():
    """Laziness is invisible to callers: every spelling they use still works."""
    out = _ok(
        """
        import sys
        import nw
        assert nw.jobs.enqueue                      # attribute access
        assert "au" in sys.modules
        from nw import storyboard_from_shots        # from-import of a re-export
        assert "artful" in sys.modules
        import nw.storyboard                        # submodule import
        assert nw.open_storyboard is nw.storyboard.open_storyboard
        print("ok")
        """
    )
    assert out.strip() == "ok"


def test_lazy_names_are_discoverable():
    """``dir(nw)`` lists the lazy names, so tab-completion and agents see them."""
    out = _ok(
        """
        import sys
        import nw
        names = set(dir(nw))
        assert {"jobs", "storyboard", "open_storyboard"} <= names, names
        assert sys.modules.get("artful") is None  # listing must not load them
        print("ok")
        """
    )
    assert out.strip() == "ok"


def test_unknown_attribute_still_raises_attribute_error():
    out = _ok(
        """
        import nw
        try:
            nw.no_such_name
        except AttributeError as e:
            print("AttributeError", "no_such_name" in str(e))
        """
    )
    assert out.strip() == "AttributeError True"


# --- an optional dependency that is not installed ------------------------------

_WITHOUT_ARTFUL = """
import sys
sys.modules["artful"] = None   # what `import artful` sees when it is not installed
"""


def test_nw_imports_and_works_without_artful(tmp_path):
    """The storyboard extra is optional: everything else works without it."""
    out = _ok(
        _WITHOUT_ARTFUL
        + f"""
import nw
p = nw.Project.init({str(tmp_path / "proj")!r})
p.set_title("no artful here")
assert p.read_spec().title == "no artful here"
assert nw.list_transforms() is not None
from nw import *   # star-import must not reach for a missing extra
print("ok")
"""
    )
    assert out.strip() == "ok"


def test_importing_the_feature_without_its_extra_names_the_extra():
    """Reaching for the feature fails at the reach, and says how to install it."""
    out = _ok(
        _WITHOUT_ARTFUL
        + """
import nw
from nw._extras import MissingExtra
try:
    import nw.storyboard
except MissingExtra as e:
    print("MissingExtra", "nw[storyboard]" in str(e))
"""
    )
    assert out.strip() == "MissingExtra True"


def test_a_missing_extra_keeps_the_attribute_protocol():
    """`nw.<name>` answers AttributeError, so introspection never crashes.

    `hasattr`, `getattr(..., default)`, `help()` and `inspect.getmembers` treat
    only AttributeError as "not there". The message still names the extra.
    """
    out = _ok(
        _WITHOUT_ARTFUL
        + """
import inspect, pydoc
import nw
assert not hasattr(nw, "open_storyboard")
assert not hasattr(nw, "storyboard")
assert getattr(nw, "save_storyboard", None) is None
inspect.getmembers(nw)
pydoc.render_doc(nw)
try:
    nw.open_storyboard
except AttributeError as e:
    print("AttributeError", "nw[storyboard]" in str(e))
"""
    )
    assert out.strip() == "AttributeError True"


@needs_artful
def test_a_broken_install_is_not_mislabelled_as_a_missing_extra():
    """A dependency *of* the extra failing to import propagates as itself."""
    out = _ok(
        """
import sys
import artful  # loads fine
sys.modules["artful.exports"] = None   # a broken piece of an installed extra
import nw
from nw._extras import MissingExtra
try:
    import nw.storyboard
except MissingExtra:
    print("mislabelled")
except ImportError as e:
    print("ImportError")
"""
    )
    assert out.strip() == "ImportError"


@needs_artful
def test_storyboard_annotations_are_readable_without_artful(tmp_path):
    """A project someone storyboarded stays walkable by a caller without artful.

    ``iter_all_annotations`` walks every store scope, the storyboard's included;
    the panels' body schema is registered by ``artful``, so this pins that the
    walk does not need the extra installed to *read* what the extra wrote.
    """
    root = tmp_path / "proj"
    write = _ok(
        f"""
import nw
from nw.schema import SectionSpec, ShotSpec
p = nw.Project.init({str(root)!r})
p.upsert_section(SectionSpec(id="v", start_s=0.0, end_s=8.0))
p.upsert_shot(ShotSpec(id="s01", start_s=0.0, end_s=8.0, section_id="v",
                       description="Bell tower at moonlight"))
sb, ivs = nw.storyboard_from_shots(p)
nw.save_storyboard(p, sb, panel_intervals=ivs)
print(sum(1 for a in nw.iter_all_annotations(p.root) if a.tier == "storyboard"))
"""
    )
    n_written = int(write.strip())
    assert n_written >= 1

    out = _ok(
        _WITHOUT_ARTFUL
        + f"""
import nw
print(sum(1 for a in nw.iter_all_annotations({str(root)!r}) if a.tier == "storyboard"))
"""
    )
    assert int(out.strip()) == n_written


# --- the dependency policy -----------------------------------------------------


def test_required_dependencies_are_the_substrate():
    """Adding a required dependency is a decision, not a side effect of a feature.

    If this fails, read ``misc/docs/What nw is — a substrate, not an
    aggregator.md``: a dependency is required only if the substrate's contract is
    written in it. A feature's dependency goes in an extra and its module loads
    lazily (see ``_LAZY_SUBMODULES`` in ``nw/__init__.py``).
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # py3.10
        tomllib = pytest.importorskip("tomli")
    import re

    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text())
    names = {
        re.split(r"[\s<>=!~\[;]", d, maxsplit=1)[0].lower()
        for d in pyproject["project"]["dependencies"]
    }
    assert names == {"pydantic", "lacing", "falaw", "dol", "xdol", "au"}
    assert any(
        d.startswith("artful")
        for d in pyproject["project"]["optional-dependencies"]["storyboard"]
    )
