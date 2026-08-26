# Executing an agent

**Status:** proposal, awaiting review. Nothing here is implemented yet.
**Scope:** the design of `opengraft run`. The rule it implements — executing an
agent must not require writing Python — is stated in the [README](../README.md).
**Translation:** [中文版](execution.zh-CN.md). This file is the original; if the
two disagree, this one is right.

---

## 1. The command

```
opengraft run <ref> <task>

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
`AutoAgent.from_pretrained` accepts. [`registry.resolve`](../src/opengraft/registry.py)
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
401-ing mid-run. [`env.py:17`](../src/opengraft/env.py#L17) already rejects
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
depth and [`registry.publish`](../src/opengraft/registry.py) strips it via
`ignore_patterns("__pycache__", "*.pyc", ".env")` — so a package that depended on
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

Rule 4 is [`load_env()`](../src/opengraft/env.py)'s existing cwd-upward search,
unchanged, and disabled by `--no-dotenv`. It is a development affordance, not the
mechanism.

**Follow-up:** [`.gitignore`](../.gitignore) whitelists `.env.example` and no such
file exists. That is where the variable names belong — committed, valueless, and
the thing a new contributor copies.

## 4. Configuration

`~/.config/opengraft/config.yaml`, following XDG. YAML, matching `agent.yaml` and
`benchmark.yaml`. Overridden by `OPENGRAFT_CONFIG`, or per-invocation by
`--config PATH`. Absent is a valid state: every key has a default, and the CLI
must work with no config file at all. The hub cache stays where
[`hub_path()`](../src/opengraft/registry.py) puts it, `~/.opengraft/agents`.

```yaml
# ~/.config/opengraft/config.yaml — settings, never secrets.
env_file: ~/.config/opengraft/secrets.env

defaults:
  budget_usd: 1.00
  model: null          # null: whatever the manifest asks for

hub:
  path: ~/.opengraft/agents
  auto_pull: false

output:
  ledger: true
  json: false
```

**The config file holds no secret values, only a pointer to where secrets live.**
That is what keeps it safe to commit to a dotfiles repository, which is where a
file like this ends up whether or not we intend it to.

Settings precedence, highest first: CLI flags, then `OPENGRAFT_*` environment
variables (`OPENGRAFT_HOME` already exists and must keep winning over
`hub.path`), then the config file, then the agent's manifest, then built-in
defaults.

This chain orders *settings*; §3 orders *credentials*. They must not be merged —
a config file that could set `OPENAI_API_KEY` directly would undo §3.

## 5. Output and exit codes

- **stdout** — `result.answer`, and nothing else.
- **stderr** — the preflight echo and the cost ledger.
- `--json` — one JSON object on stdout, and *nothing* else on stdout.

So `opengraft run ... > answer.txt` leaves a clean file with the ledger still on
the terminal, and `opengraft run ... --json | jq .cost.total` works.

| Code | Meaning |
|---:|---|
| 0 | Completed; goal met, or no goal checks declared |
| 1 | Completed; goal not met |
| 2 | Stopped early: budget exceeded |
| 3 | Stopped early: runtime error |
| 4 | Did not start: bad ref, invalid manifest, or missing environment |

Codes 2 and 3 need a library change. [`loop.py`](../src/opengraft/loop.py)
currently collapses both into one string — `BudgetExceeded` sets `stop_reason` to
`exc.reason`, a generic exception sets it to `f"{type(exc).__name__}: {exc}"` —
and telling them apart by parsing that string is a smell. `Result` gets a
discriminator:

```python
stop_kind: str | None = None   # "budget" | "error" | None
```

## 6. `--dry-run`

Everything except the model call: resolve, validate, preflight, import `tools.py`
and `checks.py`, build the tool schemas, render the ledger with zeroes. It answers
"is this package wired correctly and am I configured to run it?" for no key and
no spend.

It is a wiring check, not a run, and must be described as one — it cannot say
whether the agent is any good. A recorded or stubbed model (`--replay`, built on
the existing `model_fn` seam) is a separate feature.

## 7. `--json` shape

```json
{
  "agent":   {"name": "opengraft/note-taker", "version": "0.1.0", "ref": "examples/note-taker"},
  "model":   "gpt-5-mini",
  "task":    "Notes: ...",
  "answer":  "Action items\n- Ray: ...",
  "goal":    {"met": true, "checks": {"non_empty": true}, "failed": [], "reasons": []},
  "attempts": 1,
  "tool_calls": ["save_action_item", "list_action_items"],
  "cost":    {"model": 0.0031, "tool": 0.02, "compute": 0.01, "total": 0.0331},
  "usage":   {"input_tokens": 1840, "output_tokens": 220, "cached_input_tokens": 0},
  "budget":  {"usd": 0.25, "remaining": 0.2169},
  "stopped": {"early": false, "kind": null, "reason": null},
  "elapsed_s": 7.4,
  "exit_code": 0
}
```

Every field reads off `Result`, `GoalVerdict`, `Ledger` and `Budget` except two:
`stopped.kind` from §5, and `budget.remaining`, which today exists only as an
expression inside [`Ledger.render`](../src/opengraft/budget.py#L158).

## 8. Implementation plan

| # | Change | Files |
|---:|---|---|
| 1 | `stop_kind` on `Result`, set at both `except` sites | `src/opengraft/loop.py` |
| 2 | `AgentSpec.missing_environment()` | `src/opengraft/manifest.py` |
| 3 | `Budget.remaining(ledger)` — lift it out of `Ledger.render` | `src/opengraft/budget.py` |
| 4 | Make `load_env`'s cwd search skippable; it already takes an explicit path and returns the file it used, which preflight echoes | `src/opengraft/env.py` |
| 5 | Config loader: read, defaults, precedence (§4) | `src/opengraft/config.py` *(new)* |
| 6 | Delete the unused `entrypoint` field from `AgentSpec` | `src/opengraft/manifest.py` |
| 7 | `cli.py`: arg parsing, preflight, run, render, exit codes, `--json` | `src/opengraft/cli.py` *(new)* |
| 8 | `[project.scripts] opengraft = "opengraft.cli:main"` | `pyproject.toml` |
| 9 | `.env.example` with the variable names, no values | repository root |
| 10 | Tests: preflight failures, exit codes, JSON shape, `--dry-run` | `tests/test_cli.py` *(new)* |

Items 1–6 are small and independently useful; item 7 is the bulk. Item 6 costs
nothing in compatibility — `AgentSpec.to_dict` returns `dict(self.raw)`, so an
`entrypoint:` key in an existing `agent.yaml` still round-trips through publish.

Tests use the existing `model_fn` seam, so the CLI suite runs with no key and no
spend, like the rest of the suite.

## 9. Open questions

1. **Should `run` auto-pull an unresolved ref**, or require `opengraft pull`
   first? Auto-pull is friendlier; explicit pull means `run` never reaches the
   network on its own.
2. **Exit code 1 for "goal not met"** treats an honest, completed, under-budget
   run as a failure. Correct for CI, possibly surprising interactively. Keep, or
   gate behind `--strict`?
