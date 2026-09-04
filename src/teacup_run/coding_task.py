"""`run_coding_task` — drive a coding agent against a real repo without ever
touching its primary checkout.

Every task gets its own disposable `git worktree` + branch off `base_branch`
(load-bearing decision from this project's own design notes: "a bad run costs
'delete a worktree,' never 'recover a working tree someone else is using'").
The worktree becomes the sandboxed process's `cwd` via `external_cli.run_external`'s
`target_repo` — decoupling "where teacup-agent's own code/deps live"
(`project_root`, for `uv run --project`) from "what it operates on" (the
worktree), so the same bridge package can drive a task against any target
repo, not only wherever `project_root` happens to point.

`--coding-tools --approve hooks` are always added to the launched CLI's argv
(`extra_flags`) — a coding task with neither would only ever produce an
answer, never a change. `--hooks` is deliberately **not** passed explicitly:
teacup-agent's own CLI auto-discovers `./hooks.py` relative to its cwd, which
is now the worktree — so if (and only if) the target repo has committed a
`hooks.py` at its root, `git worktree add` checks it out into the worktree
along with everything else, and it is picked up for free. A target repo with
no `hooks.py` gets exactly the safe default: every gated call denied without
a TTY, per this project's own "deny by default when nobody is watching" (the
same discipline teacup-agent's `AGENTS.md` states) — a coding task simply
producing no side effects is the correct outcome there, not a bug to route
around.

This module never commits on the caller's behalf, never pushes, and never
opens a pull request. It stops at "a reviewable local branch, with a diff
and a test result attached" — a human decides what happens to it next, the
same discipline this whole engagement has followed by hand every round.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from .loop import Result
from .manifest import AgentSpec
from .sandbox import run_sandboxed

__all__ = ["CodingTaskError", "CodingTaskResult", "run_coding_task"]


class CodingTaskError(RuntimeError):
    """The worktree/branch setup failed — before teacup-agent ever ran."""


@dataclass
class CodingTaskResult:
    """What a coding task produced: the agent's own `Result`, plus the two
    signals that actually decide whether it succeeded — what changed, and
    whether the target repo's own tests still pass."""

    result: Result
    branch: str
    base_branch: str
    worktree_path: Path
    files_changed: tuple[str, ...]
    diff_stat: str
    commits_made: int
    tests_passed: bool | None  # None: run_tests=False, or no test_command given
    test_output: str | None

    def __str__(self) -> str:
        return self.result.answer


def run_coding_task(
    spec: AgentSpec,
    task: str,
    *,
    target_repo: Path,
    base_branch: str = "main",
    budget: float | None = None,
    live: bool = True,
    timeout: float | None = None,
    run_tests: bool = True,
    test_command: str | None = None,
    test_timeout: float = 300.0,
) -> CodingTaskResult:
    """Run `task` against a disposable worktree of `target_repo`, on a new
    branch off `base_branch`, and report what changed.

    `run_tests`/`test_command`: there is no reliable, language-agnostic way to
    guess "the target repo's own test command", so `run_tests=True` with no
    `test_command` is a deliberate no-op (`tests_passed=None`) rather than a
    guess dressed up as a result — pass the command explicitly to actually run
    it (e.g. `test_command="uv run pytest"`). `test_command` is split with
    `shlex.split` and run **without a shell** (same reason `sandbox.py` never
    passes `shell=True`) — a single command only, no `&&`/`;`/pipes/env-var
    prefixes. Wrap it yourself (`test_command='bash -c "make check && make
    test"'`) if you need shell features.
    """
    from .external_cli import run_external  # local import: avoid a cycle at module load

    branch = _branch_name(task)
    scratch = Path(tempfile.mkdtemp(prefix="teacup-run-worktree-"))
    worktree_path = scratch / "worktree"
    try:
        _create_worktree(target_repo, worktree_path, branch=branch, base_branch=base_branch)
    except Exception:
        # _create_worktree can fail before anything exists under `scratch` (a bad
        # target_repo/base_branch) — mkdtemp() itself isn't a context manager, so
        # nothing else removes it, and every failed call would otherwise leak one
        # empty directory forever.
        shutil.rmtree(scratch, ignore_errors=True)
        raise

    result = run_external(
        spec,
        task,
        budget=budget,
        live=live,
        timeout=timeout,
        target_repo=worktree_path,
        extra_flags=("--coding-tools", "--approve", "hooks"),
    )

    files_changed, diff_stat, commits_made = _collect_diff(worktree_path, base_branch)

    tests_passed: bool | None = None
    test_output: str | None = None
    if run_tests and test_command:
        tests_passed, test_output = _run_tests(worktree_path, test_command, test_timeout)

    return CodingTaskResult(
        result=result,
        branch=branch,
        base_branch=base_branch,
        worktree_path=worktree_path,
        files_changed=files_changed,
        diff_stat=diff_stat,
        commits_made=commits_made,
        tests_passed=tests_passed,
        test_output=test_output,
    )


