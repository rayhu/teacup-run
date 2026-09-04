"""Cross-platform subprocess sandbox: launch, bound, and tear down an external
agent process.

This is not filesystem isolation. The caller passes `cwd` explicitly — usually
the target project's own root, since `uv run --project <dir>` does **not**
change the subprocess's working directory (verified empirically: relative
paths and any cwd-based defaulting inside the launched program resolve
against wherever the parent process was, not `--project`'s target). An
earlier version of this module gave every launch a throwaway scratch
directory instead, which silently broke anything in the launched program that
defaults relative to its own cwd — teacup-agent's `read_file`, its `./mcp.json`
and `./skills` discovery, all of it. `cwd` is required now so that mistake
can't happen quietly again. What this module actually bounds:

- **Environment.** Only `env_allowlist` plus a minimal base (PATH, HOME and the
  handful of Windows variables an interpreter needs to start) reaches the child —
  no ambient credential leakage from the caller's own shell.
- **Lifetime.** A wall-clock `timeout`, enforced with a full process-tree kill, not
  just the top process — `uv run` spawns a child, and a plain `Popen.kill()` would
  leave that child running past the deadline it was meant to enforce.
- **Resources**, best-effort, POSIX only: an address-space cap via
  `resource.setrlimit`. Windows has no stdlib equivalent, and `SandboxResult`
  says so explicitly (`limits_applied`) rather than silently claiming parity.
  "Best-effort" is not decorative: confirmed live on macOS, `setrlimit(RLIMIT_AS,
  ...)` can fail outright — a typical Python process's own virtual address space
  is already well past a 1 GiB cap before this even runs (macOS accounts for it
  very differently than Linux). `_preexec_limits` swallows that failure rather
  than letting it kill the whole launch; an earlier version did not, and a
  `preexec_fn` exception surfaces to the caller as an opaque
  `subprocess.SubprocessError: Exception occurred in preexec_fn.` with no
  indication a resource limit — not the launch itself — was the actual problem.

Deliberately out of scope for this version: network egress control. That needs a
container or an OS firewall rule, and this backend is aimed at "run a subprocess
without requiring Docker" — see docs/backends.md for the tradeoff.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

__all__ = ["SandboxResult", "run_sandboxed"]

_POSIX = os.name == "posix"

# Just enough for `uv` and the interpreter it launches to find themselves and
# write temp files; env_allowlist supplies everything the launched program
# actually needs (an API key, say).
_BASE_ENV_NAMES = (
    "PATH",
    "HOME",
    "USERPROFILE",  # Windows equivalent of HOME
    "SYSTEMROOT",  # Windows: required by the CRT/socket layer
    "APPDATA",
    "LOCALAPPDATA",
    "TEMP",
    "TMP",
)

# 1 GiB address space: generous enough for `uv`/Python startup, tight enough to
# stop a runaway child from taking the host down. A per-package override belongs
# in the manifest eventually; this is a backstop, not a tuned budget.
_MEMORY_LIMIT_BYTES = 1 << 30


@dataclass
class SandboxResult:
    stdout: str
    stderr: str
    returncode: int | None
    timed_out: bool
    elapsed_s: float
    limits_applied: bool  # False on Windows: no stdlib memory/CPU rlimit there


def run_sandboxed(
    argv: Sequence[str],
    *,
    cwd: str | Path,
    env_allowlist: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> SandboxResult:
    """Launch `argv` as a subprocess, with a scoped env and a hard wall-clock ceiling.

    `cwd` is required and not defaulted to a scratch directory — see the module
    docstring for why a throwaway cwd silently breaks a launched program's own
    cwd-relative defaults. Pass the target project's root.
    """
    started = time.monotonic()
    env = _child_env(env_allowlist or {})

    popen_kwargs: dict = {}
    if _POSIX:
        popen_kwargs["start_new_session"] = True  # own process group, for killpg
        popen_kwargs["preexec_fn"] = _preexec_limits
    else:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        list(argv),
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **popen_kwargs,
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        stdout, stderr = proc.communicate()  # drain what's left; don't hang here too

    return SandboxResult(
        stdout=stdout,
        stderr=stderr,
        returncode=proc.returncode,
        timed_out=timed_out,
        elapsed_s=time.monotonic() - started,
        limits_applied=_POSIX,
    )


def _child_env(env_allowlist: Mapping[str, str]) -> dict[str, str]:
    base = {name: os.environ[name] for name in _BASE_ENV_NAMES if name in os.environ}
    base.update(env_allowlist)
    return base


def _preexec_limits() -> None:
    """POSIX-only, wired in as `preexec_fn` — never called on Windows, so the
    import of a POSIX-only stdlib module is deferred to here rather than the
    module top, where it would break the import on Windows entirely.

    Must never raise: this runs in the forked child, between fork() and exec(),
    and subprocess.Popen has no tolerance for a preexec_fn failure — an
    exception here aborts the entire launch with an opaque
    `SubprocessError: Exception occurred in preexec_fn.`, not a "your program
    ran, just unbounded" fallback. Confirmed live on macOS: `setrlimit` itself
    can raise here, since a typical Python process's own virtual address space
    already exceeds a 1 GiB cap by the time this runs (macOS's VA accounting
    differs sharply from Linux's). Swallowing that failure is what makes
    "best-effort" in the module docstring actually true, instead of a resource
    cap silently becoming a hard requirement to launch anything at all.
    """
    import resource

    try:
        resource.setrlimit(resource.RLIMIT_AS, (_MEMORY_LIMIT_BYTES, _MEMORY_LIMIT_BYTES))
    except (ValueError, OSError):
        pass


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the whole process tree, not just argv[0]. `uv run` spawns a child
    process; a plain proc.kill() only kills the parent and leaves that child
    running past the timeout it was meant to enforce."""
    if _POSIX:
        import signal

        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # already gone
    else:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
