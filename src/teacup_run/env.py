"""Reading `.env`, because documenting a file nothing reads is a trap.

Explicit, not magic: nothing loads `.env` on import. Call `load_env()` yourself,
and variables already exported win over the file.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["load_env"]

# Values a copied-but-unedited .env still carries. Treating them as real keys
# produces a 401 halfway through a run instead of a message up front.
PLACEHOLDERS = frozenset({"sk-...", "your-key-here", "changeme", "todo", "xxx"})


def load_env(path: str | Path | None = None, *, override: bool = False) -> Path | None:
    """Load `KEY=value` lines from a `.env`. Returns the file used, or None.

    Searches the given path, then the working directory and its parents.
    """
    for candidate in _candidates(path):
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if not value or value.lower() in PLACEHOLDERS:
                continue
            if override or key not in os.environ:
                os.environ[key] = value
        return candidate
    return None


def _candidates(path: str | Path | None) -> list[Path]:
    if path is not None:
        return [Path(path)]
    cwd = Path.cwd().resolve()
    return [d / ".env" for d in (cwd, *cwd.parents)]
