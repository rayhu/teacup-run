# Intent

`README.md` says what this is and argues for why it should exist. `CONTRIBUTING.md` says
where things live. This file says what the project is **for**, and what a fork owes it —
the criteria a change can actually fail, so "did this stay true to the thing?" is a
question with an answer.

It is also the first artifact in the chain this repo's `REVIEW.md` and
[teacup-agent's `docs/workflow.md`](https://github.com/rayhu/teacup-agent/blob/main/docs/workflow.md)
run on:

```
docs/intent.md  ->  the spec docs  ->  docs/roadmap.md item  ->  code + tests  ->  review  ->  human merges
```

That ordering is a working constraint, not a diagram. **The spec is downstream of this
file**: every capability named in §3 gets a document that contracts it — values, shapes,
interfaces, what happens on the unhappy path — and every such document exists because a
capability here asked for it. That is what "machine-actionable" means; not that a tool
parses the prose, but that an agent given this file and the repo can say which contracts
are missing, which criteria are red, and which invariants no longer have a guard.

Today this repo has two of those documents — [`docs/execution.md`](execution.md) and
[`docs/backends.md`](backends.md) — and §3 names the rest as gaps rather than leaving them
to be discovered by whoever gets bitten. **The most useful thing this file does right now
is be the table of contents for a spec that has not been written.**

Three checks keep the pair honest, and each is a command, not a judgment:

- **Coverage.** Every row in §3 names where its contract lives, or says "none yet". A
  capability with no contract is unspecified — and this repo distributes other people's
  code, so unspecified means "whatever `main` happens to do this week."
- **Currency.** Every criterion in §6 carries the command that measures it and the number
  that command printed. Re-run them; a number that moved is either the change or the
  criterion, and the author has to say which.
- **Guards.** Every invariant in §5 names a test that exists. An invariant nothing
  executes is a preference.

## 1. The intent in one sentence

Make an agent a portable, forkable, evaluable artifact — one directory, one reference,
one command — so that improving somebody else's agent and publishing the improvement is
routine rather than a rewrite.

The measure of success is not how many agents exist. It is the sentence the README puts
last: **how often does someone take another person's agent, improve it, and publish the
improvement?** Everything below is downstream of that question, and any feature that does
not eventually make that sentence more true is a feature for a different project.

## 2. Who this is for

Each reader implies a surface, and the surface is what a spec has to pin down.

| Reader | What they want | The surface they touch |
| --- | --- | --- |
| **The agent author** publishing something they built | one layout, no build step, no second shape to reconcile | the package format: `agent.yaml`, `prompts/`, `skills/`, `tools.py`, `checks.py`, `evals/` (§3.1) |
| **The forker** who found an agent that does most of what they need | pull it, add a skill, prove their version is better, publish it | `from_pretrained` / `add_skill` / `eval` / `push_to_hub`, lineage (§3.2, §3.5, §3.8, §3.9) |
| **The runner** who just wants to use one | a command, a budget that is real, an exit code that does not lie | `teacup run`, the ledger, `--json` (§3.10, §3.7, §3.11) |
| **The framework author** whose agents already work | their runtime as a backend, not a rewrite | `framework:` + the backend contract (§3.4, §3.12) |

Note who is *not* in this table: an operator running a fleet, a compliance reviewer, a
buyer. Those readers are why §7 exists.

## 3. What the system must be able to do

This is the generation map. The last column is where a capability's contract lives —
which is how a spec gets written from this file instead of from someone's memory. "none
yet" is not an apology; it is the work item.

| # | Capability | Why the intent requires it | Contract |
| --- | --- | --- | --- |
| 3.1 | **The package format**: one directory that *is* the artifact — manifest, instructions, skills, tools, checks, benchmark, card | reuse needs one shape, and a second inner shape is what silently drops the benchmark and the card | **none yet** — `manifest.py` and README prose are the only statement. This is gap #1. |
| 3.2 | **Resolve a reference** — local path, hub name, git URL — to a package on disk | "load it with one line" | **none yet** — `registry.py` |
| 3.3 | **Run it on the native loop**, with tools and an outer goal loop | an agent nobody can run is not an artifact | `docs/execution.md` covers the CLI's half; the loop's own contract: **none yet** (`loop.py`, `goal.py`) |
| 3.4 | **Run it on somebody else's framework** through a backend | "existing frameworks should become backends, not competitors" — the claim the whole format rests on | [`docs/backends.md`](backends.md) |
| 3.5 | **Extend without rewriting**: skills, MCP servers, model, extra instructions | composability is the difference between forking and copying | **none yet** — the [Agent Skills spec](https://agentskills.io/specification) and MCP are the external halves; this repo's side is unwritten |
| 3.6 | **Deterministic goal checks** — what "done" means, as predicates | "evaluate whether your version is actually better" needs a definition of better that is not a vibe | **none yet** — `goal.py`, a package's `checks.py` |
| 3.7 | **Budget as a real constraint**, plus a ledger of what was spent | quality *under constraints* is the evaluation this project argues for | **none yet** — `budget.py`; the ledger's shape is pinned only by `docs/execution.md` and tests |
| 3.8 | **Evaluate against a benchmark**, comparably between upstream and fork | the flywheel's proof step | **none yet** — `evaluate.py` |
| 3.9 | **Publish and pull, preserving lineage** | the flywheel's last step; lineage is what makes it cumulative | **none yet** — `registry.py`; the round-trip is pinned by tests (§6.1) |
| 3.10 | **A CLI**: running an agent must not require writing Python | stated in the README as the reason this project exists | [`docs/execution.md`](execution.md) — for `run` only; see §6.3 |
| 3.11 | **Machine-readable output**: one JSON object, exit codes that classify the ending | a runner in CI, and the integrator who never sees the terminal | `docs/execution.md` §6-§8 |
| 3.12 | **Sandboxed subprocess launch** for external backends | running a stranger's agent is the headline API | `docs/backends.md` for the mechanism; **`docs/threat-model.md`: none yet**, and it is the most expensive gap here — see §4.1 and roadmap item 2 |
| 3.13 | **The coding-task bridge** (`run_coding_task`): drive a coding agent against a checkout, stop at a reviewable branch | this is how the improve loop gets dogfooded rather than asserted | **none yet** — `coding_task.py`; the receipts are in [teacup-agent's `docs/case-studies.md`](https://github.com/rayhu/teacup-agent/blob/main/docs/case-studies.md) |

Eleven of thirteen rows say "none yet". That is the honest state of a repo whose library
half is built and whose written contract is two documents; it is also, in priority order,
exactly what the spec should contain.

## 4. What binds any implementation

Constraints a spec must satisfy whatever else it says.

1. **This repo runs code it did not write.** A package carries Python (`tools.py`,
   `checks.py`), a manifest that can name an `entrypoint`, an env allowlist, and an MCP
   server list. Every design decision here is downstream of that, and the document that
   should state the trust boundary does not exist yet (roadmap item 2). Until it does,
   the honest sentence for a user is the one in the README: only connect to, and only
   run, something you trust to run unattended.
2. **Open formats over invented ones.** Skills are the Agent Skills spec; instructions may
   come from `AGENTS.md`; tools may come from MCP. A format this project invents where an
   open one exists is a tax on every fork.
3. **The format stays readable, hackable, and friendly to git.** No build step, no
   generated artifact, no lockfile-of-the-format.
4. **Python 3.11, `uv`**, provider SDKs optional and per-provider. A user installing this
   should not install three vendors' clients to run one agent.
5. **Everything checkable stays hermetic and free** — no API key, no network, no cost —
   or people stop checking it (§6.2).
6. **MIT, and it stays MIT.**
7. **English in the repo**, whatever language the conversation happens in.

## 5. What a fork must keep to still be this thing

Everything else is yours. Each of these was paid for by a real bug or a real review
finding, and each is executed by a named test.

| # | Invariant | What executes it |
| --- | --- | --- |
| 1 | **The agent directory is the distributed artifact.** Publishing copies the directory; pulling gets that copy back. There is no narrower "real package" inside it, so nothing — benchmark, card, skills — can be silently left behind. Two kinds of thing stay out, by an explicit list rather than by location: secrets (`.env`) and dev residue (`.git`, `.venv`, caches, build output). | `tests/test_auto.py::test_publishing_reproduces_the_agent_directory`, `::test_publishing_leaves_dev_residue_and_secrets_behind`, `::test_push_to_hub_then_pull_it_back` |
| 2 | **A package may only read its own files.** Manifest-declared paths are contained; an `instructions:` or skill path pointing outside the package is refused, in `--dry-run` too. | `tests/test_manifest.py::test_instructions_may_not_escape_the_package`, `::test_the_escape_is_refused_during_dry_run_too`, `::test_a_package_still_reads_its_own_files` |
| 3 | **`sandbox.py` requires an explicit `cwd`.** An earlier version defaulted to a throwaway scratch directory, which silently broke anything the launched program resolves relative to its own working directory. | `tests/test_sandbox.py::test_cwd_is_honored_not_a_throwaway_scratch_directory` |
| 4 | **The sandboxed child's `stdin` is always severed** (`subprocess.DEVNULL`). A child launched from a real terminal otherwise inherits that TTY, and an unattended run hangs indefinitely on a prompt nobody is there to answer. It once hung for 630 seconds. | `tests/test_sandbox.py::test_run_sandboxed_always_severs_stdin_explicitly`, `::test_child_cannot_block_reading_a_severed_stdin` |
| 5 | **A resource limit that cannot be honored on this platform is skipped, not fatal.** `resource.setrlimit` can fail outright on macOS; that must never abort the launch. | `tests/test_sandbox.py::test_memory_limit_failure_does_not_abort_the_launch` |
| 6 | **Nothing is pushed, merged, or committed to the target repo automatically.** `run_coding_task` stops at a reviewable local branch and worktree; a human decides what happens next, every time. | `tests/test_coding_task.py::test_run_coding_task_creates_a_worktree_on_a_new_branch`, `::test_run_coding_task_never_touches_the_original_checkout` |
| 7 | **An ending that is not a success never reports success.** An unclassified early stop, a crashed backend or a burnt budget each get their own exit code; exit 0 means the goal was met. | `tests/test_cli.py::test_a_sandboxed_run_that_stopped_early_does_not_exit_zero`, `::test_an_unclassified_early_stop_is_not_reported_as_success`, `::test_exit_2_when_the_budget_stopped_the_run` |

## 6. Success criteria

Vague goals ("portable", "composable") cannot be failed, so they do not constrain
anything. These can. Each carries the command, the threshold, and what the command printed
when this file was last checked (2026-09-11).

**6.1 The format round-trips.** Publish a directory, pull it back, and the two shapes are
equal — otherwise "the directory is the artifact" is a slogan, and whatever the copy
forgets is missing only for people downstream.

```bash
uv run pytest tests/test_auto.py -q -k "publish or push_to_hub or pulled"
```

Threshold: green. **Measured: 6 passed. Status: green.**

**6.2 Everything checkable stays free and hermetic.** No API key, no network, no cost —
which is also why CI can run it on every pull request.

```bash
uv run pytest
```

Threshold: green, under a minute, with no API key set. **Measured: 257 passed, ~14s
(the count is the claim; the seconds are the machine). Status: green.**

**6.3 Using an agent never requires writing Python.** The README states this as the reason
the project exists, and today it holds for exactly one verb.

```bash
uv run teacup --help        # every verb a user needs should be reachable from here
```

Threshold: pull, run, evaluate and publish all reachable from the CLI. **Measured: 1 of 4
(`teacup run`). Status: RED.** `from_pretrained`, `eval` and `push_to_hub` are Python-only,
so the flywheel's discover → fork → evaluate → publish loop still requires the thing the
project says it is removing.

**6.4 The first thirty seconds need no key.** A reader who has just cloned this should be
able to see a package load, resolve, wire up its tools and report what it *would* do.

```bash
env -u OPENAI_API_KEY uv run teacup run examples/note-taker "any task" --dry-run
```

Threshold: exits 0 and prints the preflight. **Measured: exit 4, with
`ERROR: teacup/note-taker needs OPENAI_API_KEY, which is unset or still a placeholder`, in
0.09s. Status: RED.** Preflight treats a declared environment requirement as mandatory
even when nothing will be called; a wiring check that cannot run without a credential is
the one thing a stranger cannot try. (teacup-agent's equivalent criterion — an offline
demo that runs instantly with no key — is green, and is why this one is written down.)

**6.5 More than one framework, and eventually one that is not ours.** "Existing frameworks
should become backends, not competitors" is unproven with a single implementation.

```bash
ls examples/*/agent.yaml | xargs grep -h '^framework:' | sort -u
```

Threshold: at least two frameworks, at least one of them not written by this project.
**Measured: 2 (`teacup`, `teacup-agent-cli`), both ours. Status: amber.** The mechanism is
proven; the claim about *other people's* frameworks is not, and a third-party backend is
roadmap item 6's conformance suite made concrete.

**6.6 The metric that actually matters** — someone takes an agent, improves it, publishes
the improvement — has no command yet. Roadmap item 3 is what turns it into one; until
then it is a goal, not a criterion, and this file should not pretend otherwise.

## 7. Non-goals

Stated here so "should we build it?" has an answer before the argument starts. The
README's "What Teacup Run Is Not" is the long version:

- **Not an enterprise control plane, RBAC product, SSO platform, or governance suite.**
  Useful problems; not the first one.
- **Not another orchestration DSL or agent framework.** A portable layer *above* the
  frameworks, or it is just one more of them.
- **Not a closed marketplace.** The hub is a directory, optionally a git repo, and no
  server — which is a scope decision, not a limitation to be apologized for.
- **Not v0.1's missing pieces pretended into existence**: no sub-agents or handoffs, no
  memory, no streaming, no async, no registry server, and beyond the one sandboxed
  subprocess backend, no general adapter system.
- **Not a second agent to read.** One readable loop is teacup-agent's job — see §8.

## 8. The other half: teacup-agent

[teacup-agent](https://github.com/rayhu/teacup-agent) is the sibling project, and the line
between them is what keeps both honest:

> **teacup-agent is one agent you can read and fork; teacup-run is the ecosystem around
> many agents.**

So: a feature that would make *that* repo a platform — a package format, a hub, lineage,
budgeted leaderboards, a way to run somebody else's agent — belongs here. A feature that
makes one loop more honest belongs there. This repo owns the package format; teacup-agent
is one instance of it, reached through the `teacup-agent-cli` backend. Where the two
touch, the contracts are teacup-agent's `docs/integration.md` (the `--json` line
`external_cli.run_external` parses) and its `docs/threat-model.md` (what this repo's
sandbox does and does not isolate — and it does not isolate the filesystem or the
network). The same statement from the other side is in teacup-agent's `docs/intent.md`.

## 9. Fork

The table in [CONTRIBUTING.md](../CONTRIBUTING.md) is the map: a new framework backend is
a new module beside `external_cli.py` + `sandbox.py`; the package format is `manifest.py`;
cost accounting is `budget.py`; the model backends are `model.py`; the native loop is
`loop.py` + `goal.py`. Two documents are worth reading before touching either end:
`docs/backends.md` for a new backend, `docs/execution.md` for a new execution mode.

If your change forces the *format* to grow a special case for your runtime, that is the
signal to look again — the format sitting above frameworks is the whole claim.

## 10. Improve

The habit is **before-and-after numbers**, not "should work". That is also the product:
§6.6 is the same habit pointed at agents instead of at this codebase, and roadmap item 3
is what makes it measurable here. When a run goes wrong, the post-mortem goes into
`docs/roadmap.md`; when it goes wrong while driving a coding agent through this repo's own
sandbox, it goes into [teacup-agent's `docs/case-studies.md`](https://github.com/rayhu/teacup-agent/blob/main/docs/case-studies.md)
in the same commit as the fix, not after. That log is not a highlight reel: a sandbox that
hung for 630 seconds, a model that gave up when one tool call was denied, a model that
reached for a shell command instead of the tool built for the job. Every one was a real
bug in this repo's code, found by running the thing.

The review pass a change goes through is [REVIEW.md](../REVIEW.md): one agent writes, a
different agent reviews with its own context, a human merges.

## 11. Publishing a fork

There are two names — `teacup_run`, the Python module, and `teacup-run`, the distribution
— plus one console script, `teacup`, that is neither. One command finds every file
carrying any of them:

```bash
grep -ril 'teacup' . --exclude-dir=.git --exclude-dir=.venv --exclude=uv.lock
```

1. `git mv src/teacup_run src/<your_module>`.
2. `pyproject.toml`: `name`, `[project.scripts]` (the console script users type), and
   `[tool.hatch.build.targets.wheel] packages`.
3. Sweep all three spellings across everything grep listed — `src/`, `tests/`,
   `examples/`, `docs/`, `.env.example`.
4. `TEACUP_HOME` and `TEACUP_CONFIG`, plus the config path `~/.config/teacup/config.yaml`
   (`config.py`, `registry.py`, `env.py`, the tests and the docs). Rename them or you will
   read someone else's prefix in your own error messages — and share a hub directory with
   a project you have diverged from.
5. `examples/teacup-agent-bridge/agent.yaml` points at a sibling checkout via
   `teacup_agent.project_root`. If you keep the bridge, that path is yours to fix.
6. `LICENSE` is MIT: keep the existing copyright line, add your own.
7. **There is no `AGENTS.md` here yet.** If you drive this repo with a coding agent, write
   one — [teacup-agent's](https://github.com/rayhu/teacup-agent/blob/main/AGENTS.md) is
   the closest reference, and it has actually been used against this repo's own
   coding-task pipeline. Write `CLAUDE.md` as a one-line import of it, so there is one set
   of rules and no second copy to drift.
8. **This file is next.** Rewrite §1, §3 and §6 for what *your* fork is for; an inherited
   success criterion is one nobody will run.

Then `uv sync --extra dev` and `uv run pytest` must be green before you publish anything.

## 12. Upstream, or your fork?

- **Upstream** if it makes the format more portable or closes a gap in §3 or §6 — a
  written contract for a capability that has none, a backend conformance case, a threat
  model, a CLI verb that removes Python from the flywheel.
- **Your fork** if it makes the runtime better at *your* job: your provider, your
  orchestration, your hub policy, your service layer. Those are real work and they are
  welcome to exist — just not in the layer that is supposed to sit above all of them.

## Keeping this file true

- **When a capability lands**, add its row to §3 with the document that contracts it. A
  row that still says "none yet" after the code ships is the reviewer's finding.
- **When behaviour changes**, re-run §6's commands and paste what they print. The date in
  §6 is the claim; a stale date is a stale file.
- **When an invariant gains or loses a guard**, fix §5's right-hand column. A named test
  that no longer exists is the same lie as a number that no longer holds.
- **Nothing in §6 is enforced by CI.** `.github/workflows/verify.yml` runs `uv run pytest`,
  which is §6.2 and nothing else. The two red criteria (§6.3, §6.4) went unnoticed because
  no command was ever written down for them; writing them down is what this file is for,
  and wiring them into CI is what makes it stop needing discipline.
