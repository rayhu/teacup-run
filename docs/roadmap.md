# Roadmap

What is missing, in the order it should be built. Same shape as teacup-agent's
`docs/roadmap.md`: **Now** (what the code does today), **What to change**, and a
**Definition of done** that can be checked rather than argued about.

**Progress**: the library half is built — `AutoAgent`'s four verbs
(`from_pretrained` / `add_skill` / `run` / `eval` / `push_to_hub`), a hub that is a
directory and optionally git with no server, budgeted evaluation, goal checks, the
sandboxed subprocess launcher, and `run_coding_task`. What is missing is mostly the
seams between those pieces, the one verb that still requires writing Python, and #7: a
run reports everything it did and then keeps none of it, which is why "is my fork better"
has no command and why a browsable front end has nothing to read. #7 → #8 → #9 are that
one path: keep the record, give it a long-lived local process, then render it — none of
which crosses a machine boundary, which is what keeps them out of intent §7's non-goals.
#10 is the one that path cannot skip: that page may *show* runs with no gate at all, but it
may not grow a button until this repo has one, because today it has none for any tool.

The dividing line with teacup-agent, stated once because it is what keeps both repos
honest: **teacup-agent is one agent you can read and fork; teacup-run is the ecosystem
around many agents.** A feature that would make teacup-agent a platform belongs here.
Its own `AGENTS.md` says the same thing from the other side ("its value is that the
control loop in `loop.py` fits in one head").

---

### 1. `teacup run` — the CLI — DONE (2026-09-08)

**Was**: there was none. `pyproject.toml` declared no `[project.scripts]`, and running an
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

**What shipped**: the nine outstanding table items. `stop_kind` on `Result`, so "budget"
and "error" are told apart by a value rather than by parsing prose written for a human;
`AgentSpec.missing_environment()`, kept out of `validate()` for the reason §2 gives;
`Budget.remaining()` lifted out of `Ledger.render`; `load_env(search_cwd=False)`;
`config.py`; `cli.py`; `[project.scripts]`; `.env.example`; and `tests/test_cli.py`.

Two things the implementation learned that the design could not have. The CLI test suite
fakes `model._call_openai`, **not** `loop.call_model` — `loop.run` takes
`model_fn=call_model` as a default argument, bound when the function was defined, so
patching the module attribute changes nothing and the test silently calls the real
provider. And a run that crashes has no goal verdict, which is not the same as passing:
reporting `goal.met: true` there would have called a failed run successful, so a missing
verdict means "met" only when the run also completed.

Not verified: no live provider call was made. The whole suite runs on the faked provider
seam, so what is pinned is the CLI's own behaviour — preflight, exit codes, output
separation, the JSON shape — and not that any particular model answers well.

**Definition of done**: `uv run teacup run examples/note-taker "..."` works with no
config file present; a missing declared environment variable fails at preflight with
exit 4 and before any spend; `--json` puts exactly one object on stdout with the answer
on stdout and the ledger on stderr; `--dry-run` completes with no key and no model call;
each of the five exit codes is reachable and tested.

---

**Still open from this item**: `publish` must resolve the hub through
`config.effective_hub()`, or reads and writes split. Today a config with `hub.path` and
no `TEACUP_HOME` sends `teacup run` to the config's directory while `push_to_hub()` —
which passes no explicit hub — writes to `registry.hub_path()`'s default. Only reachable
from the library right now, because there is no `teacup publish` subcommand yet; it
becomes user-visible the moment there is one.

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

**Three concrete escalations, found by review of item 1 and left for this item**
rather than half-fixed there, because each is a policy decision and not a bug:

1. **`entrypoint:` is executed.** Any `framework:` other than `teacup` routes to
   `run_external`, which `shlex.split`s the manifest's `entrypoint` string and runs it.
   No `tools.py` and no network fetch are needed — a manifest alone is enough. Proven
   during review with `entrypoint: "/bin/sh -c '...'"`, which ran as the user, from a
   `--json` invocation, and wrote a file. The framework name is validated now, but that
   changes nothing here: a hostile package simply writes `teacup-agent-cli`.

   **And a manifest with no `entrypoint:` at all still gets there**, which the first
   version of this entry missed. `run_external` defaults the entrypoint to
   `uv run teacup-agent` and `_build_argv` inserts `--project <project_root>` — so
   `framework: teacup-agent-cli` plus `teacup_agent: {project_root: .}` makes `uv` read
   the *package's own* `pyproject.toml`, resolve and install the dependencies it
   declares, and run its `[project.scripts]` console script. That is code execution
   sourced from a second attacker-controlled file, and it reaches the network — the
   thing `_check_auto_pull` holds up as what the default avoids. So the escalation is
   not "do not write an entrypoint"; two independent manifest keys each reach execution.
2. **`environment.required` is the child's env allowlist.** A package declares the
   variable names it wants and `_resolve_env` hands exactly those to the subprocess —
   so a package declaring `AWS_SECRET_ACCESS_KEY` gets it, and preflight *insists* the
   variable be present before it will run. The mechanism that makes preflight helpful
   is the one that makes this reachable.
3. **`teacup_agent.project_root` sets the subprocess cwd *and* `uv`'s `--project`**
   via `spec.root / value`, which `../..` or an absolute path escapes. Per 1, that
   second role is a code-execution vector on its own, not just a working directory. Not containable without a decision:
   `examples/teacup-agent-bridge` escapes on purpose, pointing at a sibling checkout.

Manifest-declared paths *inside* the schema — `instructions:`, `read()`, `AGENTS.md` —
are contained as of the CLI change (`AgentSpec._inside`), because there was no design
question there: nothing legitimately points outside. These three have one.

**Definition of done**: the document names, for each of the four verbs, what an attacker
who controls a published package can reach; every default it recommends is either already
the code's behaviour or has an issue linking to it; the three escalations above each have
a stated answer (allowlist, prompt-on-first-run, sandbox-by-default, or "accepted, and
here is why"); and item 1's CLI does not ship a `run` that is easier to point at a
stranger than the library is.

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

---

### 7. A run record: keep what a run already knows

**Now**: a run produces a detailed report and then throws it away. `cli.py`'s `_payload()`
builds exactly what `docs/execution.md` §7 specifies — agent name and version, the ref,
the model, the task, the answer, every goal check with its pass/fail and reasons, the
attempt count, the tool calls, the cost split three ways, token usage including cached,
the budget and what is left of it, how it stopped, elapsed seconds, exit code — and
`print`s it to stdout. Nothing writes it anywhere. The native path has no `--run-dir`
equivalent at all; only `external_cli.run_external` hands the launched agent a directory,
and `coding_task` points that at a worktree.

So this repo cannot answer either of the two questions its own README implies:

- **"What did I run, and what did it cost?"** — only for the run still on screen.
- **"Is my fork better than upstream?"** — intent §6.6 says this metric has no command.
  Item 3 is the machinery for one before/after comparison; without a record, even that
  comparison evaporates the moment the process exits.

It is also what makes a browsable front end impossible today rather than merely unbuilt: a
page that shows agents and their runs has nothing to read. The tempting version of that
page — a live "what is running right now" dashboard — is out of scope for a different
reason: a run here is a foreground call in the caller's own process, so that set is never
larger than one, and making it larger means a daemon, a queue and multi-tenancy, which
intent §7 rules out. Recording runs is the part that is not a control plane.

**What to change**: persist the payload that already exists.

- **One append-only file per run** under `$TEACUP_HOME/runs/`, named so it sorts by time
  and does not collide: JSON lines, one event per line, nothing ever rewritten. The first
  line is `started` — run id, agent and ref, model, task, `started_at`, and the pid of the
  process that runs the agent and holds the lock (see below). The last line
  is `finished` — `docs/execution.md` §7's object plus `finished_at` and the exit code. In
  between, one `turn` line per step: step number, tokens, cost so far. That middle stream
  is what lets #8 show a run while it is still running and #9 tail it, without either of
  them polling a file that changes under them.
- **Status is derived, never stored.** The first draft of this item said a mutable
  `status` field going `running` → `finished`, written at the start and rewritten at the
  end. That is wrong, and it is worth writing down why rather than quietly fixing: a
  process that is killed, OOMs, or loses power never reaches the rewrite, so the file says
  `running` forever and every reader of it — including #9's page — shows a run that died
  days ago as live. **A field that can only be correct when the program exits cleanly is
  not a record of whether the program exited cleanly.** The three states come from the file
  instead:
  - `finished` — the last line is a terminal event.
  - `running` — no terminal event, and the writer still holds the file's advisory lock.
  - `crashed` — no terminal event, and the lock is free.

  **The lock belongs to the process that runs the agent — never to a parent that supervises
  it.** Under #8 that is the run's own subprocess, not the serving daemon. This is the whole
  design, and getting it wrong reinstates the defect above in a worse form: if the daemon
  held the lock, the lock would track *the daemon's* liveness, so a run killed under a
  daemon that stays up would read `running` forever — exactly the zombie, now invisible to
  the SIGKILL test below because that test passes on the CLI path. It also settles who may
  write a terminal event: only the lock holder, which is why #8's shutdown stops its
  children and lets each write its own rather than writing on their behalf.

  Naming the owner that way is also what makes the lock *readable*. Testing a lock means
  attempting to take it, so status is an acquire-and-release, not an inspection — and with
  POSIX record locks (`fcntl(F_SETLK)` — `flock(2)` is the BSD call, only standardised in
  POSIX Issue 8) **any `close()` of any descriptor on that file drops every lock the calling
  process holds on it.** A reader inside the writing process would therefore erase the
  signal it came to read, which is precisely what would happen if `serve` both owned the
  records and rendered the page. Because the owner is the child, `serve` reads as a separate
  process, where a failed acquire is just a failed acquire. A reader that does acquire the
  lock releases it immediately and reports `crashed`.

  Two ordering rules the states depend on. The record is created under its lock before any
  reader can see it — written to a temporary name, locked, then `rename`d into place — so
  there is no window in which a live run looks lock-free. And the last line may be
  truncated: a process killed mid-write leaves a partial JSON line, which is the *expected*
  shape in the case this record exists to capture. A partial line is not a terminal event,
  and a reader that raises on it has turned the crash it was built to report into a crash
  of its own.

  Scope, stated rather than assumed: **liveness here is a single-machine property.** A
  `$TEACUP_HOME` shared over NFS, or read across a container boundary, is where advisory
  locks are least trustworthy — wrong in one direction, silently — and #8 is loopback-only
  precisely because nothing in this path is designed to cross a machine. Where the lock is
  unavailable at all, a record with no terminal event is `unknown`, not a guess: the
  `started` line's pid is diagnostic, and status must never depend on it, because the record
  carries no process start time and therefore nothing that could distinguish a live process
  from a reused pid.
- **The `started` line is flushed before the first model call**, not after it. A run that
  dies inside its first request has already spent money, and a record written at the end
  would have no trace of it. This is write-ahead logging for the same reason a database
  writes its log before its pages: the record has to survive the process that writes it.
- On by default for `teacup run`, off with `--no-record`, and off for `--dry-run`
  (a wiring check is not a run). The library keeps its current behaviour unless the
  caller asks for a record; `AutoAgent.run()` must not start writing to a user's home
  directory as a side effect of an import.
- **What it must not contain**: no environment values, no credential material, nothing
  resolved from `environment.required`. The task text and the answer are the user's own
  data on their own machine and belong in the record — but that is exactly why the
  directory is `$TEACUP_HOME`, never inside a package, so `push_to_hub` cannot sweep a
  run history into the hub.
- A record is written even when the run fails, is stopped by a ceiling, or raises. A
  history that only keeps the successes is the same lie as an exit code that only
  reports them, which is intent §5 invariant 7.

**Definition of done**: `teacup run` leaves one file per run whose event shapes a test
asserts against the documented key set — the same test style item 6 wants for the backend
contract, so a field cannot be renamed without something failing here; `--no-record` and
`--dry-run` leave nothing behind; a run that exits 2, 3 or 4 still leaves a record saying
so; **a run killed with SIGKILL mid-flight leaves a record that reads `crashed`, and a test
kills one and asserts exactly that — on every path that can start a run, the served one
included once #8 exists**, because the same assertion passes on the CLI path while being
false under a supervisor that outlives the run, and that gap is the defect this item was
rewritten to fix; a record whose last line is a truncated write still reads `crashed`
rather than raising; status is one named function over a file, not an inline check repeated
at each call site, and it returns `unknown` rather than guessing where the lock is
unavailable; the record's shape is written down in
`docs/execution.md` beside the `--json` object it extends, rather than in a comment; intent
§3 gains a row for it that names that document instead of "none yet"; and `uv run pytest`
stays hermetic — the tests must not write into a real `$TEACUP_HOME`.

**Not this item, and deliberately after it**: #8 (`teacup serve`, the long-lived local
mode that makes records visible while they are being written) and #9 (`teacup site`, the
page that renders them). Writing the record first is what makes both of those a view
rather than a rewrite.

