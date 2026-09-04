# Framework backends: running something that isn't Teacup Run's own loop

**Status:** one backend shipped — `framework: teacup-agent-cli`, a sandboxed
subprocess launcher for a [teacup-agent](https://github.com/rayhu/teacup-agent)
checkout. This file is the design record for it, and the pattern the next
backend should follow.

## Why this exists

The README has said since v0.1: "Existing frameworks should become backends,
not competitors." The manifest already carried a `framework:` field for exactly
this, but nothing branched on it — every resolved package ran through Teacup
Run's own loop regardless. `AutoAgent.run()` (`auto.py`) now does branch: when
`spec.framework != "teacup"`, it hands off to that framework's backend instead
of the native loop. `teacup-agent-cli` (`external_cli.py` + `sandbox.py`) is the
first one.

## Why a subprocess, not the network

teacup-agent ships an Agent2Agent (A2A) client and server
(`delegate_a2a`, `teacup-agent-serve`) — a real option for calling into a
*running* agent. It wasn't used here because this integration's job is
different: **execute** a task and get a result back, once, then the process is
gone. Spinning up an HTTP server, allocating a port, and tearing it down again
for a single task is overhead a plain subprocess doesn't have — and
teacup-agent's own roadmap frames its A2A server as a deliberate, narrow
exception to "no service layer," not a pattern to build more traffic through.
A future backend that genuinely wants a long-lived peer relationship (multiple
tasks against a warm process, streaming, cancellation) is a legitimate reason
to reach for A2A instead; batch execution is not.

## Why a bridge package, not a shared `agent.yaml`

Teacup Run's manifest loader looks for a file literally named `agent.yaml`
(`MANIFEST_NAME`, `manifest.py`) with a schema — `name`/`version`/`model.primary`/
`goal`/... — built for a native, in-process package. teacup-agent has grown its
own, unrelated `agent.yaml` (`models.profiles`/`runtime`/`a2a`/...), read by its
own `agent_config.py`. The two are not compatible, and cannot be the same file.

The fix is [`examples/teacup-agent-bridge/`](../examples/teacup-agent-bridge): a
small directory holding *Teacup Run's* `agent.yaml`, physically separate from
whatever teacup-agent checkout it points at. `AutoAgent.from_pretrained(...)`
resolves the bridge directory, never the target checkout. The pointer — where
the real project lives — rides in a `teacup_agent:` block that isn't part of the
formal schema at all; it survives because `AgentSpec.raw` keeps the whole parsed
YAML verbatim (`manifest.py`), so `external_cli.py` reads
`spec.raw["teacup_agent"]["project_root"]` directly rather than teaching
`manifest.py` about a field only one framework needs.

`entrypoint` (`AgentSpec.entrypoint`) is the other piece this reuses rather than
invents: an earlier draft of `docs/execution.md` planned to delete it as unused.
It wasn't unused, it was unfinished — it's now the base command a non-native
backend shells out to (default: `"uv run teacup-agent"`).

## Why the sandboxed process still needs a real project directory

teacup-agent's own CLI resolves `--mcp`/`--skills`/`--memory` defaults (and would
resolve `--config`) relative to its process's cwd. Changing that cwd to a
throwaway scratch directory would break all of it. An earlier version of this
backend assumed `uv run --project <root>` handled this — it does not:
verified empirically that `--project` only affects dependency/venv resolution,
not the subprocess's working directory, so a launch with no explicit `cwd`
left teacup-agent's `read_file`, `./mcp.json` and `./skills` discovery
resolving against `sandbox.py`'s own scratch directory instead of the real
project, silently. `run_sandboxed()` now takes a **required** `cwd` keyword-
only parameter instead — `external_cli.py` passes the target project's real
root explicitly, so there is no implicit fallback left to get wrong.
`external_cli.py`'s `_build_argv` still inserts `--project` right after a
`uv run ...`-shaped entrypoint (it's still needed for dependency/venv
resolution), but `cwd` is what actually fixes what a launched program sees
when it resolves a relative path; a future non-`uv` framework would still
need `cwd` set correctly, `--project` insertion or not.

