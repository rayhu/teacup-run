# Roadmap

What is missing, in the order it should be built. Same shape as teacup-agent's
`docs/roadmap.md`: **Now** (what the code does today), **What to change**, and a
**Definition of done** that can be checked rather than argued about.

**Progress**: the library half is built — `AutoAgent`'s four verbs
(`from_pretrained` / `add_skill` / `run` / `eval` / `push_to_hub`), a hub that is a
directory and optionally git with no server, budgeted evaluation, goal checks, the
sandboxed subprocess launcher, and `run_coding_task`. What is missing is mostly the
seams between those pieces, and the one verb that still requires writing Python.

The dividing line with teacup-agent, stated once because it is what keeps both repos
honest: **teacup-agent is one agent you can read and fork; teacup-run is the ecosystem
around many agents.** A feature that would make teacup-agent a platform belongs here.
Its own `AGENTS.md` says the same thing from the other side ("the value of this repo is
that the ~80-line control loop fits in one head").

---

### 1. `teacup run` — the CLI

**Now**: there is none. `pyproject.toml` declares no `[project.scripts]`, and running an
agent means importing `AutoAgent` in Python — which contradicts the rule the README
states as the reason this project exists ("executing an agent must not require writing
Python").

**What to change**: [`docs/execution.md`](execution.md) is the design and it is complete
— command surface, preflight order, the credential-vs-settings split, config file,
output and exit codes, `--dry-run`, the `--json` shape, and a ten-item implementation
table. It has been reviewed and needs no redesign. Its own status line ("Nothing here is
implemented yet") is what this item removes.

Outstanding from that table: `stop_kind` on `Result` (1), `AgentSpec.missing_environment()`
(2), `Budget.remaining()` lifted out of `Ledger.render` (3), a way to skip `load_env`'s
cwd-upward search (4 — it already takes an explicit path and returns the file it used),
`config.py` (5), `cli.py` (7), `[project.scripts]` (8), `.env.example` (9), tests (10).
Item 6 is superseded: `entrypoint` turned out to be the field a non-native framework
needs, not dead weight.

The two open questions in §9 are answered by the design itself and should be recorded
rather than re-litigated: **auto-pull follows `hub.auto_pull`, default false** (the key is
already in the config schema, and a `run` that never reaches the network on its own is
the safer default), and **exit code 1 for "goal not met" stays as the table specifies** —
an agent that declared checks and failed them did not do the job, and the exit table is
the contract. `--strict` can be added later without breaking it.

**Definition of done**: `uv run teacup run examples/note-taker "..."` works with no
config file present; a missing declared environment variable fails at preflight with
exit 4 and before any spend; `--json` puts exactly one object on stdout with the answer
on stdout and the ledger on stderr; `--dry-run` completes with no key and no model call;
each of the five exit codes is reachable and tested.

---

### 2. A threat model for running someone else's agent

**Now**: `sandbox.py` exists and `run_external` uses it, but there is no document saying
what is trusted when you run a package you did not write — and that is this repo's
headline API. A package can carry Python: `auto.py`'s `_load_tools` and `_load_checks`
import `tools.py` and `checks.py` from the package directory, so
`from_pretrained(...).run(...)` executes a stranger's code in-process. It also carries
their prompt, their skills, and their MCP server list.

teacup-agent has `docs/threat-model.md` for its own surface. This repo has none, and it
is the one that downloads and runs other people's work.

**What to change**: a `docs/threat-model.md` in the same per-feature shape teacup-agent
uses — what is trusted, what is not, what each capability adds. Then decide the defaults
it implies rather than inheriting them by accident: whether a pulled package's `tools.py`
runs in-process at all by default, what `--dry-run` may import, and whether the native
in-process path deserves the sandbox the external path already gets.

**Definition of done**: the document names, for each of the four verbs, what an attacker
who controls a published package can reach; every default it recommends is either already
the code's behaviour or has an issue linking to it; and item 1's CLI does not ship a
`run` that is easier to point at a stranger than the library is.

---

### 3. Close the improve loop: measured before-and-after

**Now**: the two halves exist and do not meet. `run_coding_task` produces a branch, a
diff and a test result; `evaluate.py` scores an agent on quality *and* cost. Nothing runs
a package's own benchmark before and after a coding task, so "improve" is only
"edit" — whether the change helped is a human judgement made from a diff.

This is the gap that seven review rounds on teacup-agent's #11/#16 made concrete: every
round needed "is this better than last time", and the answer came from a scoring script
written by hand for that one task.

**What to change**: a function that takes a package and a coding task, runs the
package's benchmark on the base ref, applies the task, runs it again on the branch, and
returns both rows plus the delta — quality and cost, never one without the other, which
is `evaluate.py`'s existing rule. `docs/backends.md` already documents the worktree
discipline this must not break: never touch the primary checkout, never commit, never
push, stop at a reviewable branch.

**Definition of done**: one call produces before/after scores, before/after cost, the
diff, and the test result, for a package that declares a benchmark; a task that makes the
agent worse is visible as a negative delta rather than as a green test suite; the
existing "produce a reviewable branch and nothing else" guarantee is unchanged.

---

### 4. One package format, two implementations, no drift

**Now**: `agent.yaml` means two different things. This repo's is a package manifest
(`name`, `version`, `framework`, `entrypoint`, `model`, `skills`, `tools`, `goal`,
`budget`, `environment.required`, `lineage`). teacup-agent's is a runtime config
(`models`, `mcp`, `tools`, `skills`, `runtime`). Same filename, incompatible schemas —
which is why `examples/teacup-agent-bridge/agent.yaml` carries a comment saying the two
"cannot share a directory".

Agent Skills validation is deliberately mirrored in both repos, with a comment in each
saying a skill that validates in one must validate in the other. That is two
implementations of one format with **no test that they agree**.

**What to change**: write the package format down once — this repo owns it, because
teacup-agent is one instance of the format rather than its author — and ship a fixture
suite (valid packages, and invalid ones with the reason each is invalid) that both repos
run. Not a shared library: a third package at this size is more coupling than the problem
costs.

Also settle the filename collision rather than leaving it as a comment in an example.
Either the manifest gets a distinct name, or the rule "a teacup-agent checkout is never
itself a teacup-run package, only a bridge points at one" is stated somewhere a person
reads before hitting it.

**Definition of done**: a `tests/fixtures/format/` directory both repos load; a
conformance test in each that walks it; and one document that is the format's definition,
linked from both repos' docs.

---

### 5. Reproducibility: pin what a version actually resolves to

**Now**: `push_to_hub` writes `lineage.derived_from`, so provenance exists — but it
records a **name**, not a version, so a fork does not say which version it forked. And a
package pins its model by name (`model.primary`), while a run's behaviour also depends on
the skills, the harness version, and — for a `framework != "teacup"` package — the target
checkout's own commit. `from_pretrained("alice/deep-research")` is therefore repeatable
in the sense that it fetches the same files, and not in the sense that it does the same
thing.

**What to change**: `lineage.derived_from` carries the version it was derived from. Then
decide how much further to go, and say so rather than implying more than is true: a
recorded resolution (what a run actually used — model id, package version, harness
version) attached to the result is cheap and honest; a full lockfile is a bigger promise
and probably not this project's job.

**Definition of done**: a fork records the exact version it came from; a `--json` result
states what it actually resolved to; and the README does not claim reproducibility beyond
what those two provide.

---

### 6. A conformance suite for framework backends

**Now**: `run_external` launches any framework that speaks a small contract — argv shape
plus one JSON line on stdout — and `docs/backends.md` describes it. `tests/fixtures/fake_cli.py`
is already a stand-in for a conforming backend. What is missing is the suite an adapter
author can run to find out whether their framework qualifies, and a version on the
contract so it can change without silently breaking every adapter.

**What to change**: promote the contract to a versioned document, and turn the fake CLI
into a reusable conformance test: given a command, assert it accepts the argv teacup-run
builds, emits exactly one parseable JSON line, reports `remaining_budget`, and exits 0/1
where the contract says.

**Definition of done**: an adapter author can run one command against their own CLI and
get a pass/fail with reasons; teacup-agent passes it; the contract document carries a
version number that `run_external` checks or explicitly does not.
