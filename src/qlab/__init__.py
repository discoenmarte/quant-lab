"""quant-lab: an evidence-first quant research lab."""

import functools
import subprocess
from pathlib import Path

__version__ = "0.1.0"


@functools.cache
def code_version() -> str:
    """Package version plus the git commit, so every report says which code produced it.

    Returns:
        ``"0.1.0+834907f"``, with ``-dirty`` when there are uncommitted changes, or the bare package version
        outside a git checkout.
    """
    root = Path(__file__).resolve().parents[2]
    try:
        sha = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True,
                             text=True, timeout=5, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=5, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return __version__
    return f"{__version__}+{sha}{'-dirty' if dirty else ''}"

