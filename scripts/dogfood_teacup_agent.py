"""Phase 4 dogfood run: drive a real teacup-agent checkout, through this repo's
own coding_task.py, to fix a real, already-scoped gap in teacup-agent's own
source (docs/roadmap.md's #20 "Known gap" writeup: agent.yaml's ToolsConfig has
no coding_tools field). Live (real OpenAI API call), small budget. Never
commits, pushes, or opens a PR — this only produces a local branch + worktree
for a human to review.

Run from the teacup-run repo root:
    export OPENAI_API_KEY=sk-...          # your own key, your own shell only
    uv run python scripts/dogfood_teacup_agent.py

Reads OPENAI_API_KEY from your shell's own environment — never hardcode it
here, never paste it into chat.

Assumes:
- teacup-agent is cloned as a sibling directory to this teacup-run checkout
  (set TEACUP_AGENT_DIR in your environment if it lives somewhere else).
- That teacup-agent checkout has `main` fetched and up to date. The dogfood
  task branches off `main`, which is where the pieces it depends on now live:
  hooks.py, which approves edit_file/write_file unconditionally, landed on main
  in rayhu/teacup-agent#13, and main additionally carries #14 — the edit_file
  description that tells the model to retry with a shorter one-line anchor
  rather than give up when a long old_string fails to match.

  This used to pin `phase4/hooks-and-dogfood` and must not go back to it.
  That branch was merged into main by #13, so pinning it no longer isolates
  anything — it just silently runs the agent with a two-commits-stale prompt.
  Observed cost of the stale pin: a run that landed 1 of this task's 4 edits
  and then stopped to explain, in prose, what a human should type to finish
  the job — "I could not reliably locate the exact strings to edit" — which is
  the exact failure #14 was written to fix.
- This teacup-run checkout is on (or has merged) phase4/coding-task-model-flag,
  which is what adds the `model` and `max_steps` params run_coding_task uses
  below (rayhu/teacup-run#4).
- `uv sync` already run in both (or `uv run` will do it on first use).

Task text deliberately does NOT ask the model to read docs/roadmap.md for
background first: an earlier attempt did, and roadmap.md is 1800+ lines, which
burned most of an 8-step budget just locating the relevant paragraph before a
single edit was made. The five numbered steps below already fully specify
every change, so there is nothing left for a "read for context" detour to add
— removing it removes that whole failure mode.

Task step 2 deliberately does not describe the existing `ToolsConfig(...)`
argument list. It used to, and it described it wrongly: it said `exclude=`,
`subagents=` and `subagent_max_steps=` were all read "from `tools_raw`", when
only `exclude=` is — the other two come from `subagents_raw`. A wrong
description is worse than none, because the model treats it as a verbatim
anchor: across two runs it spent six `edit_file` calls trying to match
`subagent_max_steps=tools_raw.get(...)`, a string that has never existed in
that file, and one of those runs escalated to renaming `load()` and leaving
the suite 28 tests red. Describe where to look, not what will be there.

max_steps=30 (teacup-agent's own CLI default is 8) is deliberately raised: a
coding task realistically needs "edit N files, add a test, run the suite" in
one run, which is a different shape of budget than a quick lookup.

30, and the history of that number is worth keeping, because raising it is
only right under one specific condition. Raising 16 to 24 while the model
could not actually read the file it was editing made things strictly worse:
it renamed `load` to `_original_load` intending to wrap it, never landed the
wrapper, and left the suite 28 tests red. Extra steps given to a model that is
stuck buy nothing but room to improvise destructively. Once visibility was
fixed (coding_task.py puts the agent's run dir inside the worktree) and the
task text stopped misdescribing the code, a 16-step run landed 3 of 4 edits,
cleanly, and stopped at step 16/16 having just read tests/test_agent_config.py
— out of steps with useful work in flight, with 86% of its dollar budget
unspent. That, and only that, is the condition under which this number should
go up: the run is doing correct work and the ceiling is what stops it. It then
did the same thing again at 22.

30 is sized against how this harness actually spends steps, rather than nudged
up one more time. Reading a file large enough to externalize costs *two* steps
by design, not one: teacup-agent hands the model a 600-char excerpt plus a path
and expects it to read that path back (tools.py keeps `runs/` off the deny-list
for exactly this reason). This task touches three files, two of them well over
the 2000-char threshold, and the model re-reads as it goes — a 22-step run
spent 8 steps on those round-trips alone. Three files to read, four edits to
land, a test to write and a suite to run does not fit in 22 once two-thirds of
the reads cost double. Budget is not the constraint at this size either: the
22-step run still returned 88% of its $0.25 unspent.
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent  # this teacup-run checkout
TEACUP_AGENT_DIR = Path(
    os.environ.get("TEACUP_AGENT_DIR", REPO_ROOT.parent / "teacup-agent")
)

if not os.environ.get("OPENAI_API_KEY"):
    sys.exit("ERROR: export OPENAI_API_KEY=sk-... in your shell first, then re-run.")
if not TEACUP_AGENT_DIR.is_dir():
    sys.exit(
        f"ERROR: expected a teacup-agent checkout at {TEACUP_AGENT_DIR} — "
        "set TEACUP_AGENT_DIR in your environment if it lives somewhere else."
    )

from teacup_run.manifest import AgentSpec
from teacup_run.coding_task import run_coding_task

TASK = """\
Make exactly this change to this repo (teacup-agent):

