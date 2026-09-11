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
instead. `--memory` stays an absolute path into teacup-run's own scratch
space. `--run-dir` used to as well, and `coding_task.py` now deliberately points
it *inside* the worktree instead — see `run_external`'s `run_dir` parameter for
why the excerpt-plus-path mechanism it feeds only works when the model can reach
the directory. Artifacts therefore do land in the target checkout for a coding
task; `coding_task._collect_diff` filters them back out of the reported diff, and
the worktree they land in is disposable.

Current limitation, stated rather than hidden: `_build_argv` only knows how to
insert `--project` after a `uv run ...`-shaped entrypoint. A future framework
whose entrypoint isn't `uv run`-based would need its own insertion rule — not
needed yet, since `teacup-agent-cli` is the only backend that exists.

`target_repo` (Phase 3, `coding_task.py`) decouples two things this module used
to conflate: `project_root` is where *teacup-agent's own code and deps* live —
`uv run --project` needs it to find `pyproject.toml`/the venv, and it never
changes. `target_repo` is what the launched agent should actually *operate
on*: the sandbox `cwd`, which `coding_task.py` points at a disposable git
worktree so the same teacup-agent checkout can be driven against teacup-run's
own repo, or any other target, not only wherever `project_root` happens to
point. `target_repo` defaults to `project_root` — plain `run_external()` calls
(no coding task involved) are unaffected.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import tempfile
from pathlib import Path
from typing import Sequence

from .budget import Ledger
from .env import load_env
from .loop import Result
from .manifest import AgentSpec, ManifestError
from .sandbox import run_sandboxed

__all__ = ["run_external"]

_DEFAULT_DEADLINE_S = 600.0  # teacup-agent cli.py's own default
_GRACE_S = 30.0  # headroom for teacup-agent's own forced wrap-up to finish and print JSON


# The child's statuses that mean "ran out of an allowance", as opposed to "broke".
# Kept beside the mapping rather than inline so the list is visible as a list.
CEILING_STATUSES = frozenset({"out_of_budget", "out_of_time", "max_steps"})


def run_external(
    spec: AgentSpec,
    task: str,
    *,
    budget: float | None = None,
    live: bool = True,
    timeout: float | None = None,
    target_repo: Path | None = None,
    extra_flags: Sequence[str] = (),
    run_dir: Path | None = None,
    model: str | None = None,
) -> Result:
    """Run `spec` (a `framework != "teacup"` package) as a sandboxed subprocess,
    normalizing its output into teacup-run's own `Result`.

    `live=False` (offline scripted demo, no network, no cost) is what tests and
    the cross-repo smoke test use; production calls leave it at the default.

    `target_repo`: what the launched process's cwd should be, if different from
    `project_root` (see the module docstring) — `coding_task.py` is the caller
    that needs this; a plain call leaves it unset and behaves exactly as before.
    `extra_flags` are appended to the launched CLI's argv verbatim (e.g.
    `coding_task.py` passes `--coding-tools --approve hooks`); empty by default.

    `run_dir`: where the launched agent writes its `--run-dir` trajectory. This
    is not just a place to put logs — teacup-agent externalizes any tool result
    over 2000 chars into this directory and leaves the model a 600-char excerpt
    plus the path, telling it to `read_file` that path for the rest. So the
    directory has to be somewhere the model is *allowed to read*: teacup-agent's
    `read_file` rejects anything outside its cwd, and `context.externalize()`
    only emits a relative path when the run dir is under that cwd (it falls back
    to an absolute one otherwise). A run dir outside the target repo therefore
    silently truncates every large file the model reads and hands it a path it
    is forbidden to follow — confirmed live: reading a 12147-char source file
    showed the model 864 characters and an unusable absolute path, and it spent
    six `edit_file` calls guessing at text it had never been shown.

    Default `None` keeps the historical behaviour (a `TemporaryDirectory`
    discarded when the subprocess exits), which is fine for a throwaway call but
    also leaves no record of *why* a disappointing run went the way it did.
    `coding_task.py` passes the worktree's own gitignored `runs/`.
    """
    project_root = _project_root(spec)
    cwd = target_repo if target_repo is not None else project_root
    budget_usd = budget if budget is not None else (spec.budget_usd or 0.05)
    deadline_s = timeout if timeout is not None else (spec.budget_max_wall_clock_s or _DEFAULT_DEADLINE_S)

    load_env()  # same cwd-upward .env search teacup-run's own CLI plan uses (docs/execution.md §3)
    # teacup-agent's offline scripted demo (live=False) reads no key at all — requiring
    # one anyway would make every hermetic test and dry run need a real credential.
    env_allowlist = _resolve_env(spec) if live else {}

    with tempfile.TemporaryDirectory(prefix="teacup-run-external-") as scratch:
        scratch_dir = Path(scratch)
        if run_dir is not None:
            run_dir.mkdir(parents=True, exist_ok=True)
        agent_run_dir = run_dir if run_dir is not None else scratch_dir / "runs"
        argv = _build_argv(
            spec.entrypoint or "uv run teacup-agent",
            model=model,
            project_root=project_root,
            task=task,
            budget=budget_usd,
            deadline=deadline_s,
            live=live,
            run_dir=agent_run_dir,
            memory_path=scratch_dir / "memory.json",
            extra_flags=extra_flags,
        )
        result = run_sandboxed(
            argv, cwd=cwd, env_allowlist=env_allowlist, timeout=deadline_s + _GRACE_S
        )

    ledger = Ledger()
    # Seeded from what the sandbox actually measured, then stopped. Constructing the
    # Ledger here and stopping it on the next line reported `elapsed_s: 0.0` and
    # `cost.compute: 0.0` for every bridge run however long it took — the one field in
    # the §7 object that did not reconcile with reality.
    ledger.started_at = ledger.started_at - result.elapsed_s
    ledger.stop_clock()

    if result.timed_out:
        return Result(
            answer="",
            ledger=ledger,
            stopped_early=True,
            stop_kind="error",
            stop_reason=f"sandboxed run timed out after {result.elapsed_s:.0f}s",
        )
    if result.returncode not in (0, 1):  # neither "done" (0) nor "goal not met" (1) — a real crash
        return Result(
            answer="",
            ledger=ledger,
            stopped_early=True,
            stop_kind="error",
            stop_reason=f"teacup-agent exited {result.returncode}: {result.stderr[-500:]}",
        )

    payload = _parse_json_line(result.stdout)
    if payload is None:
        return Result(
            answer="",
            ledger=ledger,
            stopped_early=True,
            stop_kind="error",
            stop_reason=f"could not parse --json output: {result.stdout[-500:]!r}",
        )

    # teacup-agent doesn't decompose cost into model/tool/compute the way our own
    # Ledger does — one lump-sum line is the honest amount of detail available.
    spent = max(0.0, budget_usd - payload.get("remaining_budget", budget_usd))
    ledger.record_tool_call("teacup-agent", cost_usd=spent)

    done = payload.get("status") == "done"
    status = payload.get("status")
    # The child reports max_steps / out_of_budget / out_of_time / error, and §5 splits
    # those two ways: 2 is "ran out of an allowance you set", 3 is "something broke".
    # All three ceilings are the first. Flattening max_steps and out_of_time into
    # "error" was defended as the safe direction, but it made the same manifest report
    # a different exit code depending only on its `framework:` key — a wall-clock
    # deadline is `BudgetExceeded` and exit 2 on the native loop and was exit 3 here.
    # Anything unrecognised stays "error": an unknown status is not a known ceiling.
    stop_kind = None if done else ("budget" if status in CEILING_STATUSES else "error")
    return Result(
        answer=payload.get("answer", ""),
        ledger=ledger,
        stopped_early=not done,
        stop_reason=None if done else status,
        stop_kind=stop_kind,
    )


