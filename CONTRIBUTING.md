# Contributing

Teacup Run is an early-stage, experimental project. APIs and terminology are still
expected to move. This file is a map of where things live and what's actually checked,
not a formal process. What the project is *for* — the capabilities it owes, the invariants
a fork must keep, and the criteria a change can fail — is [`docs/intent.md`](docs/intent.md);
read that before proposing something that changes the shape of the thing.

## Before anything else

```bash
uv sync --extra dev
uv run pytest             # sandbox, coding_task, manifest, budget, model, tools
```

Every test here is hermetic: a throwaway git-repo fixture, `tests/fixtures/fake_cli.py`,
`live=False` everywhere. No API key, no network, no cost — which is also why CI can run
it on every pull request.

## Where things live

| You want to | Change this |
| --- | --- |
| Support a new external agent framework | a new backend module (`external_cli.py` + `sandbox.py` is the reference: `docs/backends.md` explains the pattern) |
| Change how a coding task is driven | `coding_task.py` |
| Change the manifest / package format | `manifest.py` |
| Change budget or cost accounting | `budget.py` |
| Add or change a model backend | `model.py` |
| Change the native control loop | `loop.py`, `goal.py` |

`docs/backends.md` and `docs/execution.md` cover the two most likely places a change
touches — read the relevant one before proposing a new backend or a new execution mode.

## The rules that are not negotiable

Each of these was paid for by a real bug, most of them documented in
[teacup-agent's `docs/case-studies.md`](https://github.com/rayhu/teacup-agent/blob/main/docs/case-studies.md)
from actually driving a coding agent through this repo's own sandbox:

1. **`sandbox.py` requires an explicit `cwd`.** An earlier version defaulted to a
   throwaway scratch directory, which silently broke anything the launched program
   resolves relative to its own working directory.
2. **The sandboxed child's `stdin` is always severed (`subprocess.DEVNULL`).**
   Leaving it at the default meant a child launched from a real terminal inherited that
   terminal's TTY, and an unattended run could hang indefinitely on a prompt nobody was
   there to answer.
3. **A resource limit that can't be honored on this platform is skipped, not fatal.**
   `resource.setrlimit` can fail outright on macOS; `_preexec_limits` must never let that
   abort the whole launch.
4. **Nothing is pushed, merged, or committed to the target repo automatically.**
   `run_coding_task` stops at a reviewable local branch and worktree — a human decides
   what happens to it next, every time.

## Things this project deliberately does not do

See "What Teacup Run Is Not" in the README before proposing an enterprise control plane,
an RBAC system, or another orchestration DSL. The first problem being solved is simpler:
making agents as easy to reuse, modify, evaluate, and share as pretrained models.

## If your change is behavioural

Measure it. If you're touching `budget.py`, `sandbox.py`'s resource limits, or anything
that changes what a run actually costs or how long it takes, show a before/after number,
not just a description of the intent.

## Working with a coding agent on this repo

There's no `AGENTS.md` here yet — if you fork this and drive it with an agent, the
closest reference for what one looks like is
[teacup-agent's own `AGENTS.md`](https://github.com/rayhu/teacup-agent/blob/main/AGENTS.md),
which this project's own coding-task pipeline (`coding_task.py`) has actually been used
against, repeatedly, with the results written down.

CI (`.github/workflows/verify.yml`) runs `uv run pytest` on every pull request and on
pushes to `main`. On a feature branch with no pull request open yet, nothing checks it
but you.
