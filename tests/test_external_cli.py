"""external_cli.py: the framework != "teacup" dispatch path, made hermetic by
pointing `entrypoint` straight at fixtures/fake_cli.py instead of `uv run
teacup-agent` — no real teacup-agent checkout, no `uv`, no network, no cost.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from teacup_run.external_cli import _build_argv, run_external
from teacup_run.manifest import AgentSpec, ManifestError

FAKE_CLI = Path(__file__).parent / "fixtures" / "fake_cli.py"


def _manifest(*, required_env: str = "FAKE_API_KEY") -> str:
    entrypoint = f"{sys.executable} {FAKE_CLI}"
    return f"""
name: fake-bridge
version: 0.1.0
description: test fixture
framework: teacup-agent-cli
entrypoint: "{entrypoint}"
model:
  primary: gpt-5
environment:
  required:
    - {required_env}
budget:
  default_usd: 0.10
  max_wall_clock_s: 5
teacup_agent:
  project_root: "."
"""


def test_run_external_round_trips_through_the_fake_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "secret123")
    spec = AgentSpec.load(tmp_path, manifest_text=_manifest())

    result = run_external(spec, "hello world", live=False)

    assert result.answer == "echo: hello world"
    assert result.stop_reason is None
    assert not result.stopped_early
    assert result.ledger.tool_cost > 0  # the lump-sum "teacup-agent" line was recorded


def test_run_external_requires_declared_env_vars_when_live(tmp_path, monkeypatch):
    monkeypatch.delenv("FAKE_API_KEY", raising=False)
    spec = AgentSpec.load(tmp_path, manifest_text=_manifest())

    with pytest.raises(ManifestError, match="FAKE_API_KEY"):
        run_external(spec, "hello world", live=True)


def test_run_external_offline_does_not_require_env_vars(tmp_path, monkeypatch):
    """A regression test: teacup-agent's offline scripted demo reads no key at
    all, so live=False must not demand one either — an earlier version of this
    backend got this backwards and made every hermetic call require a real key."""
    monkeypatch.delenv("FAKE_API_KEY", raising=False)
    spec = AgentSpec.load(tmp_path, manifest_text=_manifest())

    result = run_external(spec, "hello world", live=False)

    assert result.answer == "echo: hello world"


def test_run_external_reports_timeout(tmp_path, monkeypatch):
    """The timeout *mechanism* is sandbox.py's own job (test_sandbox.py exercises
    a real one); this only checks that run_external translates a timed-out
    SandboxResult into a Result correctly. A real timeout here would need to
    outlast run_external's own 30s grace period on top of the deadline — not
    worth a slow test when the translation is what's actually novel here.
    """
    from teacup_run import external_cli
    from teacup_run.sandbox import SandboxResult

    monkeypatch.setenv("FAKE_API_KEY", "secret123")
    monkeypatch.setattr(
        external_cli,
        "run_sandboxed",
        lambda argv, **kw: SandboxResult(
            stdout="", stderr="", returncode=None, timed_out=True, elapsed_s=30.2, limits_applied=True
        ),
    )
    spec = AgentSpec.load(tmp_path, manifest_text=_manifest())

    result = run_external(spec, "hello", live=False)

    assert result.stopped_early
    assert "timed out" in result.stop_reason


def test_build_argv_inserts_project_after_uv_run(tmp_path):
    argv = _build_argv(
        "uv run teacup-agent",
        project_root=tmp_path,
        task="do the thing",
        budget=0.1,
        deadline=10.0,
        live=True,
        run_dir=tmp_path / "runs",
        memory_path=tmp_path / "memory.json",
    )
    assert argv[:5] == ["uv", "run", "--project", str(tmp_path), "teacup-agent"]
    assert "do the thing" in argv
    assert "--live" in argv


def test_build_argv_leaves_non_uv_entrypoints_alone(tmp_path):
    argv = _build_argv(
        "some-other-cli",
        project_root=tmp_path,
        task="t",
        budget=0.1,
        deadline=10.0,
        live=False,
        run_dir=tmp_path / "runs",
        memory_path=tmp_path / "memory.json",
    )
    assert argv[0] == "some-other-cli"
    assert "--project" not in argv
    assert "--live" not in argv
