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
- In that teacup-agent checkout: `git fetch origin phase4/hooks-and-dogfood &&
  git checkout phase4/hooks-and-dogfood` — this branch has the real hooks.py
  that approves edit_file/write_file unconditionally (rayhu/teacup-agent#13),
  plus the SYSTEM_PROMPT fix that tells the model to try a different tool
  before giving up on a denial (Field patch I). The dogfood task branches off
  this ref, not main, since main doesn't have hooks.py yet.
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

max_steps=16 (teacup-agent's own CLI default is 8) is deliberately raised: a
coding task realistically needs "edit N files, add a test, run the suite" in
one run, which is a different shape of budget than a quick lookup.
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
is constructed (it currently sets `exclude=`, `subagents=`, `subagent_max_steps=` \
from `tools_raw`). Add a fourth keyword argument: \
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

5. Run `uv run pytest` and confirm the full suite passes, including your new test.

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
        base_branch="phase4/hooks-and-dogfood",
        live=True,
        model="gpt-5-mini",
        max_steps=16,
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
    print(f"Review it yourself: cd {result.worktree_path} && git diff {result.base_branch}")
    print("Nothing was pushed or opened as a PR — that's your call once you've reviewed it.")


if __name__ == "__main__":
    main()
