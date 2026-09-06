# REVIEW.md

The independent review pass. One agent writes the change; a **different** agent, with its
own context and no memory of the arguments that produced the diff, reviews it. That
separation is the whole point — the author's context is exactly what hides the author's
mistakes.

This file is teacup-run's copy of the pass teacup-agent's `REVIEW.md` defines. The two
repos are reviewed the same way and the passes below do not change; what differs is the
list of rules this repo does not negotiate, because they are this repo's rules.

The reviewer may be any agent or person who did not write the code. A human decides what
merges.

## How to run it

```bash
git diff main...HEAD          # or the phase's diff, if the phase is several commits
```

Give the reviewer this file, the `README.md`, and whatever stated the change was supposed
to land — the issue, the PR body, the design note under `docs/`. Nothing else is needed: a
review that has to ask what the change was for is reviewing the wrong thing.

## The three passes

Run all three. They find different failures, and skipping one is how the cheap bug ships.

**1. Bugs and logic.** Does it do what it says under inputs that are not the happy path?
An agent that returns no JSON line, a subprocess that times out, a manifest field that is
absent where a default was assumed, a `None` where a dataclass was rebuilt from JSON. This
library's whole job is running *other people's* agents, so "what if the thing we launched
misbehaves" is the happy path's twin, not an edge case.

**2. Security.** This repo launches subprocesses, hands them credentials, and points them
at a checkout. Check what a change widens: the env allowlist (which variables actually
reach the child), anything that turns user or model text into a shell command or a file
path, `shell=True` appearing anywhere in `sandbox.py`'s launch path, secrets reaching a
log, a ledger, or a run directory, and any new write that lands inside a repo the caller
did not expect to be written to.

**3. Compliance.** Does the diff match what the change said it would do? Work that was not
asked for is as much a finding as work that was skipped. Then check the rules this repo
does not negotiate:

- **A coding task never commits, pushes, or opens a pull request.** It stops at a
  reviewable local branch with a diff and a test result attached. A human decides what
  happens next.
- **Every task gets its own disposable worktree.** A bad run must cost "delete a
  worktree", never "recover a working tree someone else is using".
- **`live=False` stays hermetic.** No network, no cost, no credential — it is what tests
  and dry runs use, and requiring a real key would make every hermetic test need one.
- **Cost is reported with the result, never separately.** A quality number without the
  dollars beside it is not a result this repo accepts.
- **Deny by default when nobody is watching.** A gated call with no approval policy and no
  TTY is denied; a coding task that therefore produces no side effects is the correct
  outcome, not a bug to route around.
- **We do not guess on the target's behalf.** `run_tests=True` with no `test_command` is a
  deliberate no-op rather than a guessed command dressed up as a result. Any new "helpful"
  inference belongs in this list as a finding until someone argues it in.

## What counts as a finding

**Important**, and worth blocking a merge: wrong behaviour, a security hole, a claim in
the report that the diff does not support, a documented default the code does not
implement, or an assumption about the target repo that is true for the one repo it was
tested against and silently false for others.

**Nit**, and capped at five per review: naming, comment wording, a tidier way to write a
correct line. Past five, summarize the rest in one line and move on — a review that
returns thirty nits gets read as thirty nits and its two real findings are lost.

**Not a finding**: style already settled by the surrounding code, and type-checking or
linting ceremony this repo has decided not to carry.

## What the reviewer must verify, not assume

- Run the suite and quote the output:

  ```bash
  uv run --with 'pytest>=8' python -m pytest -q
  ```

  `uv run pytest` alone fails to spawn — pytest lives in the `dev` extra, not the default
  environment. A green report from the author is a claim; checking it is part of the job.
- If the change touches how an external agent is launched, read the argv that is actually
  built, not the docstring describing it.
- If the change is behavioural, look for the before-and-after number. "It feels better" is
  not a measurement.
- If the report says something is unverified, that is acceptable — check the reason is the
  real one ("no API key in this shell" is; "should work" is not).
- **Assumptions about a target repo are the ones to distrust.** This library advertises
  driving *any* repo. A change validated against the sibling checkout next door has been
  validated against one data point; ask what it does to a repo with a different layout,
  different ignore rules, or no tests at all, and whether that failure is loud or silent.

## Output

One list, most severe first. Each finding: file and line, what breaks, and the input or
state that breaks it. No summary of what the diff does — the author knows, and the human
reading the review has the diff.

Findings the author disagrees with stay in the thread rather than being silently dropped;
the human deciding the merge reads both sides.
