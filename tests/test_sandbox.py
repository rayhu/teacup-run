"""sandbox.py: what it actually bounds — env, lifetime — not filesystem isolation.

Every test launches tests/fixtures/fake_cli.py with the real Python interpreter,
so these exercise real subprocess behavior (env scoping, a real timeout, a real
kill), not a mock of subprocess.
"""

from __future__ import annotations

import sys
from pathlib import Path

from teacup_run.sandbox import run_sandboxed

FAKE_CLI = Path(__file__).parent / "fixtures" / "fake_cli.py"


def _argv(*extra: str) -> list[str]:
    return [sys.executable, str(FAKE_CLI), "hello", "--json", *extra]


def test_normal_run_returns_parsed_stdout(tmp_path):
    result = run_sandboxed(_argv(), cwd=tmp_path, timeout=10)
    assert result.returncode == 0
    assert not result.timed_out
    assert '"status": "done"' in result.stdout


def test_env_allowlist_reaches_the_child(tmp_path, monkeypatch):
    monkeypatch.setenv("TEACUP_RUN_TEST_AMBIENT", "leaked-if-visible")
    result = run_sandboxed(
        _argv("--echo-env", "TEACUP_RUN_TEST_AMBIENT"),
        cwd=tmp_path,
        env_allowlist={},
        timeout=10,
    )
    assert "ENV TEACUP_RUN_TEST_AMBIENT=\n" in result.stderr, (
        "an unlisted ambient var from the caller's own shell must not reach the child"
    )


def test_env_allowlist_passes_through_named_vars(tmp_path, monkeypatch):
    monkeypatch.delenv("TEACUP_RUN_TEST_ALLOWED", raising=False)
    result = run_sandboxed(
        _argv("--echo-env", "TEACUP_RUN_TEST_ALLOWED"),
        cwd=tmp_path,
        env_allowlist={"TEACUP_RUN_TEST_ALLOWED": "visible"},
        timeout=10,
    )
    assert "ENV TEACUP_RUN_TEST_ALLOWED=visible" in result.stderr


def test_timeout_kills_a_hung_process(tmp_path):
    result = run_sandboxed(_argv("--sleep", "5"), cwd=tmp_path, timeout=0.3)
    assert result.timed_out is True
    # A real kill returns promptly; a leaked process would make this take ~5s.
    assert result.elapsed_s < 3


def test_cwd_is_honored_not_a_throwaway_scratch_directory(tmp_path):
    """Regression test: an earlier version of this module gave every launch a
    throwaway scratch cwd, which silently broke anything the launched program
    resolves relative to its own cwd (teacup-agent's read_file, mcp.json,
    skills/ discovery — see the module docstring). Pin it by proving a
    relative path in the launched process resolves against the *caller's*
    chosen `cwd`, not some directory this function invents.
    """
    (tmp_path / "marker.txt").write_text("found-it")
    result = run_sandboxed(
        [
            sys.executable,
            "-c",
            "import pathlib, sys; sys.stdout.write(pathlib.Path('marker.txt').read_text())",
        ],
        cwd=tmp_path,
        timeout=10,
    )
    assert result.stdout == "found-it"