1. In src/teacup_agent/agent_config.py: add a new field `coding_tools: bool \
= False` to the `ToolsConfig` dataclass, right after the existing \
`subagent_max_steps: int = 4` field.

2. In the same file, inside the `load()` function, find where `ToolsConfig(...)` \
is constructed and assigned to `tools_cfg`. Read the file and match its exact \
current text rather than assuming the argument list looks a particular way. Add \
one more keyword argument to that call, keeping the existing ones unchanged and \
on their own lines: \
`coding_tools=bool(tools_raw.get("coding_tools", False)),`

3. In src/teacup_agent/cli.py: find the `loop.run(...)` call that passes \
`subagents=cfg.tools.subagents,` and `subagent_max_steps=cfg.tools.subagent_max_steps,` \
(this is the --config agent.yaml execution path). Add \
`coding_tools=cfg.tools.coding_tools,` as a new keyword argument right after \
`subagent_max_steps=cfg.tools.subagent_max_steps,`.

4. Add one new test to tests/test_agent_config.py that loads a minimal YAML config \
with `tools:\\n  coding_tools: true` and asserts `cfg.tools.coding_tools is True`, \
and confirms the default (no `tools.coding_tools` key at all) is `False`. Follow \
the exact style of the existing tests in that file for how a minimal config is \
built and loaded — read the file first.

5. Run `uv run pytest -q`. The suite must end green, including your new test. \
If it reports any failure or error, that is your bug and it is part of this task: \
read the traceback, fix what you broke, and run it again. Repeat until it is green. \
Do not stop while the suite is red, and do not report success without having seen a \
green run — an unfinished edit is recoverable, a broken suite reported as done is not.

Two mistakes have actually been made on this task before, both by editing carelessly \
rather than by misunderstanding it, and `uv run pytest -q` catches both. When you add \
a line next to existing ones, re-read the file afterwards and check that you inserted \
one line and changed nothing else: (a) the argument you were told to add ended up \
indented differently from the arguments around it, and (b) a keyword argument that was \
already there got written out a second time, so the call passed it twice and every \
module importing it failed to load.

Do not change anything else. Do not touch docs/roadmap.md itself. This is a small, \
additive, backward-compatible change — existing YAML configs with no `coding_tools` \
key must keep behaving exactly as they do today.\
"""


def main():
    spec = AgentSpec.load(REPO_ROOT / "examples" / "teacup-agent-bridge")
    result = run_coding_task(
        spec,
        TASK,
        target_repo=TEACUP_AGENT_DIR,
        base_branch="main",
        live=True,
        model="gpt-5-mini",
        max_steps=30,
        budget=0.25,
        timeout=600,
        test_command="uv run pytest -q",
        test_timeout=180,
    )
    print("=" * 70)
    print("ANSWER:", result.result.answer)
    print("STOPPED_EARLY:", result.result.stopped_early)
    print("STOP_REASON:", result.result.stop_reason)
    print("BRANCH:", result.branch)
    print("WORKTREE:", result.worktree_path)
    print("FILES_CHANGED:", result.files_changed)
    print("COMMITS_MADE:", result.commits_made)
    print("DIFF_STAT:")
    print(result.diff_stat)
    print("TESTS_PASSED:", result.tests_passed)
    print("TEST_OUTPUT (tail):")
    print((result.test_output or "")[-2000:])
    print("LEDGER:")
    print(result.result.render_ledger())
    print()
    print("AGENT_ARTIFACTS:", result.agent_artifacts_path)
    print(f"Review it yourself: cd {result.worktree_path} && git diff {result.base_branch}")
    print("Nothing was pushed or opened as a PR — that's your call once you've reviewed it.")


if __name__ == "__main__":
    main()
