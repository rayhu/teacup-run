"""`framework: teacup-agent-cli` — run a teacup-agent installation as a sandboxed
subprocess: batch-style, one task in, one JSON result out, then the process exits.

Why not talk to it over the network (A2A)? teacup-agent already ships an A2A
client and server (`delegate_a2a`, `teacup-agent-serve`), but its own roadmap
frames the server as a narrow, deliberate exception to "no service layer" — not a
pattern to build on for one-shot task execution. Spinning up an HTTP server,
picking a port, and tearing it down again for a single task is real overhead a
plain subprocess invocation does not have.

Why the plain-flags CLI, not `--config agent.yaml`: teacup-agent's `--config` path
has no offline mode at all — `_main_config()` always builds a real, billable model
(`live=True` is hardcoded there, since "a config run is always real"). Routing
every sandboxed invocation through it would make this backend impossible to test
without spending money. The plain-flags path keeps `--live` optional, so a
hermetic test (and a `live=False` call here) gets teacup-agent's free, instant,
scripted offline demo instead — see docs/backends.md.

Why both `uv run --project <root>` **and** `cwd=<root>`: `--project` alone
only tells `uv` where to find `pyproject.toml`/the venv — it does **not**
change the subprocess's actual working directory (verified empirically; see
sandbox.py's module docstring). teacup-agent's own CLI resolves
`--mcp`/`--skills` defaults and `read_file`'s root against its real OS-level
cwd, so `sandbox.run_sandboxed` is called with `cwd=project_root` explicitly
— an earlier version of this file relied on `--project` alone and shipped
with those defaults silently resolving against an empty scratch directory
instead. `--run-dir`/`--memory` stay absolute paths into teacup-run's own
scratch space regardless, so run artifacts never land in the target project.

Current limitation, stated rather than hidden: `_build_argv` only knows how to
insert `--project` after a `uv run ...`-shaped entrypoint. A future framework
whose entrypoint isn't `uv run`-based would need its own insertion rule — not
needed yet, since `teacup-agent-cli` is the only backend that exists.
"""

from __future__ import annotations

import json
import shlex
import tempfile
from pathlib import Path

from .budget import Ledger
from .env import load_env
from .loop import Result
from .manifest import AgentSpec, ManifestError
from .sandbox import run_sandboxed

__all__ = ["run_external"]

_DEFAULT_DEADLINE_S = 600.0  # teacup-agent cli.py's own default
_GRACE_S = 30.0  # headroom for teacup-agent's own forced wrap-up to finish and print JSON


def run_external(
    spec: AgentSpec,
    task: str,
    *,
    budget: float | None = None,
    live: bool = True,
    timeout: float | None = None,
) -> Result:
    """Run `spec` (a `framework != "teacup"` package) as a sandboxed subprocess,
    normalizing its output into teacup-run's own `Result`.

    `live=False` (offline scripted demo, no network, no cost) is what tests and
    the cross-repo smoke test use; production calls leave it at the default.
    """
    project_root = _project_root(spec)
    budget_usd = budget if budget is not None else (spec.budget_usd or 0.05)
    deadline_s = timeout if timeout is not None else (spec.budget_max_wall_clock_s or _DEFAULT_DEADLINE_S)

    load_env()  # same cwd-upward .env search teacup-run's own CLI plan uses (docs/execution.md §3)
    # teacup-agent's offline scripted demo (live=False) reads no key at all — requiring
    # one anyway would make every hermetic test and dry run need a real credential.
    env_allowlist = _resolve_env(spec) if live else {}

    with tempfile.TemporaryDirectory(prefix="teacup-run-external-") as scratch:
        scratch_dir = Path(scratch)
        argv = _build_argv(
            spec.entrypoint or "uv run teacup-agent",
            project_root=project_root,
            task=task,
            budget=budget_usd,
            deadline=deadline_s,
            live=live,
            run_dir=scratch_dir / "runs",
            memory_path=scratch_dir / "memory.json",
        )
        result = run_sandboxed(
            argv, cwd=project_root, env_allowlist=env_allowlist, timeout=deadline_s + _GRACE_S
        )

    ledger = Ledger()
    ledger.stop_clock()

    if result.timed_out:
        return Result(
            answer="",
            ledger=ledger,
            stopped_early=True,
            stop_reason=f"sandboxed run timed out after {result.elapsed_s:.0f}s",
        )
    if result.returncode not in (0, 1):  # neither "done" (0) nor "goal not met" (1) — a real crash
        return Result(
            answer="",
            ledger=ledger,
            stopped_early=True,
            stop_reason=f"teacup-agent exited {result.returncode}: {result.stderr[-500:]}",
        )

    payload = _parse_json_line(result.stdout)
    if payload is None:
        return Result(
            answer="",
            ledger=ledger,
            stopped_early=True,
            stop_reason=f"could not parse --json output: {result.stdout[-500:]!r}",
        )

    # teacup-agent doesn't decompose cost into model/tool/compute the way our own
    # Ledger does — one lump-sum line is the honest amount of detail available.
    spent = max(0.0, budget_usd - payload.get("remaining_budget", budget_usd))
    ledger.record_tool_call("teacup-agent", cost_usd=spent)

    done = payload.get("status") == "done"
    return Result(
        answer=payload.get("answer", ""),
        ledger=ledger,
        stopped_early=not done,
        stop_reason=None if done else payload.get("status"),
    )


def _project_root(spec: AgentSpec) -> Path:
    options = spec.raw.get("teacup_agent") or {}
    project_root_value = options.get("project_root")
    if not project_root_value:
        raise ManifestError(
            f"{spec.name}: framework {spec.framework!r} needs a "
            "teacup_agent.project_root in agent.yaml"
        )
    return (spec.root / project_root_value).resolve()


def _resolve_env(spec: AgentSpec) -> dict[str, str]:
    """Only called for `live=True` — see the comment at the call site."""
    import os

    missing = [name for name in spec.environment_required if name not in os.environ]
    if missing:
        raise ManifestError(
            f"{spec.name}: missing required environment variable(s): {', '.join(missing)} "
            "(check .env)"
        )
    return {name: os.environ[name] for name in spec.environment_required}


def _build_argv(
    entrypoint: str,
    *,
    project_root: Path,
    task: str,
    budget: float,
    deadline: float,
    live: bool,
    run_dir: Path,
    memory_path: Path,
) -> list[str]:
    argv = shlex.split(entrypoint)
    if argv[:2] == ["uv", "run"]:
        argv = argv[:2] + ["--project", str(project_root)] + argv[2:]
    argv += [
        task,
        "--json",
        "--budget",
        str(budget),
        "--deadline",
        str(deadline),
        "--run-dir",
        str(run_dir),
        "--memory",
        str(memory_path),
    ]
    if live:
        argv.append("--live")
    return argv


def _parse_json_line(stdout: str) -> dict | None:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None
    return None
