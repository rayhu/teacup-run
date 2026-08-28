"""The README's four lines, end to end, with the model faked."""

from __future__ import annotations

import subprocess

import pytest
import yaml

from conftest import FakeModel, text_reply, tool_reply
from opengraft import AutoAgent
from opengraft.manifest import AgentSpec, ManifestError
from opengraft.registry import RegistryError


def test_from_pretrained_loads_a_local_package(note_taker_path):
    agent = AutoAgent.from_pretrained(str(note_taker_path))

    assert agent.spec.name == "opengraft/note-taker"
    assert [t.name for t in agent.tools] == ["save_action_item", "list_action_items"]
    # The package's own checks are registered alongside the library's builtins.
    assert "has_action_items" in agent.checks
    assert "non_empty" in agent.checks


def test_publishing_reproduces_the_agent_directory(note_taker_path, git_hub):
    """The agent directory *is* the distributed artifact — one layout, not two.

    An agent whose published shape differs from its source shape needs glue to
    reconcile them, and that glue is where "works in my checkout, breaks after
    pull" lives. Flat means there is nothing to reconcile.
    """
    target = AutoAgent.from_pretrained(str(note_taker_path)).push_to_hub("ray/n", hub=git_hub)

    def shape(root):
        return sorted(
            p.relative_to(root).as_posix()
            for p in root.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        )

    assert shape(target) == shape(note_taker_path)


def test_publishing_leaves_dev_residue_and_secrets_behind(agent_copy, git_hub):
    """The one thing the shape check above cannot see.

    It compares source to target, so anything copied to *both* sides matches.
    An agent directory is often a repository root, and a published `.git` gives
    the hub an embedded clone its own `git add` cannot represent.
    """
    subprocess.run(["git", "init", "-q"], cwd=agent_copy, check=True)
    (agent_copy / ".env").write_text("OPENAI_API_KEY=sk-real-secret\n")
    (agent_copy / ".venv" / "lib").mkdir(parents=True)
    (agent_copy / ".venv" / "lib" / "big.bin").write_text("x")

    target = AutoAgent.from_pretrained(str(agent_copy)).push_to_hub("ray/clean", hub=git_hub)

    assert not (target / ".git").exists()
    assert not (target / ".env").exists()
    assert not (target / ".venv").exists()
    assert (target / "agent.yaml").is_file()      # the agent itself still travelled
    assert (target / "evals" / "benchmark.yaml").is_file()


def test_add_skill_appends_the_skill_body(note_taker_path):
    agent = AutoAgent.from_pretrained(str(note_taker_path))
    before = agent.instructions()

    agent.add_skill("concise-style")

    assert "Concise style" in agent.instructions()
    assert len(agent.instructions()) > len(before)


def test_add_skill_is_idempotent_and_rejects_unknown_names(note_taker_path):
    agent = AutoAgent.from_pretrained(str(note_taker_path))
    agent.add_skill("concise-style").add_skill("concise-style")

    assert agent.instructions().count("Concise style") == 1

    with pytest.raises(ManifestError, match="pdf-analysis"):
        agent.add_skill("pdf-analysis")


def test_an_unresolvable_reference_says_so(tmp_path):
    with pytest.raises(RegistryError, match="not in the hub"):
        AutoAgent.from_pretrained("nobody/nothing", hub=tmp_path / "hub")


def test_run_uses_the_packages_tools_and_checks(note_taker_path):
    agent = AutoAgent.from_pretrained(str(note_taker_path))
    model = FakeModel(
        tool_reply("save_action_item", {"what": "ship the pricing page", "owner": "Ray"}),
        text_reply("Action items\n- Ray: ship the pricing page"),
    )

    result = agent.run("Notes: Ray ships the pricing page Friday.", model_fn=model)

    assert result.artifacts["action_items"] == [
        {"what": "ship the pricing page", "owner": "Ray", "when": ""}
    ]
    assert result.goal.met
    assert result.attempts == 1


def test_the_goal_loop_retries_an_answer_that_omits_what_it_recorded(note_taker_path):
    """The package's own `items_appear_in_answer` check drives the retry."""
    agent = AutoAgent.from_pretrained(str(note_taker_path))
    model = FakeModel(
        tool_reply("save_action_item", {"what": "ship the pricing page", "owner": "Ray"}),
        text_reply("I looked at the notes."),          # recorded, but not listed
        text_reply("Action items\n- Ray: ship the pricing page"),
    )

    result = agent.run("Notes: Ray ships the pricing page Friday.", model_fn=model)

    assert result.attempts == 2
    assert result.goal.met
    assert "pricing page" in result.answer