---

### 8. `teacup serve` — a long-lived local mode, not a control plane

**Now**: `teacup --help` offers exactly one subcommand, `run`. A run is a foreground call
in the caller's own process, so nothing outlives it, nothing else can see it, and the set
of "agents running right now" is never larger than one.

Worse for anything long-lived: the native path executes a pulled package's Python **in the
caller's own interpreter**. `auto.py` resolves `tools.py` and `checks.py` with
`importlib.util.spec_from_file_location` and `spec.loader.exec_module` — a stranger's code
in this process, with this process's environment and privileges. Today that is contained by
what the process is: a one-shot run the user typed themselves. The external-backend path
already does the opposite and does it properly — `sandbox.py` launches a subprocess with an
explicit `cwd`, severed `stdin`, an env allowlist and resource limits.

Intent §7 rules out an enterprise control plane and a registry server. Those are
*cross-machine, multi-tenant* things. A process on the user's own machine, bound to
loopback, running with the credentials that user already has in their own shell, changes no
trust boundary — it is the CLI with a longer life. It is also the missing half of #7 (a
record you can watch being written, not only read afterwards) and the thing #9 talks to.

**Build #10 first.** The file's contract is "in the order it should be built", and these
two are out of order as numbered. The dependency is on correctness, not polish: this entry
already describes a disconnected client leaving its run to a denial (below), which is
behaviour that does not exist until the gate does, and building in listed order would ship a
long-lived process that starts runs of stranger-authored code — with the target repo's own
`hooks.py` still hardcoded as the approver on the coding path — while this repo has no gate
at all. Read #10 as this item's prerequisite wherever the numbering suggests otherwise.

