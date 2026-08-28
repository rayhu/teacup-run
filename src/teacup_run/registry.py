"""The hub: a directory, and optionally git.

There is no server. An agent lives in a directory; publishing copies it into the
hub directory and, when that directory is a git repository, commits it. Pulling
resolves a reference to a local directory, cloning first if the reference is a
git URL.

That is enough for the flywheel the README describes — pull, fork, extend,
publish — and it means lineage is just git history.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

__all__ = ["RegistryError", "clone", "hub_path", "publish", "resolve"]

MANIFEST_NAME = "agent.yaml"

# The agent directory is the artifact, so publishing copies all of it — except
# these. Secrets must never travel; the rest is local dev residue, and since an
# agent directory is often a repository root, `.git` in particular would land the
# hub with an embedded clone that its own `git add` cannot represent.
NOT_PUBLISHED = (
    ".env",
    ".git",
    ".venv",
    "__pycache__",
    "*.pyc",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "*.egg-info",
    "node_modules",
    "dist",
    "build",
)


class RegistryError(RuntimeError):
    """A reference could not be resolved, or a package could not be published."""


def hub_path() -> Path:
    """Where pulled and published agents live. Override with `TEACUP_HOME`."""
    return Path(os.environ.get("TEACUP_HOME", Path.home() / ".teacup")) / "agents"


def _is_git_url(ref: str) -> bool:
    return ref.startswith(("git@", "http://", "https://", "ssh://", "file://")) or ref.endswith(".git")


def _package_root(path: Path) -> Path:
    """The directory the manifest lives in — that directory is the agent.

    Usually `path` itself. The fallback is for pointing at a repository that
    keeps its agent in a subdirectory: one `agent.yaml` one level down is
    unambiguous, several are not.
    """
    manifest = path / MANIFEST_NAME
    if manifest.is_file():
        return manifest.resolve().parent
    candidates = sorted(p.parent for p in path.glob(f"*/{MANIFEST_NAME}"))
    if len(candidates) == 1:
        return candidates[0].resolve()
    if not candidates:
        raise RegistryError(f"no {MANIFEST_NAME} in {path} or its immediate subdirectories")
    raise RegistryError(
        f"{path} contains several agents: {', '.join(c.name for c in candidates)}. "
        "Point at one of them."
    )


def resolve(ref: str, *, hub: Path | None = None) -> Path:
    """Turn a reference into a local package directory.

    Order: a local path → the hub cache → a git clone.
    """
    hub = hub or hub_path()

    local = Path(ref).expanduser()
    if local.exists():
        return _package_root(local)

    if not _is_git_url(ref):
        cached = hub / ref
        if cached.exists():
            return _package_root(cached)
        raise RegistryError(
            f"{ref!r} is not a local path and is not in the hub ({hub}). "
            f"Pull it first, or pass a git URL."
        )

    return _package_root(clone(ref, hub=hub))


def clone(url: str, *, hub: Path | None = None, name: str | None = None) -> Path:
    """Clone a git URL into the hub, or update it if it is already there."""
    hub = hub or hub_path()
    name = name or url.rstrip("/").split("/")[-1].removesuffix(".git")
    target = hub / name
    if target.exists():
        _git(["pull", "--ff-only"], cwd=target, tolerate_failure=True)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    _git(["clone", "--depth", "1", url, str(target)], cwd=hub.parent)
    return target


def publish(source: Path, ref: str, *, hub: Path | None = None, message: str | None = None) -> Path:
    """Copy a package into the hub under `ref`, committing when the hub is a git repo."""
    hub = hub or hub_path()
    source = _package_root(Path(source))
    target = hub / ref
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(*NOT_PUBLISHED))

    if (hub / ".git").is_dir() or _git(["rev-parse", "--git-dir"], cwd=hub, tolerate_failure=True):
        _git(["add", "-A", str(target)], cwd=hub, tolerate_failure=True)
        _git(
            ["commit", "-m", message or f"Publish {ref}"],
            cwd=hub,
            tolerate_failure=True,
        )
    return target


def _git(args: list[str], *, cwd: Path, tolerate_failure: bool = False) -> bool:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=not tolerate_failure,
        )
    except FileNotFoundError as exc:
        if tolerate_failure:
            return False
        raise RegistryError("git is not installed, so this reference cannot be cloned") from exc
    except subprocess.CalledProcessError as exc:
        raise RegistryError(f"git {' '.join(args)} failed: {exc.stderr.strip()}") from exc
    return result.returncode == 0
