"""Make this checkout's own source the one under test.

An editable install points `nw` at whichever checkout registered it, so without
this a worktree's tests silently exercise a different tree — green here, green
there, and the change was never run.

It also keeps the suite collectable without the optional extras (nw#96):
``--doctest-modules`` imports every module under ``nw/``, and a feature module
whose extra is absent raises at import by design.
"""

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

#: feature module -> the package its extra installs
_EXTRA_MODULES = {"nw/storyboard.py": "artful"}


def _installed(package: str) -> bool:
    try:
        return importlib.util.find_spec(package) is not None
    except (ImportError, ValueError):  # e.g. blocked via sys.modules[package] = None
        return False


collect_ignore = [
    path for path, package in _EXTRA_MODULES.items() if not _installed(package)
]