def test_goal_loop_can_be_turned_off_for_a_baseline(note_taker_path):
    agent = AutoAgent.from_pretrained(str(note_taker_path))
    model = FakeModel(text_reply("nothing recorded"), text_reply("unused"))

    result = agent.run("Notes: nothing.", goal_loop=False, model_fn=model)

    assert result.attempts == 1
    assert result.goal is None


def test_eval_reports_quality_and_cost(note_taker_path):
    agent = AutoAgent.from_pretrained(str(note_taker_path))
    replies = []
    for _ in range(len(_tasks(agent)) * 6):
        replies.append(text_reply("Action items\n- Ray: ship the pricing page (Friday)"))
    model = FakeModel(*replies)

    report = agent.eval(budget=1.0, model_fn=model)

    assert len(report.rows) == len(_tasks(agent))
    summary = report.summary("default")
    assert 0.0 <= summary["quality"] <= 1.0
    assert summary["cost"] > 0
    assert "Quality/$" in report.render()


def test_eval_can_compare_both_arms(note_taker_path):
    agent = AutoAgent.from_pretrained(str(note_taker_path))
    model = FakeModel(*[text_reply("Ray: pricing (Friday)") for _ in range(60)])

    report = agent.eval(budget=1.0, compare="goal_loop", model_fn=model)

    assert report.arms == ("baseline", "goal-loop")
    assert report.for_arm("baseline") and report.for_arm("goal-loop")
    # The baseline never checks its goal; the goal-loop arm always does.
    assert all(r.goal_met is None for r in report.for_arm("baseline"))
    assert all(r.goal_met is not None for r in report.for_arm("goal-loop"))


def test_push_to_hub_then_pull_it_back(agent_copy, git_hub):
    agent = AutoAgent.from_pretrained(str(agent_copy))
    agent.add_skill("concise-style").set_model("gpt-5")

    target = agent.push_to_hub("ray/note-taker-plus", hub=git_hub)

    assert (target / "agent.yaml").is_file()
    assert (target / "prompts" / "system.md").is_file()   # runtime files survive publishing

    published = AutoAgent.from_pretrained("ray/note-taker-plus", hub=git_hub)

    assert published.spec.name == "ray/note-taker-plus"
    assert published.spec.derived_from == "opengraft/note-taker"   # lineage recorded
    assert published.spec.version == "0.1.1"                       # version bumped
    assert published.spec.model_primary == "gpt-5"                 # the override travels
    assert "concise-style" in published.spec.skills                # so does the skill


def test_publishing_commits_to_the_hub_repo(agent_copy, git_hub):
    AutoAgent.from_pretrained(str(agent_copy)).push_to_hub("ray/n", hub=git_hub)

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=git_hub, capture_output=True, text=True, check=True
    )

    assert "Publish ray/n" in log.stdout


def test_a_published_manifest_is_still_valid_yaml(agent_copy, git_hub):
    target = AutoAgent.from_pretrained(str(agent_copy)).push_to_hub("ray/n", hub=git_hub)
    manifest = yaml.safe_load((target / "agent.yaml").read_text())

    assert manifest["name"] == "ray/n"
    assert manifest["goal"]["checks"]        # the goal survived the round trip
    AgentSpec.load(target)                   # and it still parses


def _tasks(agent):
    import yaml as _yaml

    path = agent.spec.root / "evals" / "benchmark.yaml"
    return _yaml.safe_load(path.read_text())["tasks"]


def test_a_pulled_agent_can_still_be_evaluated(agent_copy, git_hub):
    """`from_pretrained` -> `eval` is the flow the README sells; publishing must not break it.

    `evals/` lives in the agent directory, so publishing carries it for free —
    which is what lets a fork be measured against the upstream it came from.
    """
    AutoAgent.from_pretrained(str(agent_copy)).push_to_hub("ray/evaluable", hub=git_hub)
    pulled = AutoAgent.from_pretrained("ray/evaluable", hub=git_hub)

    model = FakeModel(*[text_reply("Ray: pricing (Friday)") for _ in range(20)])
    report = pulled.eval(budget=1.0, model_fn=model)

    assert report.rows
    assert (git_hub / "ray" / "evaluable" / "AGENT.md").is_file()
