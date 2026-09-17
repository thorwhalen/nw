"""Make this checkout's own source the one under test.

An editable install points `nw` at whichever checkout registered it, so without
this a worktree's tests silently exercise a different tree — green here, green
there, and the change was never run.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