`--run-dir` and `--memory` are still pointed at teacup-run's own scratch
directory, not the target checkout — otherwise concurrent invocations would
collide and the checkout would accumulate run artifacts across every call.

## What `sandbox.py` actually bounds

Not filesystem isolation — the child needs its own real project directory, so
this was never going to be a jail. What it does bound, cross-platform (Mac,
Windows, Linux, stdlib only, no Docker):

| Property | Mechanism |
|---|---|
| Credential/env scope | Only `env_allowlist` (the manifest's `environment.required`, resolved from teacup-run's own environment) plus a minimal base (`PATH`, `HOME`, and the Windows variables an interpreter needs) reaches the child |
| Lifetime | A wall-clock `timeout`, enforced with a full process-tree kill — `os.killpg` on POSIX, `taskkill /T /F` on Windows — not just the top process. Plain `Popen.kill()` would leave `uv run`'s own child running past the deadline |
| Resources | Best-effort, POSIX only: a 1 GiB address-space `resource.setrlimit`. `SandboxResult.limits_applied` says `False` on Windows rather than silently claiming parity |

**Deliberately not attempted in this version: network egress control.** That
needs a container or an OS firewall rule. A credential-scoped, time-bounded
subprocess that can still make arbitrary outbound calls is a real, stated
limitation, not an oversight — see the "Docker or Seatbelt is the wrong first
question" framing in teacup-agent's own `docs/roadmap.md` #14 for the same
distinction applied to its own threat model: isolation, reproducibility, and
credential/egress scope are different axes, and this backend only buys the
first and the credential half of the third.

## The two layers of "how long can this run"

teacup-agent's own `--deadline` is a soft brake — hitting it triggers a
*forced wrap-up* inside the loop, so a run that's about to be cut off still
produces an answer and prints its `--json` line. `sandbox.py`'s `timeout` is a
hard kill from outside, with no cooperation from the child required. So
`external_cli.py` sets the sandbox timeout to `deadline + 30s`: teacup-agent
gets its own graceful exit first, and the hard kill is a backstop for a run
that ignores its deadline entirely (a wedged network call, say), not the
common path.

## The result contract

teacup-agent's `--json` (its own `docs/integration.md`) is the one thing this
backend parses: one JSON object on stdout, nothing else. `external_cli.py`
maps it onto Teacup Run's own `Result` — `answer`, `stop_reason` (`None` when
`status == "done"`), and one lump-sum ledger line (`budget - remaining_budget`),
since teacup-agent doesn't decompose cost into model/tool/compute the way
Teacup Run's own `Ledger` does. That's less detail than a native run's ledger
carries, and it's left that way rather than fabricated.

## Why `--config agent.yaml` is not used here

teacup-agent's declarative `--config` path always builds a real, billable model
(`_main_config()` hardcodes `live=True` — "a config run is always real"). Routing
every sandboxed invocation through it would make this backend impossible to
test without spending money. `external_cli.py` uses teacup-agent's plain-flags
CLI instead, where `--live` is optional: omitting it runs teacup-agent's free,
instant, scripted offline demo, which is what `live=False` here and every
hermetic test use. The corollary: this backend does not currently pass through
model choice, MCP config, or skills — it relies on whatever the target
checkout's own defaults are (or a `--live` run picks up its own `mcp.json`/
`skills/` the same way a human invocation would). Threading those through is a
natural next step, not done in this round.

## Coding tasks: `coding_task.py` (Phase 3)

`run_external` proved the plumbing — one task string in, one JSON answer out.
`coding_task.run_coding_task(spec, task, *, target_repo, ...)` is the layer on
top that actually lets teacup-agent **change** a repo, not just answer a
question about it, without ever touching that repo's primary checkout:

- **Every task gets its own disposable `git worktree` + branch**, cut from
  `base_branch` (default `"main"`) inside `target_repo`. A bad run costs
  "delete a worktree," never "recover a working tree someone else is using" —
  the load-bearing decision this whole engagement made before writing any of
  Phase 0–2.
