"""coding_task.py: a coding task always gets its own disposable git worktree +
branch, never touches the target repo's primary checkout, reports what actually
changed (committed and uncommitted), and never pushes or opens a PR.

Hermetic throughout: a throwaway git repo fixture (never the real teacup-agent/
teacup-run checkouts) plus fixtures/fake_cli.py as the entrypoint, exactly like
test_external_cli.py.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from teacup_run.coding_task import CodingTaskError, _collect_diff, _create_worktree, run_coding_task
from teacup_run.manifest import AgentSpec

FAKE_CLI = Path(__file__).parent / "fixtures" / "fake_cli.py"

# Hermetic regardless of the host's own git config (a CI runner may have none).
_GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def _git(args: list[str], cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    import os

    full_env = {**os.environ, **_GIT_ENV, **(env or {})}
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True, env=full_env
    )


@pytest.fixture
def target_repo(tmp_path) -> Path:
    """A throwaway git repo with one commit on `main` and a tracked file."""
    repo = tmp_path / "target"
    repo.mkdir()
    _git(["init", "-b", "main"], cwd=repo)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "README.md"], cwd=repo)
    _git(["commit", "-m", "initial"], cwd=repo)
    return repo


def _manifest() -> str:
    entrypoint = f"{sys.executable} {FAKE_CLI}"
    return f"""
name: fake-bridge
version: 0.1.0
description: test fixture
framework: teacup-agent-cli
entrypoint: "{entrypoint}"
model:
  primary: gpt-5
budget:
  default_usd: 0.10
  max_wall_clock_s: 5
teacup_agent:
  project_root: "."
