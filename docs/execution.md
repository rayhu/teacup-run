# Executing an agent

**Status:** implemented (2026-09-08). This file remains the design; where it and
`src/teacup_run/cli.py` disagree, fix this file first and the code second.
**Scope:** the design of `teacup run`. The rule it implements — executing an
agent must not require writing Python — is stated in the [README](../README.md).
**Translation:** [中文版](execution.zh-CN.md). This file is the original; if the
two disagree, this one is right.

---

## 1. The command

```
teacup run <ref> <task>

  --budget USD          override the manifest budget
  --model NAME          override the manifest model
  --skill NAME          enable a packaged skill (repeatable)
  --no-goal-loop        single attempt, skip the outer loop
  --env-file PATH       explicit .env (§3)
  --no-dotenv           do not search the working directory for .env (§3)
  --config PATH         explicit config file (§4)
  --json                machine-readable result on stdout (§7)
  --dry-run             preflight and wire up; make no model call (§6)
  -q, --quiet           suppress the preflight echo and ledger
```

`<ref>` is a local path, a name in the hub, or a git URL — exactly what
`AutoAgent.from_pretrained` accepts. [`registry.resolve`](../src/teacup_run/registry.py)
already performs that resolution; the CLI is its second caller.

When `<task>` is `-`, the task is read from stdin, so notes can be piped in.

Later subcommands, each a thin wrapper over a function that already exists:
`pull` (`registry.clone`), `eval` (`AutoAgent.eval`), `publish`
(`AutoAgent.push_to_hub`), `inspect` (print the manifest, tools, checks, skills,
budget). None is needed for `run`.

## 2. Preflight

Every run, in this order:

1. Resolve the ref to a package directory.
2. Load and validate the manifest.
3. Resolve the environment (§3).
4. Check `environment.required` — every name present and not a placeholder.
5. Echo agent name and version, model, budget, tools, skills, and **which source
   supplied the environment**, so "why did it use that key" is answerable without
   a debugger.
6. Only then call the model.