- **`target_repo` decouples "what the agent operates on" from `project_root`.**
  `run_external` already conflated the two — `project_root` (where teacup-
  agent's own code/deps live, for `uv run --project`) happened to always equal
  the sandbox `cwd` (what gets operated on). `run_external` now takes an
  optional `target_repo` that becomes `cwd` instead, defaulting to
  `project_root` so a plain call is unaffected. `coding_task.py` always passes
  the worktree path, so the same teacup-agent checkout — and the same bridge
  package — can drive a task against **any** target repo (teacup-run's own
  source, say), not only wherever `project_root` points.
- **`--coding-tools --approve hooks` are always added** (`extra_flags` on
  `run_external`) — a coding task with neither would only ever produce an
  answer, never a change. `--hooks` is deliberately *not* passed explicitly:
  teacup-agent's CLI auto-discovers `./hooks.py` relative to its cwd, which is
  now the worktree, so a target repo's own committed `hooks.py` (if it has
  one — teacup-agent's or teacup-run's own, once Phase 4 ships one in each) is
  checked out into the worktree by `git worktree add` and picked up for free.
  No `hooks.py` in the target repo means every gated call is denied without a
  TTY, exactly as it should be — a coding task producing no side effects on an
  un-configured repo is the correct outcome, not a bug.
- **What changed is always reported, committed or not.** teacup-agent's own
  coding tools (`list_files`/`edit_file`/`write_file`/`run_command`) never
  commit anything themselves, but the model can `git commit` via
  `run_command` if a target repo's `hooks.py` allows it — so `_collect_diff`
  reports both: uncommitted changes (`git status --porcelain`, tracked and
  untracked) and commits made on the branch (`git log base_branch..HEAD`).
  `CodingTaskResult.diff_stat` is the human-readable summary of both;
  `files_changed` is the flat list a caller can act on.
- **A target repo's own tests are opt-in, on purpose left unguessed.** There
  is no reliable, language-agnostic way to infer "the test command" from a
  repo alone, so `run_tests=True` with no `test_command` is a deliberate
  no-op (`tests_passed=None`) rather than a guess dressed up as a result.
  Passing `test_command="uv run pytest"` (or whatever the target repo uses)
  runs it inside the worktree through `sandbox.run_sandboxed` — the same
  real, cross-platform process-tree-kill-on-timeout mechanism `run_external`
  itself uses, not a second, weaker implementation. It is `shlex.split` and
  run **without a shell** — one command, no `&&`/`;`/pipes/env-var prefixes
  (wrap it yourself, `test_command='bash -c "make check && make test"'`, if
  you need those) — and a command whose executable doesn't exist reports as
  a failed test run (`tests_passed=False`, an `ERROR:` in `test_output`)
  rather than crashing `run_coding_task` and discarding an already-produced
  result over an unrelated test-runner typo.
- **Never commits on the caller's behalf, never pushes, never opens a PR.**
  `coding_task.py` has no code path that does any of the three. It stops at
  "a reviewable local branch, with a diff and a test result attached" — the
  same human-gated stopping point every round of this engagement has used by
  hand, now built into the module itself rather than a habit to remember.
- **The worktree is left in place, not cleaned up.** It's the reviewable
  artifact — `CodingTaskResult.worktree_path` is where a human looks. Cleanup
  (`git worktree remove`) is the caller's job once a branch has been reviewed
  and either kept or discarded.

## What's deferred

- An A2A-based backend, for the different job A2A is actually for (see above).
- Passing model/MCP/skills selection through to the sandboxed invocation.
- A Docker (or similar) backend for real filesystem/network isolation, for a
  framework that needs more than this one buys.
- Teacup Run's own `teacup run <ref> <task>` CLI (`docs/execution.md`) — this
  backend only needed `AutoAgent.run()` to dispatch correctly; a user-facing
  CLI on top is separate, larger, already-scoped work.
- Automatic worktree cleanup, and any notion of "coding task queue" beyond
  one call, one worktree, one task — concurrent coding tasks are out of
  scope (teacup-agent's own tool registry is process-global, the same
  limitation already documented for its A2A server).
- Auto-push / auto-PR from inside `coding_task.py` — stays human-gated,
  matching every round so far, not a missing feature.
