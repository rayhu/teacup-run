"""The manifest is the package's contract; it must fail loudly when it drifts."""

from __future__ import annotations

import pytest

from teacup_run.manifest import AgentSpec, ManifestError, parse_frontmatter, strip_frontmatter

MINIMAL = """
name: test/agent
version: 0.1.0
description: A test agent.
model:
  primary: gpt-5-mini
instructions: prompts/system.md
tools: [do_thing]
goal:
  description: Do the thing.
  checks: [non_empty]
  max_attempts: 2
budget:
  default_usd: 0.5
"""


def test_it_parses_the_documented_keys(tmp_path):
    spec = AgentSpec.load(tmp_path, manifest_text=MINIMAL)

    assert spec.name == "test/agent"
    assert spec.model_primary == "gpt-5-mini"
    assert spec.tools == ("do_thing",)
    assert spec.goal_checks == ("non_empty",)
    assert spec.goal_max_attempts == 2
    assert spec.budget_usd == 0.5
    assert spec.framework == "teacup"  # defaulted


@pytest.mark.parametrize("key", ["name", "version"])
def test_a_missing_required_key_is_named(tmp_path, key):
    broken = "\n".join(line for line in MINIMAL.splitlines() if not line.startswith(f"{key}:"))

    with pytest.raises(ManifestError, match=key):
        AgentSpec.load(tmp_path, manifest_text=broken)


def test_a_manifest_without_a_model_is_rejected(tmp_path):
    broken = MINIMAL.replace("  primary: gpt-5-mini", "  fallback: gpt-5")

    with pytest.raises(ManifestError, match="model.primary"):
        AgentSpec.load(tmp_path, manifest_text=broken)


def test_zero_attempts_is_rejected(tmp_path):
    with pytest.raises(ManifestError, match="max_attempts"):
        AgentSpec.load(tmp_path, manifest_text=MINIMAL.replace("max_attempts: 2", "max_attempts: 0"))


def test_an_unknown_tool_is_rejected_against_the_package(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "system.md").write_text("do things")
    spec = AgentSpec.load(tmp_path, manifest_text=MINIMAL)

    with pytest.raises(ManifestError, match="do_thing"):
        spec.validate(tools=set(), checks={"non_empty"})


def test_an_unknown_check_is_rejected(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "system.md").write_text("do things")
    spec = AgentSpec.load(tmp_path, manifest_text=MINIMAL)

    with pytest.raises(ManifestError, match="non_empty"):
        spec.validate(tools={"do_thing"}, checks=set())


def test_a_missing_prompt_file_is_reported_as_such(tmp_path):
    spec = AgentSpec.load(tmp_path, manifest_text=MINIMAL)

    with pytest.raises(ManifestError, match="prompts/system.md"):
        spec.instructions()


def test_frontmatter_round_trip():
    text = "---\nname: s\ndescription: d\n---\n\n# Body\n\ntext"

    assert parse_frontmatter(text) == {"name": "s", "description": "d"}
    assert strip_frontmatter(text).startswith("# Body")
    assert parse_frontmatter("no frontmatter") == {}


def test_the_example_agent_is_a_valid_package(note_taker_path):
    spec = AgentSpec.load(note_taker_path)

    assert spec.name == "teacup/note-taker"
    assert "concise-style" in spec.available_skills()
    assert spec.instructions()