**What to change**: `teacup serve`, in the same console script.

- **Loopback only.** Bind `127.0.0.1` and refuse any other bind rather than offering a flag
  that quietly exposes it. The basis is intent §7, which rules out the cross-machine
  product, and §4.1, which says this repo runs code it did not write: the one setting that
  decides who can reach a code-execution endpoint is the last place to offer a convenience
  flag whose name understates it. (Earlier drafts cited §4 for "a flag must not lie about
  what it does" and "prefer explicit over convenient". Those are teacup-agent's `AGENTS.md`
  rules; this repo has committed no such rule, and §4's seven bullets are about trust,
  formats, tooling and licence, not naming. If this repo wants that rule it has to adopt
  it, not cite it.)
- **No auth, no users, no TLS — deliberately.** On loopback there is nobody to
  authenticate. Adding them would mean the cross-machine product intent §7 rules out, and
  the day a non-loopback bind is actually wanted, item 2's threat model is the
  *prerequisite*, not the follow-up.
- **Surface = the contract that already exists.** `docs/execution.md` §7's object over HTTP
  instead of stdout: list the hub, start a run, read a run record (#7), stream progress.
  Nothing new to design; a consumer that can read `--json` can read this.
- **The hard rule: `serve` never executes a package in its own process.** `exec_module` is
  never reached from the serving process. Three reasons, each already paid for elsewhere in
  this repo: a package that crashes must not take the service with it; the timeout and
  resource limits only exist on the subprocess path; and a stranger's code must never share
  an address space and an environment with a process that outlives the run. Item 2's three
  escalations (`entrypoint:` is executed, `environment.required` is the child's env
  allowlist, `teacup_agent.project_root` escapes) are survivable at "I typed this once" and
  are not survivable in something that stays up.
- **Obeying that rule is new work, not reuse — and this is the item's real cost.** An
  earlier draft said every run "goes through `sandbox.py`'s subprocess path", as though the
  discipline already existed for every package. It does not. `auto.py:174` routes to
  `external_cli.run_external` only when `spec.framework != "teacup"`, and `manifest.py:82-84`
  says `entrypoint` is unused for the native framework — **a native package has no command
  to launch, so there is nothing for `sandbox.run_sandboxed` to run.** `serve` therefore
  needs a native runner: re-invoking this repo's own `teacup run --json` as a subprocess
  under the same `cwd`, severed `stdin`, allowlist and rlimits. Cheap to build, and
  dishonest to leave unnamed, because "reuse the path we already have" is what made the rule
  sound free.
- **Unanswered, and it belongs to item 2: how a credential reaches that child.**
  `sandbox._child_env` gives a subprocess a minimal base plus an env allowlist, and today
  that allowlist is fed by the package-declared `environment.required` — which is one of
  item 2's escalations. A native run needs a model API key in the child. Letting a
  stranger's manifest name what else travels with it is exactly the thing this rule exists
  to stop, so the answer cannot be "widen the allowlist" and this item must not pretend it
  has one.
- Bounded concurrency: a run is a subprocess with a budget, so "how many at once" and "what
  happens when that is full" are the only two questions, and neither needs a scheduler.
- **A request's blocking budget is bounded; runs outlive requests.** Starting a run answers
  with a run id inside a short fixed bound — a ceiling, not a target — and the run continues
  in its subprocess; progress is read from #7's event stream, not from a held connection. An
  agent run takes minutes and an HTTP client does not wait minutes, so the alternative is a
  surface that appears to hang. The socket does not own the run either: a client that
  disconnects leaves the run going, and stopping one is an explicit call. What the
  disconnect *does* take with it is the approval channel — per #10 that run is now nobody's,
  so `auto` denies, which is the correct and boring outcome.
- **Refusal is a recorded event, never silence.** Concurrency full, gate denied, budget
  exhausted before the first call, package not found: each leaves a record saying which. "The
  service decided not to do that" must be distinguishable from "the service is broken" from
  the outside, without reading a log — the same reason `docs/execution.md` gives exit codes
  meanings instead of only zero and nonzero. A long-lived process makes this sharper than a
  CLI does: a command that prints nothing has still visibly returned, and a daemon that does
  nothing looks exactly like a daemon that is working.
- **Shutdown is not a kill, and the daemon never writes into a record it does not own.**
  Stopping the service signals its children and gives them the chance to write their own
  terminal events, rather than orphaning them or writing on their behalf. That second half
  is not politeness: #7 puts the record's lock in the process that runs the agent precisely
  so the lock tracks *that* process, and a daemon that wrote terminal events for its
  children would be claiming an ownership it does not have — and would leave a run killed
  under a surviving daemon reading `running` forever, which is the zombie #7 was rewritten
  to remove. A child that dies without writing leaves no terminal event and a free lock,
  which #7 already reads as `crashed`. That is true, and it is what the page should say.

**Definition of done**: `teacup serve` starts and `teacup run` is unchanged; a run started
through the service produces the same `--json` payload and the same #7 record as the same
run from the CLI; a test asserts a non-loopback bind is refused; a test proves a served run
never imported the package into the serving process (a fixture package whose `tools.py`
mutates a module global, asserted absent in the parent **and paired with an assertion that
the run actually executed**, since the absence passes trivially when nothing ran); a package that crashes or burns
its budget leaves the service running; a **native** package runs served, which is what
proves the native runner exists rather than being assumed; starting a run answers within the
documented bound
while the run is demonstrably still going, and a test disconnects the client and asserts the
run survives it; a refused start leaves a record naming the reason; stopping the service
leaves no orphaned subprocess and no run record that reads `running`; the surface is
documented in `docs/execution.md` beside the CLI it mirrors; intent §3 gains a row naming
that document.

**Not this item**: cross-machine access, multiple users, credential storage, TLS, a queue
that survives restarts. Also not this item, and the tempting one: **a schedule.** A daemon
that wakes on a timer and starts runs nobody asked for is a different product, not a longer
CLI — it acts when no human is present, which is precisely the case #10's `auto` denies, and
it is what would actually turn `serve` into the thing intent §7 rules out. `serve` stays
reactive: it runs what it is asked to run, and its only unprompted behaviour is reaping its
own children. Also left to a human rather than decided here: whether *"serve
never runs a package in-process"* should be promoted to an eighth invariant in intent §5.
The item pins it with a test either way; making it a thing a **fork** must keep is a
bigger claim, and §5's list was deliberate.

---

### 9. `teacup site` — see what exists and what it did

**Now**: nothing in this repo renders anything. The hub is a directory; after #7 the run
history is a directory of JSON. Discovery — the *first* verb in the README's flywheel, the
one every other verb depends on — has no view at all. Lineage exists only as git history.
Intent §6.6's metric ("how often does someone take another person's agent, improve it, and
publish the improvement") has no way to be seen even once it can be computed.

**What to change**: `teacup site [--out dist/]` — a generator, not a server. It reads the
hub directory and the run history and writes a static page.

- **No build step, no npm, no framework**: one HTML file, one JS file, one generated JSON.
  Intent §4.3 says the format stays readable, hackable and friendly to git; a build chain
  in a `uv` project is a tax on exactly the person this project wants — the one who forks
  it. It must open over `file://` with no network and no server.
- Optionally mounted by #8 at `/` so it reflects runs as they happen, but the generator
  must not require `serve` to be useful.
- Content in priority order: which agents exist; what each was derived from
  (`lineage.derived_from` plus git history); what its benchmark and cost say; what has been
  run and what that cost.
- **Escaping is the feature, not a detail.** Every string on that page — `name`,
  `description`, `AGENT.md`, skill descriptions — was written by whoever published the
  package. No manifest-derived content ever reaches `innerHTML`. This is item 2's question
  asked in a second place: item 2 answers *what can a package do to my machine*; this one
  is *what can a package do to my browser*, and a hub is by design full of strangers' text.

**Definition of done**: one command turns a hub into a page that opens over `file://` with
no network; a test publishes a package whose `name` and `description` contain a `<script>`
tag and asserts the output renders it as text; the page shows, for at least one agent, its
lineage, its recent runs and their costs; no new runtime dependency and no build step;
intent §3 gains a row and §6 gains a criterion (one command, no network, no build).

---

### 10. An approval gate, and what "ask a human" means with no terminal

**Now**: there is none. This repo's native loop has no gated/ungated distinction on any
tool at all — `auto.py:118` and `mcp_tools.py:30` both say so in prose, as a stated
limitation rather than an oversight: a locally authored `@tool` function, and every tool an
MCP server contributes, already runs unattended with no human in the loop. The only
approval anywhere in a `teacup` run belongs to somebody else's process: `coding_task.py`
adds `--coding-tools --approve hooks` to the argv of the *launched* teacup-agent CLI
(`coding_task.py:149`), so the gate that runs is the child's — configured from here, not
implemented here.

That is survivable today for the same reason #8's in-process finding is survivable today:
a run is a foreground call the user typed themselves. #8 breaks it in two directions at
once, and an earlier draft of this item got the more dangerous one backwards, so both are
written out here.

**The direction that is merely useless.** For native tools there is no gate to configure,
and for an external run that reaches teacup-agent's own `auto` policy — ask when
`sys.stdin.isatty()`, deny when it does not (`cli.py:_make_approver`) — there is nothing to
ask on: `sandbox.py` severs the child's stdin deliberately and the serving process is a
daemon. Those calls are denied. Safe; also a front end you can watch and cannot answer.

**The direction that is not.** The one side-effecting path this repo actually has does not
reach `auto` at all. `coding_task.py:149` hardcodes `--coding-tools --approve hooks`, and
`--hooks` is deliberately *not* passed (`coding_task.py:13-24`) so that teacup-agent
auto-discovers `./hooks.py` relative to its cwd — which is the worktree. **A `hooks.py`
committed in the target repository is therefore checked out and becomes the approver**, and
teacup-agent's own module docstring calls `approve_tool_call` the one hook that can say
*yes* on nobody's behalf. Today that is a documented trade-off, contained by the fact that a
human typed the command against a repo they chose. Under `serve` it is a daemon running a
stranger's approval policy with no human, no TTY and no client attached — and teacup-run
cannot even select the child's policy, because that flag is a constant in the argv.

So the gate this item builds is not only a gate for this repo's own tools. It is also the
ability to *choose the child's*, and a served run must not default to letting the target
repo choose it.

The word that hides all of this is **manage**. A page that only *shows* runs is read-only
and needs none of this. A page with buttons — approve this call, start that run, stop that
one — is an approval surface, and approval surfaces are where agents get talked into
things.

**What to change**: give this repo a gate, and make "who is watching" an answer the program
states rather than a property of a file descriptor.

- **The four shared names keep their meanings exactly. `serve` gets a fifth.** teacup-agent
  has `--approve {auto,deny,allow,hooks}`, where `auto` is ask-if-TTY-else-deny and `hooks`
  defers to the operated project's own `approve_tool_call`. This repo takes those four
  unchanged, and adds `client`: ask an attached `serve` client, and deny when none is
  attached.

  An earlier draft did the tempting thing instead — kept four names and redefined `auto`
  from "is there a TTY" to "is there a channel to ask on". That is drift wearing the same
  name, and item 4 forbids it: after such a change the same flag, spelled the same way, in
  the same state (no TTY, a client attached) answers differently in the two repos, and a
  user who learned `--approve auto` in one place has learned something false about the
  other. Widening the meaning of an existing default is also a change to teacup-agent's
  behaviour that this repo does not get to make on its own. A genuinely new situation — an
  HTTP client is not a terminal — deserves a new name, not a quiet reinterpretation of an
  old one. `auto` therefore still means what it means next door: no TTY, no approval.
- **The child's policy becomes selectable, and a served run does not default to `hooks`.**
  `--approve` is forwarded to the launched CLI instead of being the constant at
  `coding_task.py:149`, so the caller decides whether the target repository's own
  `hooks.py` may approve on their behalf. Keeping `hooks` as the default for a run a human
  typed is defensible and is today's documented behaviour; keeping it as the default for a
  run started by a daemon is the hole described above. Whatever this repo cannot configure
  in the child, it must at least refuse to launch unattended.
- **An unanswered request is a denial, and it expires.** A terminal prompt blocks until a
  human types; over HTTP the human closes the tab. The request carries a deadline, the
  deadline resolves to `denied`, and the run continues as refused rather than leaving a
  subprocess parked forever. A pending approval that outlives the person who could answer
  it is a held door.
- **The decision never sees the agent's reasoning.** teacup-agent's approver already has
  the right shape: `approve(call, spec)` receives the tool call and the tool's own spec —
  not the transcript, not the model's argument for why the call is fine. Today that is true
  by accident of a signature; this item makes it a rule, because the failure it prevents is
  specific and one-way. **A model that can put text in front of its own approval check will
  eventually write text that approves it.** Whatever `serve` renders next to the button is
  built from those same two inputs.
- **Anything remembered is written where the user can read it.** "Approve calls like this
  from now on" is simultaneously the most useful button and the easiest way to leave someone
  holding a permission they cannot find or revoke. If it exists at all it is a line in a
  file under `$TEACUP_HOME`, append-only in the same style as #7's record — never in-memory
  state that dies with the daemon, and never inside a package, so `push_to_hub` cannot
  publish somebody's standing consent.
- **Approval is an event in the run's record.** #7's file already grows a line per turn;
  `requested` / `granted` / `denied` / `expired` are lines too, with which channel answered
  and how long it took. A run whose record does not say a call was approved is a run where
  it was not.

*Optional, and only if it pays for itself*: a one-sentence explanation of what the call will
actually do, shown beside the button — the difference between approving `write_file` and
approving `write_file("~/.bashrc", ...)`. It costs a model call on a path the user did not
ask to spend money on, so it is off by default and lands in the cost split #7 records. It is
generated from the call and the spec, for the same reason the decision is.

**Definition of done**: `teacup run` takes `--approve {auto,deny,allow,hooks,client}`; the
meaning of each of the four shared values is written once as a table in `docs/execution.md`,
and this repo's test asserts its own implementation against that table — *not* against the
sibling checkout, because teacup-agent is not a dependency of this repo (`pyproject.toml`
is `pyyaml` and `mcp`) and intent §4.5 requires everything checkable to stay hermetic and
free, so a test that imports the other repo cannot be written and a second hand-maintained
copy of the table is the drift it would be there to prevent; cross-implementation agreement
is item 6's conformance suite, and this item's job is to give it something unambiguous to
conform to. Then: `--approve` reaches the launched CLI rather than being hardcoded, and a
test asserts the argv actually built rather than the docstring describing it; a
side-effecting tool is denied when there is no channel to ask on, asserted under
`sandbox.py`'s real severed-stdin conditions rather than a mocked `isatty`; an approval whose
deadline passes resolves to `denied` and the run continues and says so; a test fails if the
transcript, the model's reasoning, or any other model-authored text is passed to the
approver, which takes the call and the spec and nothing else; every approval outcome appears
in #7's record; standing consent, if built, is a readable file the user can delete; and the
rules here are written into item 2's threat model when that document exists rather than
living only in this entry.

**Not this item**: which tools count as side-effecting in the first place. This item builds
the gate and decides who may open it; item 2 decides what has to go through it.