def _branch_name(task: str) -> str:
    # A short random suffix, not a timestamp: two calls in the same wall-clock
    # second (a batch of small tasks, or two tests in one process) would otherwise
    # collide and make `git worktree add -b` fail on the second one.
    slug = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")[:40] or "task"
    return f"teacup-run/{slug}-{uuid.uuid4().hex[:8]}"


def _create_worktree(target_repo: Path, worktree_path: Path, *, branch: str, base_branch: str) -> None:
    _git(["rev-parse", "--git-dir"], cwd=target_repo, error=f"{target_repo} is not a git repository")
    _git(
        ["worktree", "add", str(worktree_path), "-b", branch, base_branch],
        cwd=target_repo,
        error=(
            f"could not create a worktree for branch {branch!r} off {base_branch!r} "
            f"in {target_repo}"
        ),
    )


def _collect_diff(worktree_path: Path, base_branch: str) -> tuple[tuple[str, ...], str, int]:
    """Union of committed (on this branch, since it split from base_branch) and
    uncommitted changes — a coding task's tools don't commit anything themselves
    (list_files/edit_file/write_file/run_command, teacup-agent's coding_tools.py),
    but the model can `git commit` via run_command if a project's hooks.py allows
    it, so both cases have to be reported, not just one."""
    status = _git(["status", "--porcelain"], cwd=worktree_path, error="git status failed")
    # line[3:] drops the 2-char status code + space every porcelain line starts with
    # (`M `, `??`, ...). A rename line (`R  old -> new`) isn't split into two paths —
    # acceptable for a first cut, since files_changed is a review aid, not a machine
    # contract; `diff_stat` below carries the real, unambiguous git output either way.
    uncommitted = [line[3:] for line in status.splitlines() if line.strip()]

    # base_branch was already validated as a real ref when the worktree was created
    # (it's what the branch was cut from), so this can only come back empty, never fail.
    log = _git(["log", f"{base_branch}..HEAD", "--oneline"], cwd=worktree_path, error="git log failed")
    commits_made = len([line for line in log.splitlines() if line.strip()])

    committed = []
    if commits_made:
        diff_names = _git(
            ["diff", "--name-only", f"{base_branch}..HEAD"], cwd=worktree_path, error="git diff failed"
        )
        committed = [line for line in diff_names.splitlines() if line.strip()]

    files_changed = tuple(sorted(set(uncommitted) | set(committed)))

    # git diff HEAD --stat only covers already-tracked files — a brand-new file the
    # model wrote (untracked, never `git add`ed) shows up in `status --porcelain` as
    # `?? path` but not here at all, so it has to be reported separately or it goes
    # missing from the summary entirely despite being a real, uncommitted change.
    tracked_stat = _git(["diff", "HEAD", "--stat"], cwd=worktree_path, error="git diff --stat failed").strip()
    untracked = [line[3:] for line in status.splitlines() if line.startswith("??")]

    parts = []
    if commits_made:
        parts.append(f"{commits_made} commit(s) on {base_branch}..HEAD")
    if tracked_stat:
        parts.append(tracked_stat)
    if untracked:
        parts.append("new, untracked: " + ", ".join(untracked))
    diff_stat = "\n".join(parts) or "(no changes)"

    return files_changed, diff_stat, commits_made


def _run_tests(worktree_path: Path, test_command: str, test_timeout: float) -> tuple[bool, str]:
    """Never raises: a bad test_command (typo'd binary, wrong PATH assumption) is a
    plausible, caller-supplied value, not a coding_task.py bug — the coding agent
    has already run by the time this executes, so letting subprocess.Popen's
    FileNotFoundError/OSError propagate would discard a real, already-produced
    CodingTaskResult (branch, worktree, diff, answer) over an unrelated test-runner
    mistake. Reported as a failed test run instead, same as a real test failure."""
    try:
        sandbox_result = run_sandboxed(shlex.split(test_command), cwd=worktree_path, timeout=test_timeout)
    except OSError as exc:
        return False, f"ERROR: could not run test_command {test_command!r}: {exc}"
    if sandbox_result.timed_out:
        return False, f"ERROR: test command timed out after {test_timeout:g}s: {test_command!r}"
    output = sandbox_result.stdout
    if sandbox_result.stderr:
        output += f"\n[stderr]\n{sandbox_result.stderr}"
    return sandbox_result.returncode == 0, output


def _git(args: list[str], *, cwd: Path, error: str) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise CodingTaskError("git is not installed") from exc
    except subprocess.CalledProcessError as exc:
        raise CodingTaskError(f"{error}: {exc.stderr.strip()}") from exc
    return result.stdout
