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


# --- AGENTS.md compatibility ----------------------------------------------------


def test_agents_md_is_none_when_absent(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "system.md").write_text("do things")
    spec = AgentSpec.load(tmp_path, manifest_text=MINIMAL)
    assert spec.agents_md() is None


def test_agents_md_is_read_when_present(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "system.md").write_text("do things")
    (tmp_path / "AGENTS.md").write_text("Repo conventions: use uv, not pip.  ")
    spec = AgentSpec.load(tmp_path, manifest_text=MINIMAL)
    assert spec.agents_md() == "Repo conventions: use uv, not pip."


def test_instructions_combines_agents_md_and_the_packages_own_file(tmp_path):
    """AGENTS.md is background context; the package's own instructions are the
    specific, authored persona — both matter, so neither is dropped."""
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "system.md").write_text("You are a helpful assistant.")
    (tmp_path / "AGENTS.md").write_text("Repo conventions: use uv, not pip.")
    spec = AgentSpec.load(tmp_path, manifest_text=MINIMAL)

    combined = spec.instructions()
    assert "Repo conventions: use uv, not pip." in combined
    assert "You are a helpful assistant." in combined
    # AGENTS.md reads as background, the package's own voice comes after it
    assert combined.index("Repo conventions") < combined.index("You are a helpful")


def test_agents_md_alone_satisfies_instructions_when_the_prompt_file_is_missing(tmp_path):
    """A directory that only has an AGENTS.md — no prompts/system.md at all — should
    still work as a Teacup Run package, not fail validation over a file the
    ecosystem-standard convention already covers."""
    (tmp_path / "AGENTS.md").write_text("Just use AGENTS.md for everything.")
    spec = AgentSpec.load(tmp_path, manifest_text=MINIMAL)
    assert spec.instructions() == "Just use AGENTS.md for everything."


def test_missing_both_the_prompt_file_and_agents_md_still_raises(tmp_path):
    spec = AgentSpec.load(tmp_path, manifest_text=MINIMAL)
    with pytest.raises(ManifestError, match="prompts/system.md"):
        spec.instructions()


def test_the_example_agent_is_a_valid_package(note_taker_path):
    spec = AgentSpec.load(note_taker_path)

    assert spec.name == "teacup/note-taker"
    assert "concise-style" in spec.available_skills()
    assert spec.instructions()


# --- Agent Skills spec conformance (skill_meta / available_skills) ------------


def _agent_with_skill(tmp_path, skill_name, skill_md_text):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "system.md").write_text("do things")
    skill_dir = tmp_path / "skills" / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(skill_md_text, encoding="utf-8")
    return AgentSpec.load(tmp_path, manifest_text=MINIMAL)


def test_skill_meta_parses_the_spec_optional_fields(tmp_path):
    spec = _agent_with_skill(
        tmp_path,
        "full-metadata",
        """---
name: full-metadata
description: exercises every optional field the spec defines.
license: Apache-2.0
compatibility: requires git and docker
allowed-tools: read_file run_command
metadata:
  author: someone
---

Body.""",
    )
    meta = spec.skill_meta("full-metadata")
    assert meta.license == "Apache-2.0"
    assert meta.compatibility == "requires git and docker"
    assert meta.allowed_tools == ("read_file", "run_command")
    assert meta.metadata == {"author": "someone"}


def test_skill_meta_is_none_without_a_description(tmp_path):
    spec = _agent_with_skill(tmp_path, "broken", "---\nname: broken\n---\n\nBody.")
    assert spec.skill_meta("broken") is None


def test_a_name_disagreeing_with_its_folder_is_not_available(tmp_path):
    """Same conformance rule teacup-agent's skills.py applies: name must equal the
    folder discover() found it under, or the skill answers to two identities."""
    spec = _agent_with_skill(
        tmp_path, "the-folder", "---\nname: a-different-name\ndescription: d.\n---\n\nBody."
    )
    assert "the-folder" not in spec.available_skills()
    assert spec.skill_meta("the-folder") is None


def test_a_spec_illegal_name_is_not_available(tmp_path):
    spec = _agent_with_skill(tmp_path, "Bad_Name", "---\nname: Bad_Name\ndescription: d.\n---\n\nBody.")
    assert "Bad_Name" not in spec.available_skills()


def test_a_description_past_the_spec_limit_is_capped(tmp_path):
    spec = _agent_with_skill(
        tmp_path, "long-desc", f"---\nname: long-desc\ndescription: {'x' * 2000}\n---\n\nBody."
    )
    assert len(spec.skill_meta("long-desc").description) == 1024


def test_the_example_agents_skill_has_no_optional_fields_declared(note_taker_path):
    """The shipped example predates the optional fields; parsing it must not require
    them, and it should come back with the same empty defaults a bare skill gets."""
    spec = AgentSpec.load(note_taker_path)
    meta = spec.skill_meta("concise-style")
    assert meta.license is None
    assert meta.metadata == {}
    assert meta.allowed_tools == ()


def test_a_reserved_word_in_the_name_is_not_available(tmp_path):
    """The spec forbids "claude"/"anthropic" in a skill name — distinct from the
    folder-match and character-set rules, and previously unenforced here."""
    spec = _agent_with_skill(
        tmp_path, "claude-helper", "---\nname: claude-helper\ndescription: d.\n---\n\nBody."
    )
    assert "claude-helper" not in spec.available_skills()
    assert spec.skill_meta("claude-helper") is None


def test_a_skill_with_no_body_is_not_available(tmp_path):
    """Consistent with teacup-agent's own discover(), which already requires a
    non-empty body: a skill with nothing to load is as unusable as one with no
    description, and the two repos must agree on that."""
    spec = _agent_with_skill(tmp_path, "empty-body", "---\nname: empty-body\ndescription: d.\n---\n")
    assert "empty-body" not in spec.available_skills()
    assert spec.skill_meta("empty-body") is None


def test_validate_distinguishes_a_missing_skill_from_an_invalid_one(tmp_path):
    """A SKILL.md that exists but fails spec validation must not be reported as "no
    SKILL.md" — that sends a human looking for a file that is right there."""
    spec = _agent_with_skill(
        tmp_path, "the-folder", "---\nname: a-different-name\ndescription: d.\n---\n\nBody."
    )
    manifest_text = MINIMAL.replace("tools: [do_thing]", "tools: [do_thing]\nskills: [the-folder]")
    spec = AgentSpec.load(tmp_path, manifest_text=manifest_text)

    with pytest.raises(ManifestError, match="fails Agent Skills validation"):
        spec.validate(tools={"do_thing"}, checks={"non_empty"})


def test_validate_reports_a_truly_missing_skill_as_such(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "system.md").write_text("do things")
    manifest_text = MINIMAL.replace("tools: [do_thing]", "tools: [do_thing]\nskills: [nowhere]")
    spec = AgentSpec.load(tmp_path, manifest_text=manifest_text)

    with pytest.raises(ManifestError, match="no SKILL.md: nowhere"):
        spec.validate(tools={"do_thing"}, checks={"non_empty"})
