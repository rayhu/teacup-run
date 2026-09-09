"""`teacup` — running an agent without writing Python.

That rule is the reason this project exists (see the README), and until this module
there was no way to obey it: `AutoAgent` is a fine library and a library is not a
command. The design is `docs/execution.md`; this is its implementation, and where the
two disagree the document is the one to fix first.

Three things here are load-bearing enough to say up front.

**Preflight happens before any spend.** Resolve, validate, resolve the environment,
check that every declared variable is actually present — and only then call a model. A
missing key that surfaces as a 401 halfway through a run has already cost money and has
already thrown away the work; the same failure at preflight costs nothing and says
which variable.

**stdout carries the answer and nothing else.** The preflight echo and the ledger go to
stderr, so `teacup run ... > answer.txt` leaves a clean file with the accounting still
on the terminal. `--json` puts exactly one object on stdout, for the same reason.

**Settings and credentials resolve on two separate chains.** Flags beat `TEACUP_*` beat
the config file beat the manifest, for settings. Credentials come from the process
environment first and one file second, and the config file can only point at that file,
never hold a value — see `config.py`.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys

import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import registry
from .auto import AutoAgent
from .budget import Budget, Ledger
from .config import DEFAULT_BUDGET_USD, Config, config_path, effective_hub, load_config
from .env import load_env
from .loop import Result
from .manifest import AgentSpec, ManifestError
from .registry import RegistryError

__all__ = ["main"]

# Exit codes are a contract (docs/execution.md §5), not an implementation detail: CI
# reads them, and "goal not met" being distinguishable from "crashed" is the whole
# point of having more than one non-zero code.
EXIT_OK = 0
EXIT_GOAL_NOT_MET = 1
EXIT_BUDGET = 2
EXIT_ERROR = 3
EXIT_PREFLIGHT = 4


class PreflightError(RuntimeError):
    """The run never started: bad ref, invalid manifest, or missing environment."""


@dataclass
class Preflight:
    """What resolution decided, echoed so "why did it use that key" is answerable."""

    agent: AutoAgent
    ref: str
    # None when a non-native framework picks its own model: naming one we did not
    # choose is what the round-2 review found reported as fact. §7 allows null.
    model: str | None
    budget: Budget
    env_source: str
    config: Config

    def render(self) -> str:
        spec = self.agent.spec
        tools = ", ".join(spec.tools) or "none"
        skills = ", ".join(self.agent.enabled_skills) or "none"
        # `_resolve_budget` always lands on a float — `DEFAULT_BUDGET_USD` is the last
        # link in §4's chain — so there is no unlimited case to render here.
        budget = f"${self.budget.usd:,.2f}"
        lines = [
            f"agent   {spec.name} {spec.version}  ({self.ref})",
            f"model   {self.model or f'(whatever {spec.framework} is configured to use)'}",
            f"budget  {budget}",
            f"tools   {tools}",
            f"skills  {skills}",
            f"env     {self.env_source}",
        ]
        if self.config.source is not None and self.config.source.is_file():
            lines.append(f"config  {self.config.source}")
        return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 2 for a usage error, and §5 reserves 2 for "stopped early:
        # budget exceeded". A CI job branching on the exit code would read a typo'd
        # flag as an overspend. A usage error did not start the run, which is what 4
        # means. `--help` exits 0 and is left alone.
        code = exc.code if isinstance(exc.code, int) else EXIT_PREFLIGHT
        return EXIT_PREFLIGHT if code == 2 else code
    if args.command is None:
        parser.print_help(sys.stderr)
        return EXIT_PREFLIGHT
    try:
        return _run_command(args)
    except Exception as exc:  # noqa: BLE001
        # Without this, an unhandled exception propagates out of main() and the
        # interpreter exits 1 — which §5 defines as "completed; goal not met". A crash
        # reported as a well-defined result is the exact confusion the table exists to
        # prevent, so the catch-all maps to 3 and says what happened.
        print(f"ERROR: teacup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="teacup",
        description="Run, and eventually publish, agents you did not have to write.",
    )
    sub = p.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="run an agent on a task")
    run_p.add_argument("ref", help="a local path, a name in the hub, or a git URL")
    run_p.add_argument("task", help="the task; '-' reads it from stdin")
    run_p.add_argument("--budget", type=float, default=None, help="override the manifest budget, in USD")
    run_p.add_argument("--model", default=None, help="override the manifest model")
    run_p.add_argument("--skill", action="append", default=[], help="enable a packaged skill (repeatable)")
    run_p.add_argument("--no-goal-loop", action="store_true", help="single attempt, skip the outer loop")
    run_p.add_argument("--env-file", default=None, help="explicit .env")
    run_p.add_argument("--no-dotenv", action="store_true", help="do not search the working directory for .env")
    run_p.add_argument("--config", default=None, help="explicit config file")
    run_p.add_argument("--json", action="store_true", help="one JSON object on stdout, and nothing else")
    run_p.add_argument("--dry-run", action="store_true", help="preflight and wire up; make no model call")
    run_p.add_argument("-q", "--quiet", action="store_true", help="suppress the preflight echo and ledger")
    return p


def _run_command(args: argparse.Namespace) -> int:
    # stdout belongs to teacup; what the package `print`s goes to stderr.
    #
    # The limit, stated rather than implied: `redirect_stdout` rebinds `sys.stdout`, so
    # a package writing to fd 1 directly (`os.write`, `sys.__stdout__`, a C extension,
    # its own uncaptured subprocess) still reaches the real stream. That is not the
    # case this closes. `run_sandboxed` captures the external backend's child, so the
    # bridge path is covered by a different mechanism.
    #
    # §5 promises `--json` puts one object on stdout and *nothing else*, and a package
    # is ordinary Python: `from_pretrained` imports its `tools.py` and `checks.py`, and
    # a tool function runs mid-loop. Either can `print`. Left alone, that lands on the
    # same stream ahead of the object, `jq` fails, and a consumer reading the first line
    # gets an attacker-chosen one. Both sites need wrapping — covering only the import
    # makes a module-scope test pass while a printing tool still breaks the contract.
    #
    # Unconditional rather than only under `--json`: in human mode the answer is what
    # stdout is for, and a package's debug chatter interleaved with it is noise there
    # too. Nothing is lost — it is still on stderr.
    try:
        with contextlib.redirect_stdout(sys.stderr):
            pre = _preflight(args)
    except PreflightError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_PREFLIGHT

    as_json = args.json or pre.config.json
    if not args.quiet and not as_json:
        print(pre.render(), file=sys.stderr)
        print(file=sys.stderr)

    task = _read_task(args.task)

    if args.dry_run:
        # A wiring check, not a run: it says the package is loadable and this machine is
        # configured to run it. It cannot say whether the agent is any good, and must
        # not be described as if it could.
        result = Result(answer="", ledger=Ledger())
        return _emit(result, pre, task, as_json=as_json, quiet=args.quiet, dry_run=True)

    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = pre.agent.run(task, budget=pre.budget, goal_loop=not args.no_goal_loop)
    except Exception as exc:  # noqa: BLE001 — reported with an exit code, not a traceback
        # §5 promises one JSON object on stdout, and a consumer that has to parse stderr
        # prose to tell "the tool crashed" from "the agent failed" has no contract at
        # all. This path printed a stderr line and nothing on stdout.
        #
        # Not *every* exit path, and the exception is deliberate: a preflight failure
        # (exit 4) still prints nothing on stdout, because nothing was resolved yet —
        # there is no agent, no budget and no ledger to describe, and inventing an
        # object full of nulls would be a worse contract than none. `loop.run` catches its own
        # failures and keeps the ledger, so what reaches here spent nothing — hence an
        # empty Ledger rather than a lost one.
        reason = f"{type(exc).__name__}: {exc}"
        print(f"ERROR: the run failed: {reason}", file=sys.stderr)
        failed = Result(
            answer="", ledger=Ledger(), stopped_early=True, stop_reason=reason, stop_kind="error"
        )
        return _emit(failed, pre, task, as_json=as_json, quiet=args.quiet, dry_run=False)

    return _emit(result, pre, task, as_json=as_json, quiet=args.quiet, dry_run=False)


def _preflight(args: argparse.Namespace) -> Preflight:
    """Resolve, validate, and refuse to start if anything is missing.

    In the order `docs/execution.md` §2 specifies, because the order is the point: each
    step can only fail on something the previous one has already established.
    """
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError, TypeError, yaml.YAMLError) as exc:
        # `yaml.YAMLError` is not a `ValueError`, so an unparsable config file used to
        # reach `main()`'s catch-all and exit 3 — "stopped early: runtime error" — for
        # a run that never started. §5 reserves 4 for exactly this.
        raise PreflightError(f"{config_path(args.config)}: {exc}") from exc

    _check_auto_pull(args.ref, config)

    # §2's order, and the order matters: each step can only fail on something the
    # previous one established. Resolving the environment first meant a bad env file
    # masked a bad ref, so the message named the wrong problem.
    try:
        agent = AutoAgent.from_pretrained(args.ref, hub=effective_hub(config))
    except (ManifestError, RegistryError, FileNotFoundError, ValueError) as exc:
        raise PreflightError(f"could not load {args.ref!r}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        # A package's `tools.py` is arbitrary Python and can raise anything at import.
        # Left to `main()`'s catch-all it exited 3 naming neither the package nor the
        # file; loading is "did not start", which is 4.
        raise PreflightError(
            f"could not load {args.ref!r}: {type(exc).__name__}: {exc}"
        ) from exc

    _, env_source = _resolve_environment(args, config)


    missing = agent.spec.missing_environment()
    if missing:
        raise PreflightError(
            f"{agent.spec.name} needs {', '.join(missing)}, which "
            f"{'is' if len(missing) == 1 else 'are'} unset or still a placeholder. "
            f"Environment came from: {env_source}."
        )

    # Flags that cannot take effect are refused, not ignored. The external backend has
    # no goal loop and no packaged skills — `AutoAgent.run`'s own docstring says so —
    # and accepting the flag anyway means the run silently does the opposite of what
    # was asked. At preflight it costs nothing and names the reason.
    if agent.spec.framework != "teacup":
        # §6: dry-run answers "is this wired correctly and am I configured to run it?"
        # For this framework that answer used to skip the entire external path.
        from .external_cli import check_wiring

        try:
            check_wiring(agent.spec)
        except (ManifestError, ValueError) as exc:
            raise PreflightError(f"could not load {args.ref!r}: {exc}") from exc

        inapplicable = [
            name
            for name, given in (("--no-goal-loop", args.no_goal_loop), ("--skill", args.skill))
            if given
        ]
        if inapplicable:
            raise PreflightError(
                f"{', '.join(inapplicable)} does not apply to framework "
                f"{agent.spec.framework!r}, which runs its own loop. Remove the flag."
            )

    # After the framework refusal above, not before: run first, `--skill foo` against a
    # bridge package raised "could not enable skill 'foo'" — true, but naming the wrong
    # problem, and the reason the flag can never work is the thing worth saying.
    for skill in args.skill:
        try:
            agent.add_skill(skill)
        except Exception as exc:  # noqa: BLE001
            raise PreflightError(f"could not enable skill {skill!r}: {exc}") from exc

    # The override only. `set_model` used to be handed the fully resolved value —
    # including `spec.model_primary` when nobody asked for anything — and `auto.run`
    # forwards `self.model` to the external backend as `--model`. So every bridge run
    # started forcing the manifest's model onto the child, from a field that manifest
    # declares "informational only for this framework — the model that actually answers
    # is whatever the target checkout is configured to use". Fixing "the report names a
    # model that never ran" by making the wrong model run is not a fix.
    override = args.model or config.model
    if override:
        agent.set_model(override)

    # What to *report*. For the native loop the resolved model is the truth. For a
    # framework that picks its own, `None` is the truth, and §7 says so — a consumer
    # reading a name we invented is the failure this whole finding was about.
    if override or agent.spec.framework == "teacup":
        model = override or agent.spec.model_primary
    else:
        model = None

    budget = _resolve_budget(args, config, agent.spec)
    return Preflight(agent, args.ref, model, budget, env_source, config)


def _check_auto_pull(ref: str, config: Config) -> None:
    """Refuse to fetch a ref off the network unless the config says to.

    `hub.auto_pull` is documented as this gate, defaulting to false, on the grounds
    that a `run` which never reaches the network on its own is the safer default. It
    matters more than a convenience toggle: `registry.resolve` clones a git URL, and
    `AutoAgent.from_pretrained` then imports the clone's `tools.py` — so without the
    gate one shell command fetches and executes a stranger's Python, and `--dry-run`
    does it too, before deciding not to call a model.

    A ref already on disk or already in the hub is not a fetch and is not gated; this
    only stops the first, network-touching resolution.
    """
    if not ref.strip():
        # `Path("")` is `.`, so an empty ref quietly resolves to the current directory
        # and runs whatever agent happens to be sitting in it.
        raise PreflightError("no agent ref given")
    if config.auto_pull:
        return
    if Path(ref).expanduser().exists():
        return
    if not registry._is_git_url(ref):
        return  # a hub name: resolve() will look locally and error if it is absent
    raise PreflightError(
        f"{ref} would be fetched from the network, and fetching runs the package's own "
        f"Python. Clone it yourself and pass the path, or set `hub: {{auto_pull: true}}` "
        f"in your config to allow it. (`teacup pull` is not implemented yet — naming it "
        f"here would send you to an 'invalid choice' error.)"
    )


def _resolve_environment(args: argparse.Namespace, config: Config) -> tuple[Path | None, str]:
    """Load credentials, and report which source supplied them.

    Exactly one file is chosen — `--env-file`, else the config's `env_file`, else the
    cwd-upward search — while already-exported variables win per variable over whatever
    that file holds. A container that exports a key and also mounts a stale `.env` gets
    the exported one, which is what `load_env(override=False)` already does.
    """
    if args.env_file:
        # Typed now, so a missing file is an error: the caller named this path.
        path = Path(args.env_file).expanduser()
        if not path.is_file():
            raise PreflightError(f"env file not found: {path}")
        return load_env(path), f"{path} (--env-file)"

    if config.env_file is not None:
        # Ambient, and it travels: §4's whole argument for the config file is that it
        # is safe to commit to a dotfiles repo, so it is *expected* to land on machines
        # where this path does not exist and where the process environment supplies the
        # credentials instead (§3 rule 1: container, systemd, CI secrets). Fatal here
        # would brick `teacup run` on every one of those machines.
        path = config.env_file.expanduser()
        if not path.is_file():
            return None, f"process environment only ({path} from config is absent)"
        return load_env(path), f"{path} (config env_file)"

    used = load_env(search_cwd=not args.no_dotenv)
    if args.no_dotenv:
        return None, "process environment only (--no-dotenv)"
    if used is None:
        return None, "process environment only (no .env found)"
    return used, f"{used} (found by search)"


def _resolve_budget(args: argparse.Namespace, config: Config, spec: AgentSpec) -> Budget:
    """The settings chain (§4) applied to one field: flag, then config, then manifest.

    Config above manifest is what the document specifies, and the money reading agrees
    with it even though the key is named `defaults.budget_usd`: the person who owns the
    machine should be able to cap what a package they downloaded may spend, and a
    package that declares $5 should not be able to override a config that says $0.50.
    The naming is the confusing half — it is a default in the sense of "what this
    machine defaults to", not "what to fall back on".

    Only the dollar ceiling is overridable here; the tool-call and wall-clock ceilings
    stay the package's own, because a caller who set a budget has said something about
    money and nothing about the other two.

    What this does *not* do, stated so it is a decision rather than an oversight:
    `DEFAULT_BUDGET_USD` is a fallback, not a cap. With no config file — the first run
    of a freshly installed tool — a downloaded manifest declaring `budget.default_usd:
    999.00` applies as written, because §4 puts the manifest above built-in defaults
    and there is no config for it to override. That is the documented chain, so the
    code is right and the chain is the thing to argue with; meanwhile the user at least
    gets told, below, rather than finding out from an invoice.
    """
    from_manifest = False
    if args.budget is not None:
        usd = args.budget
    elif config.budget_usd is not None:
        usd = config.budget_usd
    elif spec.budget_usd is not None:
        usd, from_manifest = spec.budget_usd, True
    else:
        usd = DEFAULT_BUDGET_USD

    if from_manifest and usd > DEFAULT_BUDGET_USD:
        print(
            f"NOTE: {spec.name} asks for a ${usd:,.2f} ceiling, above the built-in "
            f"${DEFAULT_BUDGET_USD:,.2f}. Nothing on this machine caps it — pass "
            f"--budget, or set defaults.budget_usd in your config.",
            file=sys.stderr,
        )
    return Budget(
        usd=usd,
        max_tool_calls=spec.budget_max_tool_calls,
        deadline_s=spec.budget_max_wall_clock_s,
    )


def _read_task(task: str) -> str:
    """`-` means stdin, so notes can be piped in."""
    return sys.stdin.read() if task == "-" else task


def _exit_code(result: Result, dry_run: bool) -> int:
    if dry_run:
        return EXIT_OK
    if result.stop_kind == "budget":
        return EXIT_BUDGET
    if result.stop_kind == "error":
        return EXIT_ERROR
    if result.stopped_early:
        # A backend that stopped early without saying why. Reporting 0 here would tell
        # CI a timed-out or crashed run succeeded, which is the failure this table
        # exists to prevent — so an unclassified early stop is an error, not a success.
        return EXIT_ERROR
    if result.goal is not None and not result.goal.met:
        return EXIT_GOAL_NOT_MET
    return EXIT_OK


def _emit(
    result: Result, pre: Preflight, task: str, *, as_json: bool, quiet: bool, dry_run: bool
) -> int:
    code = _exit_code(result, dry_run)
    if as_json:
        # ensure_ascii=True, deliberately: with False, a task or answer containing a
        # non-ASCII character raises UnicodeEncodeError on any stdout that is not UTF-8
        # (PYTHONIOENCODING=ascii, a C-locale container), and the traceback exited 1 —
        # "completed, goal not met". `\uXXXX` is lossless and every JSON parser reads it.
        print(json.dumps(_payload(result, pre, task, code, dry_run)))
        return code

    if result.answer:
        print(result.answer)
    if not quiet and pre.config.ledger:
        print(file=sys.stderr)
        print(result.render_ledger(budget=pre.budget), file=sys.stderr)
        if dry_run:
            print(
                "\n(--dry-run: the package loaded and this machine is configured to run "
                "it. Nothing was asked of a model, so this says nothing about whether "
                "the agent is any good.)",
                file=sys.stderr,
            )
    return code


def _round_or_none(value: float | None, places: int = 6) -> float | None:
    return None if value is None else round(value, places)


def _payload(result: Result, pre: Preflight, task: str, code: int, dry_run: bool) -> dict[str, Any]:
    """The `--json` object, exactly as `docs/execution.md` §7 specifies it."""
    spec = pre.agent.spec
    ledger = result.ledger
    goal = result.goal
    return {
        "agent": {"name": spec.name, "version": spec.version, "ref": pre.ref},
        "model": pre.model,
        "task": task,
        "answer": result.answer,
        "goal": {
            # No verdict has two very different causes, and reporting `true` for both
            # would call a crashed run "goal met". A package with no checks that ran to
            # completion did meet its (empty) goal — that is the exit table's "goal met,
            # or no goal checks declared". A run that stopped early has no verdict
            # because it never got one, which is not the same as passing.
            # `dry_run` first: a wiring check evaluates nothing, and a consumer keying
            # on `goal.met` would otherwise read it as a pass. `dry_run: true` sits
            # beside it, but a field that is only safe when read with another field is
            # the shape of report this round kept finding.
            "met": False if dry_run else (goal.met if goal else not result.stopped_early),
            "checks": dict(goal.checks) if goal else {},
            "failed": list(goal.failed) if goal else [],
            "reasons": list(goal.reasons) if goal else [],
        },
        "attempts": result.attempts,
        "tool_calls": list(result.tool_calls),
        "cost": {
            "model": round(ledger.model_cost, 6),
            "tool": round(ledger.tool_cost, 6),
            "compute": round(ledger.compute_cost, 6),
            "total": round(ledger.total_cost, 6),
        },
        "usage": {
            "input_tokens": ledger.input_tokens,
            "output_tokens": ledger.output_tokens,
            "cached_input_tokens": ledger.cached_input_tokens,
        },
        "budget": {
            "usd": pre.budget.usd,
            # Rounded like the costs it is derived from: an unrounded float here reads
            # as 0.09999998284999748 for a budget nothing was spent against.
            "remaining": _round_or_none(pre.budget.remaining(ledger)),
        },
        "stopped": {
            "early": result.stopped_early,
            "kind": result.stop_kind,
            "reason": result.stop_reason,
        },
        "dry_run": dry_run,
        "elapsed_s": round(ledger.elapsed_s, 3),
        "exit_code": code,
    }