Steps 4 and 5 exist so that a missing key fails before any spend rather than
401-ing mid-run. [`env.py:17`](../src/teacup_run/env.py#L17) already rejects
placeholder values (`sk-...`, `your-key-here`); absent values are the case it
does not yet cover, and the one every new user hits.

**Step 4 must not move into `AgentSpec.validate()`.** That runs inside
`from_pretrained()`, and the test suite loads `examples/note-taker` with a faked
model and no key at all — `env -u OPENAI_API_KEY pytest` passes today. Enforcing
there would make the library unusable offline. Preflight calls a separate query:

```python
def missing_environment(self) -> tuple[str, ...]:
    """Declared environment variables that are absent or still placeholders."""
```

## 3. Environment

The CLI never reads a `.env` from inside an agent package. A `.env` is never
committed and never ships — [`.gitignore`](../.gitignore) ignores it at any
depth and [`registry.publish`](../src/teacup_run/registry.py) strips it via
`ignore_patterns(*NOT_PUBLISHED)`, whose first entry is `.env` — so a package that depended on
one would break the moment somebody published it. Credentials belong to the
environment an agent runs in, not to the artifact. A package declares only the
*names* it needs, through `environment.required`.

Where the CLI looks instead:

| # | Source | Intended for |
|---:|---|---|
| 1 | Already-exported process environment | production: container, systemd, CI secrets |
| 2 | `--env-file PATH` | explicit, one invocation |
| 3 | `env_file:` in the config file (§4) | a released, installed tool |
| 4 | `.env` found by searching the working directory upward | local development only |

The two kinds resolve differently, and an implementation must keep them apart:
**exactly one file is chosen** (2, else 3, else 4), while **rule 1 wins per
variable** over whatever that file supplies. A container that exports
`OPENAI_API_KEY` and also mounts a `.env` holding a stale one gets the exported
value. `load_env(override=False)` already behaves this way.

Rule 4 is [`load_env()`](../src/teacup_run/env.py)'s existing cwd-upward search,
unchanged, and disabled by `--no-dotenv`. It is a development affordance, not the
mechanism.

**Follow-up:** [`.gitignore`](../.gitignore) whitelists `.env.example` and no such
file exists. That is where the variable names belong — committed, valueless, and
the thing a new contributor copies.

## 4. Configuration

`~/.config/teacup/config.yaml`, following XDG. YAML, matching `agent.yaml` and
`benchmark.yaml`. Overridden by `TEACUP_CONFIG`, or per-invocation by
`--config PATH`. Absent is a valid state: every key has a default, and the CLI
must work with no config file at all. The hub cache stays where
[`hub_path()`](../src/teacup_run/registry.py) puts it, `~/.teacup/agents`.

```yaml
# ~/.config/teacup/config.yaml — settings, never secrets.
env_file: ~/.config/teacup/secrets.env

defaults:
  budget_usd: 1.00
  model: null          # null: whatever the manifest asks for

hub:
  path: ~/.teacup/agents
  auto_pull: false

output:
  ledger: true
  json: false
```

**The config file holds no secret values, only a pointer to where secrets live.**
That is what keeps it safe to commit to a dotfiles repository, which is where a
file like this ends up whether or not we intend it to.

Settings precedence, highest first: CLI flags, then `TEACUP_*` environment
variables (`TEACUP_HOME` already exists and must keep winning over
`hub.path`), then the config file, then the agent's manifest, then built-in
defaults.

Two consequences of that order, both found by review after being asserted here and
not implemented:

- `TEACUP_HOME` beating `hub.path` needs code, not just this sentence.
  `registry.resolve` does `hub = hub or hub_path()`, so passing it *any* path is what
  silences the environment — `config.effective_hub()` returns `None` when `TEACUP_HOME`
  is set, and `None` is how "let the environment decide" is spelled. The sample above
  is exactly the config that made this matter.
- Built-in defaults being **last** means `DEFAULT_BUDGET_USD` is a fallback, not a cap.
  With no config file, a downloaded manifest's `budget.default_usd` applies as written,
  however large — there is nothing above it in the chain to override it. The CLI prints
  a note to stderr when a manifest asks for more than the built-in default; capping it
  outright would be a different rule than this one, and would need changing here first.

Unknown keys in this file are an error, not a shrug. A dropped key fails permissively —
`defualts: {budget_usd: 0.05}` leaves you believing spend is capped while the package's
own ceiling applies — and a settings file whose job is to constrain someone else's code
cannot silently ignore the constraint.

This chain orders *settings*; §3 orders *credentials*. They must not be merged —
a config file that could set `OPENAI_API_KEY` directly would undo §3.

## 5. Output and exit codes

- **stdout** — `result.answer`, and nothing else.
- **stderr** — the preflight echo and the cost ledger.
- `--json` — one JSON object on stdout, and *nothing* else on stdout. One exception,
  deliberate: a **preflight failure (exit 4) prints nothing on stdout**, because
  nothing has been resolved yet — there is no agent, budget or ledger to describe, and
  an object full of nulls would be a worse contract than none. Every other exit path,
  including a failed run, emits the object.

So `teacup run ... > answer.txt` leaves a clean file with the ledger still on
the terminal, and `teacup run ... --json | jq .cost.total` works.

| Code | Meaning |
|---:|---|
| 0 | Completed; goal met, or no goal checks declared |
| 1 | Completed; goal not met |
| 2 | Stopped early: ran out of an allowance — dollars, tool calls, turns, or wall clock; whether you set it or it is the built-in default |
| 3 | Stopped early: runtime error |
| 4 | Did not start: bad ref, invalid manifest, or missing environment |

Codes 2 and 3 needed a library change. [`loop.py`](../src/teacup_run/loop.py)
collapsed both into one string — `BudgetExceeded` set `stop_reason` to
`exc.reason`, a generic exception set it to `f"{type(exc).__name__}: {exc}"` —
and telling them apart by parsing that string is a smell. `Result` got a
discriminator:

```python
stop_kind: str | None = None   # "budget" | "error" | None
```

Both backends must agree on it. A ceiling is 2 whichever loop hit it: the native
loop raises `BudgetExceeded` for dollars, tool calls, turns and wall clock alike,
and the external backend maps the child's `out_of_budget` / `out_of_time` /
`max_steps` to the same kind. Anything else is 3. The failure this prevents is the
same manifest changing exit code because of its `framework:` key.

## 6. `--dry-run`

Everything except the model call: resolve, validate, preflight, import `tools.py`
and `checks.py`, build the tool schemas, render the ledger with zeroes. It answers
"is this package wired correctly and am I configured to run it?" for no key and
no spend.

For a `framework != "teacup"` package that means the external path too — the
entrypoint, and that `teacup_agent.project_root` names a directory that exists.
Checking none of it and still answering "configured to run it" is worse than not
answering, and it is what shipped first: a manifest missing `project_root` passed
dry-run with exit 0 and then failed the real run.

It is a wiring check, not a run, and must be described as one — it cannot say
whether the agent is any good. A recorded or stubbed model (`--replay`, built on
the existing `model_fn` seam) is a separate feature.

## 7. `--json` shape

```json
{
  "agent":   {"name": "teacup/note-taker", "version": "0.1.0", "ref": "examples/note-taker"},
  "model":   "gpt-5-mini",          // null when the framework picks its own
  "task":    "Notes: ...",
  "answer":  "Action items\n- Ray: ...",
  "goal":    {"met": true, "checks": {"non_empty": true}, "failed": [], "reasons": []},
  "attempts": 1,
  "tool_calls": ["save_action_item", "list_action_items"],
  "cost":    {"model": 0.0031, "tool": 0.02, "compute": 0.01, "total": 0.0331},
  "usage":   {"input_tokens": 1840, "output_tokens": 220, "cached_input_tokens": 0},
  "budget":  {"usd": 0.25, "remaining": 0.2169},
  "stopped": {"early": false, "kind": null, "reason": null},
  "dry_run": false,
  "elapsed_s": 7.4,
  "exit_code": 0
}
```

`model` is the *resolved* model for the native loop, and `null` for a framework that
chooses its own — `framework: teacup-agent-cli` runs whatever the target checkout is
configured to use, so naming the manifest's `model.primary` there would be reporting a
model that never ran. A `--model` override is forwarded and reported on both paths.

`goal.met` is `false` for a `--dry-run`, which evaluated nothing.

Every field reads off `Result`, `GoalVerdict`, `Ledger` and `Budget` except one:
`dry_run`, which is not on `Result` at all because a dry run never produces one.
(Two others were exceptions when this was written and are not any more —
`stopped.kind` is a field on `Result` and `budget.remaining` is a method on
`Budget`, both added by this change.) Without it `--json --dry-run` is indistinguishable
from a real run that returned an empty answer at zero cost, which is exactly the
confusion §6 warns about — a wiring check must not be mistakable for a run.

## 8. Implementation plan

| # | Change | Files |
|---:|---|---|
| 1 | `stop_kind` on `Result`, set at both `except` sites | `src/teacup_run/loop.py` |
| 2 | `AgentSpec.missing_environment()` | `src/teacup_run/manifest.py` |
| 3 | `Budget.remaining(ledger)` — lift it out of `Ledger.render` | `src/teacup_run/budget.py` |
| 4 | Make `load_env`'s cwd search skippable; it already takes an explicit path and returns the file it used, which preflight echoes | `src/teacup_run/env.py` |
| 5 | Config loader: read, defaults, precedence (§4) | `src/teacup_run/config.py` *(new)* |
| 6 | ~~Delete the unused `entrypoint` field~~ — **superseded**: kept and repurposed as the base command for a non-native framework backend (`framework != "teacup"`). See `docs/backends.md` and `src/teacup_run/external_cli.py` | `src/teacup_run/manifest.py` |
| 7 | `cli.py`: arg parsing, preflight, run, render, exit codes, `--json` | `src/teacup_run/cli.py` *(new)* |
| 8 | `[project.scripts] teacup = "teacup_run.cli:main"` | `pyproject.toml` |
| 9 | `.env.example` with the variable names, no values | repository root |
| 10 | Tests: preflight failures, exit codes, JSON shape, `--dry-run` | `tests/test_cli.py` *(new)* |

Items 1–5 are small and independently useful; item 7 is the bulk. Item 6's original
plan assumed `entrypoint` was dead weight — it wasn't; it's now the one field a
non-native framework needs to say how it's invoked. `AgentSpec.to_dict` returning
`dict(self.raw)` already meant an `entrypoint:` key round-tripped through publish
either way, so nothing here changes that guarantee.

Tests use the existing `model_fn` seam, so the CLI suite runs with no key and no
spend, like the rest of the suite.

## 9. Open questions — answered

1. **Auto-pull** follows `hub.auto_pull`, default false. The key was already in
   §4's config schema, so the design had answered this and the question was
   redundant; a `run` that never reaches the network on its own is the safer
   default, and someone who wants the friendlier behaviour opts into it once.
2. **Exit code 1 for "goal not met" stays.** An agent that declared checks and
   failed them did not do the job, and §5's table is the contract — changing it
   invents a different one. `--strict` can be added later without breaking
   anything, which is the asymmetry that settles it: keeping 1 is reversible,
   shipping 0 and changing it later is not.