"""


def _spec(tmp_path) -> AgentSpec:
    return AgentSpec.load(tmp_path, manifest_text=_manifest())


# --- worktree + branch lifecycle -----------------------------------------------


def test_run_coding_task_creates_a_worktree_on_a_new_branch(tmp_path, target_repo):
    task_result = run_coding_task(_spec(tmp_path), "do the thing", target_repo=target_repo, live=False)

    assert task_result.worktree_path.is_dir()
    assert (task_result.worktree_path / "README.md").is_file()  # checked out from base_branch
    branches = _git(["branch", "--list", task_result.branch], cwd=target_repo).stdout
    assert task_result.branch in branches


def test_run_coding_task_never_touches_the_original_checkout(tmp_path, target_repo):
    before_head = _git(["rev-parse", "main"], cwd=target_repo).stdout.strip()
    before_status = _git(["status", "--porcelain"], cwd=target_repo).stdout

    run_coding_task(_spec(tmp_path), "do the thing", target_repo=target_repo, live=False)

    after_head = _git(["rev-parse", "main"], cwd=target_repo).stdout.strip()
    after_status = _git(["status", "--porcelain"], cwd=target_repo).stdout
    assert after_head == before_head
    assert after_status == before_status


def test_run_coding_task_returns_the_underlying_agent_result(tmp_path, target_repo):
    task_result = run_coding_task(_spec(tmp_path), "hello world", target_repo=target_repo, live=False)

    assert task_result.result.answer == "echo: hello world"
    assert str(task_result) == "echo: hello world"  # __str__ delegates to the answer


def test_run_coding_task_raises_a_clear_error_for_a_nonexistent_base_branch(tmp_path, target_repo):
    with pytest.raises(CodingTaskError, match="no-such-branch"):
        run_coding_task(
            _spec(tmp_path), "t", target_repo=target_repo, base_branch="no-such-branch", live=False
        )


def test_run_coding_task_raises_a_clear_error_when_target_is_not_a_git_repo(tmp_path):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    with pytest.raises(CodingTaskError, match="not a git repository"):
        run_coding_task(_spec(tmp_path), "t", target_repo=not_a_repo, live=False)


def test_branch_names_are_unique_across_two_tasks_with_the_same_text(tmp_path, target_repo):
    first = run_coding_task(_spec(tmp_path), "same task", target_repo=target_repo, live=False)
    second = run_coding_task(_spec(tmp_path), "same task", target_repo=target_repo, live=False)
    assert first.branch != second.branch


# --- passing the coding-tools/approval flags through ---------------------------


def test_run_coding_task_always_requests_coding_tools_and_hooks_approval(tmp_path, target_repo, monkeypatch):
    """coding_task.py imports run_external lazily inside run_coding_task, resolving
    it from teacup_run.external_cli's own module namespace at call time — so
    patching the attribute there (not a name imported into coding_task.py) is what
    actually intercepts the call."""
    captured = {}
    import teacup_run.external_cli as external_cli_mod

    real = external_cli_mod.run_external

    def spy(spec, task, **kwargs):
        captured.update(kwargs)
        return real(spec, task, **kwargs)

    monkeypatch.setattr(external_cli_mod, "run_external", spy)
    task_result = run_coding_task(_spec(tmp_path), "t", target_repo=target_repo, live=False)

    assert captured["extra_flags"] == ("--coding-tools", "--approve", "hooks")
    assert captured["target_repo"] == task_result.worktree_path  # operates on the worktree, not target_repo


# --- diff collection ------------------------------------------------------------


def test_collect_diff_reports_uncommitted_changes(tmp_path, target_repo):
    worktree = tmp_path / "wt"
    _create_worktree(target_repo, worktree, branch="teacup-run/t1", base_branch="main")
    (worktree / "new.txt").write_text("new content\n", encoding="utf-8")

    files_changed, diff_stat, commits_made = _collect_diff(worktree, "main")

    assert "new.txt" in files_changed
    assert commits_made == 0
    assert "new.txt" in diff_stat  # untracked files aren't covered by `git diff --stat` alone


def test_collect_diff_reports_committed_changes(tmp_path, target_repo):
    worktree = tmp_path / "wt"
    _create_worktree(target_repo, worktree, branch="teacup-run/t2", base_branch="main")
    (worktree / "new.txt").write_text("new content\n", encoding="utf-8")
    _git(["add", "new.txt"], cwd=worktree)
    _git(["commit", "-m", "add new.txt"], cwd=worktree)

    files_changed, diff_stat, commits_made = _collect_diff(worktree, "main")

    assert "new.txt" in files_changed
    assert commits_made == 1
    assert "1 commit(s)" in diff_stat


def test_collect_diff_reports_no_changes_cleanly(tmp_path, target_repo):
    worktree = tmp_path / "wt"
    _create_worktree(target_repo, worktree, branch="teacup-run/t3", base_branch="main")

    files_changed, diff_stat, commits_made = _collect_diff(worktree, "main")

    assert files_changed == ()
    assert commits_made == 0
    assert diff_stat == "(no changes)"


def test_run_coding_task_reports_files_changed_end_to_end(tmp_path, target_repo):
    """fake_cli.py makes no file changes on its own, so this pins the "clean" case
    through the full public API — the mutation cases above test _collect_diff
    directly, since fake_cli.py has no reason to grow a file-writing test flag."""
    task_result = run_coding_task(_spec(tmp_path), "t", target_repo=target_repo, live=False)
    assert task_result.files_changed == ()
    assert task_result.commits_made == 0


# --- test_command -----------------------------------------------------------


def test_run_coding_task_runs_the_test_command_and_reports_pass(tmp_path, target_repo):
    task_result = run_coding_task(
        _spec(tmp_path),
        "t",
        target_repo=target_repo,
        live=False,
        test_command=f"{sys.executable} -c \"import sys; sys.exit(0)\"",
    )
    assert task_result.tests_passed is True


def test_run_coding_task_runs_the_test_command_and_reports_failure(tmp_path, target_repo):
    task_result = run_coding_task(
        _spec(tmp_path),
        "t",
        target_repo=target_repo,
        live=False,
        test_command=f"{sys.executable} -c \"import sys; sys.exit(1)\"",
    )
    assert task_result.tests_passed is False


def test_run_coding_task_reports_test_failure_when_test_command_executable_is_missing(tmp_path, target_repo):
    """Regression test (found by independent review): a bad test_command used to
    raise FileNotFoundError straight out of subprocess.Popen and crash the whole
    function, discarding an already-successful coding-task result (branch, diff,
    answer) over an unrelated test-runner typo. It must degrade to a reported
    test failure instead, the same as a real test failure would."""
    task_result = run_coding_task(
        _spec(tmp_path),
        "t",
        target_repo=target_repo,
        live=False,
        test_command="definitely-not-a-real-binary-xyz",
    )
    assert task_result.tests_passed is False
    assert "could not run test_command" in task_result.test_output
    # the rest of the result must survive — this is the whole point of the fix
    assert task_result.result.answer == "echo: t"
    assert task_result.worktree_path.is_dir()


def test_run_coding_task_skips_tests_when_no_test_command_given(tmp_path, target_repo):
    """A deliberate no-op, not a guess: there is no reliable way to infer a target
    repo's own test command, so run_tests=True with no test_command is None, not
    a claim about whether tests pass."""
    task_result = run_coding_task(_spec(tmp_path), "t", target_repo=target_repo, live=False)
    assert task_result.tests_passed is None
    assert task_result.test_output is None


def test_run_coding_task_skips_tests_when_run_tests_is_false_even_with_a_command(tmp_path, target_repo):
    task_result = run_coding_task(
        _spec(tmp_path),
        "t",
        target_repo=target_repo,
        live=False,
        run_tests=False,
        test_command=f"{sys.executable} -c \"import sys; sys.exit(1)\"",
    )
    assert task_result.tests_passed is None


# --- failure cleanup and isolation ----------------------------------------------


def test_run_coding_task_cleans_up_its_scratch_dir_when_worktree_creation_fails(tmp_path, target_repo, monkeypatch):
    """Regression test (found by independent review): tempfile.mkdtemp() runs
    before _create_worktree validates anything, and isn't a context manager — a
    bad base_branch used to leak that directory forever on every failed call."""
    import teacup_run.coding_task as coding_task_mod

    created = {}
    real_mkdtemp = coding_task_mod.tempfile.mkdtemp

    def spy_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created["scratch"] = Path(path)
        return path

    monkeypatch.setattr(coding_task_mod.tempfile, "mkdtemp", spy_mkdtemp)

    with pytest.raises(CodingTaskError):
        run_coding_task(_spec(tmp_path), "t", target_repo=target_repo, base_branch="no-such-branch", live=False)

    assert "scratch" in created
    assert not created["scratch"].exists()


def test_run_coding_tasks_worktree_reflects_only_the_last_commit_not_dirty_changes_in_target(
    tmp_path, target_repo
):
    """A pre-existing uncommitted change in target_repo's own working tree must not
    leak into the worktree — git worktree add checks out base_branch's last commit,
    not whatever happens to be sitting dirty in the primary checkout."""
    (target_repo / "README.md").write_text("dirty, uncommitted change\n", encoding="utf-8")

    task_result = run_coding_task(_spec(tmp_path), "t", target_repo=target_repo, live=False)

    checked_out = (task_result.worktree_path / "README.md").read_text(encoding="utf-8")
    assert checked_out == "hello\n"  # the committed content, not target_repo's dirty edit


def test_collect_diff_reports_a_committed_rename_correctly(tmp_path, target_repo):
    """Committed renames resolve to the new path via `git diff --name-only`, unlike
    the known, documented limitation for an uncommitted rename (see the comment in
    _collect_diff) — this pins the case that does work correctly."""
    worktree = tmp_path / "wt"
    _create_worktree(target_repo, worktree, branch="teacup-run/rename1", base_branch="main")
    (worktree / "README.md").rename(worktree / "RENAMED.md")
    _git(["add", "-A"], cwd=worktree)
    _git(["commit", "-m", "rename"], cwd=worktree)

    files_changed, diff_stat, commits_made = _collect_diff(worktree, "main")

    assert "RENAMED.md" in files_changed
    assert commits_made == 1