def check_wiring(spec: AgentSpec) -> None:
    """Everything about a `framework != "teacup"` package that can be checked without
    launching it. Raises `ManifestError` naming the first thing that is wrong.

    `--dry-run`'s promise is "the package is wired correctly and this machine is
    configured to run it" (§6), and for this framework it checked none of that path:
    a manifest with no `teacup_agent.project_root` passed dry-run and exited 0, then
    failed the real run with a ManifestError. A wiring check that skips the wiring is
    worse than none, because it answers.
    """
    root = _project_root(spec)
    if not root.is_dir():
        raise ManifestError(
            f"{spec.name}: teacup_agent.project_root points at {root}, which is not a "
            "directory"
        )
    entrypoint = spec.entrypoint or "uv run teacup-agent"
    argv = shlex.split(entrypoint)
    if not argv:
        raise ManifestError(f"{spec.name}: entrypoint is empty")
    # Checking only that the string splits answered "am I configured to run it?" with
    # yes on a machine that has no such binary: `--dry-run` exited 0 and the real run
    # died with FileNotFoundError. Same shape as the `project_root` case one field over.
    # Three shapes, because they resolve three different ways and the first version of
    # this check got two of them wrong:
    #
    # - an absolute path: `is_file()` alone let a mode-644 file pass preflight and die
    #   at launch with PermissionError — the very failure this check exists to prevent,
    #   so the executable bit is part of the question;
    # - a path with a separator (`./run-agent`): `shutil.which` resolves it against
    #   *this* process's cwd, but `run_sandboxed` launches the child with
    #   `cwd=project_root` and `subprocess` chdirs before exec. So a correctly-wired
    #   package was accepted or refused depending on where the user happened to be
    #   standing, with a message blaming the machine;
    # - a bare name: PATH, which `shutil.which` is exactly right for, and the child
    #   inherits PATH via `sandbox._BASE_ENV_NAMES`.
    program = Path(argv[0])
    if program.is_absolute():
        candidate = program
    elif any(sep in argv[0] for sep in (os.sep, os.altsep) if sep):
        # The raw string, not `program.parts`: pathlib normalizes "./run-agent" to
        # ("run-agent",), so a parts check treats the commonest relative form as a bare
        # name and sends it to PATH — exactly the bug this branch exists to fix.
        candidate = root / program
    else:
        found = shutil.which(argv[0])
        candidate = Path(found) if found else None

    if candidate is None or not candidate.is_file():
        raise ManifestError(
            f"{spec.name}: entrypoint {argv[0]!r} was not found — this machine cannot "
            f"run {spec.framework!r} packages until it is installed"
        )
    if not os.access(candidate, os.X_OK):
        raise ManifestError(
            f"{spec.name}: entrypoint {argv[0]!r} ({candidate}) is not executable"
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
    model: str | None,
    project_root: Path,
    task: str,
    budget: float,
    deadline: float,
    live: bool,
    run_dir: Path,
    memory_path: Path,
    extra_flags: Sequence[str] = (),
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
    if model:
        # The child takes `--model`; without this the override reached nothing and the
        # run used the child's own default while the report named the requested one.
        argv += ["--model", model]
    argv += list(extra_flags)
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
