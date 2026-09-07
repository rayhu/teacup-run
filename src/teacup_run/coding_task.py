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

# Where the launched agent's own run artifacts go, relative to the worktree root.
# Dotted and namespaced so it cannot collide with content a target repo tracks.
ARTIFACTS_DIRNAME = ".teacup-run"


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
    # The run's own trajectory (state.json + externalized tool results), or None when
    # the agent wrote nothing there — a crash before its first step, say. Populating it
    # unconditionally would make the `| None` decorative and promise a trajectory that
    # may not exist. Note it lives inside the worktree, so `git worktree remove` takes
    # it with it.
    agent_artifacts_path: Path | None = None

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
    model: str | None = None,
    max_steps: int | None = None,
    timeout: float | None = None,
    run_tests: bool = True,
    test_command: str | None = None,
    test_timeout: float = 300.0,
) -> CodingTaskResult:
    """Run `task` against a disposable worktree of `target_repo`, on a new
    branch off `base_branch`, and report what changed.

    `model`: passed straight through as teacup-agent's own `--model` (only
    meaningful with `live=True` — teacup-agent's offline demo ignores it);
    `None` leaves teacup-agent's own default (`gpt-5`) in place. A coding
    task is exactly the kind of run where the caller cares which model does
    the editing — unlike `run_external`'s plain pass-through use, this isn't
    left to whatever the target checkout happens to default to.

    `max_steps`: passed straight through as teacup-agent's own `--max-steps`;
    `None` leaves its own default (8) in place. Confirmed live: a task that
    reads one large file for context before editing (teacup-agent's own
    docs/roadmap.md, at 1800+ lines) can burn most of an 8-step budget just
    locating the relevant paragraph, and hit the forced wrap-up before making
    a single edit — this is a coding task, not a quick lookup, and the
    default step count was never tuned for "read context, edit N files, add
    a test, run the suite" in one run.

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
    # .resolve(): on macOS mkdtemp returns /var/... while the child process reports
    # cwd as /private/var/..., so teacup-agent's `run_dir.relative_to(cwd)` raises and
    # it falls back to an absolute pointer. The read-back still worked, but only
    # because its path guard re-resolves and the symlink happens to normalise it —
    # an accident of this platform, not the mechanism. Resolve here so the run dir is
    # genuinely under the cwd the child sees.
    scratch = Path(tempfile.mkdtemp(prefix="teacup-run-worktree-")).resolve()
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

    extra_flags = ("--coding-tools", "--approve", "hooks")
    if model:
        extra_flags += ("--model", model)
    if max_steps is not None:
        extra_flags += ("--max-steps", str(max_steps))

    # Inside the worktree, deliberately. teacup-agent externalizes any tool result
    # over 2000 chars into its --run-dir and leaves the model an excerpt plus the
    # path to read back; its own read_file refuses paths outside its cwd, so that
    # path is only usable when the run dir sits under the cwd it is given.
    #
    # Why it must be reachable at all: before rayhu/teacup-agent#16, a 12147-char file
    # reached the model as 864 chars plus an absolute path its own read_file refuses,
    # and it spent six edit_file calls guessing at code it had never been shown.
    #
    # #16 has since landed on teacup-agent's main — which is what `base_branch` above
    # points at — so an unreachable run dir no longer truncates anything: the agent
    # keeps the whole result inline instead. That makes this an optimisation rather
    # than a correctness fix, and a real one: the excerpt-plus-path bargain is what
    # keeps large reads out of the context window, and the inline fallback sends them
    # unshrunk. Keep it reachable.
    #
    # Two things make putting it there safe, and neither may be dropped:
    #
    # The name. Not `runs/`: a target repo is free to already track a directory by
    # that name (fixtures, data), and writing state.json into it would surface as a
    # modified *tracked* file on the branch a human is asked to review.
    #
    # The filter. _collect_diff drops this prefix explicitly rather than trusting the
    # target's .gitignore. Relying on the ignore rules was the earlier version of this
    # code and it was wrong: it held only for teacup-agent, whose .gitignore happens to
    # carry `runs/`, and this repo's own source is a documented target (docs/backends.md)
    # whose .gitignore does not — so `files_changed` would have gained a phantom entry
    # on exactly the target the docs suggest trying first.
    #
    # What the filter does NOT do is stop a model from committing the trajectory itself:
    # state.json holds the system prompt and every tool result, and a target whose
    # hooks.py approves `git add` and `git commit` as separate calls can put it on the
    # branch. Nothing here pushes, so this stops at a local branch a human reads — but
    # it is a real consequence of moving the run dir inside the repo, so it is written
    # down rather than left to be discovered.
    agent_run_dir = worktree_path / ARTIFACTS_DIRNAME

    try:
        result = run_external(
            spec,
            task,
            budget=budget,
            live=live,
            timeout=timeout,
            target_repo=worktree_path,
            extra_flags=extra_flags,
            run_dir=agent_run_dir,
        )
    except OSError as exc:
        # run_external creates the run dir before launching anything, and that can fail
        # on inputs a target repo really has: a plain *file* named .teacup-run at its
        # root, or a read-only checkout. By this point _create_worktree has already
        # registered a worktree in the caller's real repository, and the cleanup above
        # only covers failures during creation — so without this the caller is left with
        # a stale `git worktree` entry and a scratch directory nobody removes, from what
        # reads to them as an unrelated OSError.
        _remove_worktree(target_repo, worktree_path)
        shutil.rmtree(scratch, ignore_errors=True)
        raise CodingTaskError(
            f"could not prepare the agent's run directory at {agent_run_dir}: {exc}"
        ) from exc

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
        agent_artifacts_path=agent_run_dir if _has_files(agent_run_dir) else None,
    )


def _has_files(directory: Path) -> bool:
    """Whether anything was actually written. `run_external` creates the directory
    before launching, so its existence proves nothing."""
    return directory.is_dir() and any(directory.iterdir())


def _remove_worktree(target_repo: Path, worktree_path: Path) -> None:
    """Best-effort deregistration, for failure paths only. Never raises: it runs while
    another error is already on its way up, and masking that error with a git failure
    would hide the thing the caller actually needs to see."""
    try:
        _git(["worktree", "remove", "--force", str(worktree_path)], cwd=target_repo, error="")
    except Exception:
        pass


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


def _is_artifact(path: str) -> bool:
    """Whether a path git reported is the agent's own run dir rather than task output.

    Git reports a directory (`?? .teacup-run/`) when nothing inside it is tracked and
    the whole thing is new, and individual paths once any of it is, so both shapes have
    to match. Quoted paths (`"a b"`) keep their quote, hence the lstrip.
    """
    cleaned = path.strip().lstrip('"')
    return cleaned == ARTIFACTS_DIRNAME or cleaned.startswith(ARTIFACTS_DIRNAME + "/")


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
    uncommitted = [
        line[3:] for line in status.splitlines() if line.strip() and not _is_artifact(line[3:])
    ]

    # base_branch was already validated as a real ref when the worktree was created
    # (it's what the branch was cut from), so this can only come back empty, never fail.
    log = _git(["log", f"{base_branch}..HEAD", "--oneline"], cwd=worktree_path, error="git log failed")
    commits_made = len([line for line in log.splitlines() if line.strip()])

    committed = []
    if commits_made:
        diff_names = _git(
            ["diff", "--name-only", f"{base_branch}..HEAD"], cwd=worktree_path, error="git diff failed"
        )
        committed = [
            line for line in diff_names.splitlines() if line.strip() and not _is_artifact(line)
        ]

    files_changed = tuple(sorted(set(uncommitted) | set(committed)))

    # git diff HEAD --stat only covers already-tracked files — a brand-new file the
    # model wrote (untracked, never `git add`ed) shows up in `status --porcelain` as
    # `?? path` but not here at all, so it has to be reported separately or it goes
    # missing from the summary entirely despite being a real, uncommitted change.
    tracked_stat = _git(["diff", "HEAD", "--stat"], cwd=worktree_path, error="git diff --stat failed").strip()
    untracked = [
        line[3:]
        for line in status.splitlines()
        if line.startswith("??") and not _is_artifact(line[3:])
    ]

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
