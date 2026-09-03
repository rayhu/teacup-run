"""Cross-platform subprocess sandbox: launch, bound, and tear down an external
agent process.

This is not filesystem isolation. The launched command typically needs its own
real project directory to run in — `uv run`, a package's own config file, its
`skills/` — so this module never assumes the child's cwd is disposable; callers
that need an absolute-path invocation (external_cli.py does) arrange that
themselves. What this actually bounds:

- **Environment.** Only `env_allowlist` plus a minimal base (PATH, HOME and the
  handful of Windows variables an interpreter needs to start) reaches the child —
  no ambient credential leakage from the caller's own shell.
- **Lifetime.** A wall-clock `timeout`, enforced with a full process-tree kill, not
  just the top process — `uv run` spawns a child, and a plain `Popen.kill()` would
  leave that child running past the deadline it was meant to enforce.
- **Resources**, best-effort, POSIX only: an address-space cap via
  `resource.setrlimit`. Windows has no stdlib equivalent, and `SandboxResult`
  says so explicitly (`limits_applied`) rather than silently claiming parity.

Deliberately out of scope for this version: network egress control. That needs a
container or an OS firewall rule, and this backend is aimed at "run a subprocess
without requiring Docker" — see docs/backends.md for the tradeoff.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
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
    env_allowlist: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> SandboxResult:
    """Launch `argv` as a subprocess, with a scoped env and a hard wall-clock ceiling.

    The subprocess's own cwd is a scratch directory this call owns and removes
    afterwards — it exists only because some process needs *a* cwd, not because
    it is meant to hold anything. Every path the child needs (a config file, a
    project root) must be absolute in `argv`.
    """
    started = time.monotonic()
    env = _child_env(env_allowlist or {})

    popen_kwargs: dict = {}
    if _POSIX:
        popen_kwargs["start_new_session"] = True  # own process group, for killpg
        popen_kwargs["preexec_fn"] = _preexec_limits
    else:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    with tempfile.TemporaryDirectory(prefix="teacup-run-sandbox-") as scratch:
        proc = subprocess.Popen(
            list(argv),
            cwd=scratch,
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
    module top, where it would break the import on Windows entirely."""
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (_MEMORY_LIMIT_BYTES, _MEMORY_LIMIT_BYTES))


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
