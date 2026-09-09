"""`teacup run` — preflight, exit codes, output separation, and the --json shape.

No key and no spend: the model is faked through `loop.call_model`, which is the seam
`docs/execution.md` §8 names for exactly this. A CLI suite that needed a real key would
be a CLI suite nobody runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from teacup_run import cli
from teacup_run.cli import EXIT_BUDGET, EXIT_ERROR, EXIT_GOAL_NOT_MET, EXIT_OK, EXIT_PREFLIGHT

from conftest import text_reply, tool_reply

EXAMPLE = "examples/note-taker"


# The package's own checks require a real tool call — `has_action_items` fails on an
# answer that merely *mentions* one. So a "goal met" fake has to save an item first,
# which is the behaviour the package is asking for and therefore the right thing to
# fake.
GOOD = (
    tool_reply("save_action_item", {"what": "draft the pricing page", "owner": "Ray", "when": "Friday"}),
    text_reply("Action items\n- Ray: draft the pricing page (Friday)"),
)



@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """No config file, no inherited hub, no .env from this repo's own root.

    Without the chdir the cwd-upward search finds teacup-run's own `.env` and the
    preflight tests pass for the wrong reason — they would be asserting that a key
    exists on the developer's machine.
    """
    monkeypatch.setenv("TEACUP_CONFIG", str(tmp_path / "no-such-config.yaml"))
    monkeypatch.setenv("TEACUP_HOME", str(tmp_path / "hub"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")


@pytest.fixture
def example(tmp_path, monkeypatch) -> str:
    """An absolute ref, so tests do not depend on the working directory."""
    return str(Path(EXAMPLE).resolve())


def _fake(monkeypatch, *replies):
    """Fake the provider call, not `call_model`.

    `loop.run` takes `model_fn=call_model` as a *default argument*, bound when the
    function was defined — so patching `loop.call_model` afterwards changes nothing and
    the test silently exercises the real provider. Patching the provider dispatch keeps
    the whole path (loop -> call_model -> provider) real and fakes only the network.
    """
    from teacup_run import model as model_mod

    calls = []

    def fake_provider(model, messages, tools=()):
        calls.append(messages)
        return replies[min(len(calls) - 1, len(replies) - 1)]

    monkeypatch.setattr(model_mod, "_call_openai", fake_provider)
    return calls


# --- preflight ---------------------------------------------------------------


def test_a_missing_declared_variable_fails_before_any_spend(example, monkeypatch, capsys):
    """The failure this check exists for: a 401 halfway through a run has already cost
    money and thrown away the work; the same failure at preflight costs nothing."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    called = _fake(monkeypatch, text_reply("should never be reached"))

    code = cli.main(["run", example, "task", "--no-dotenv"])

    assert code == EXIT_PREFLIGHT
    assert called == [], "the model was called despite a failed preflight"
    err = capsys.readouterr().err
    assert "OPENAI_API_KEY" in err  # names the variable, not just "misconfigured"


def test_a_placeholder_counts_as_missing(example, monkeypatch, capsys):
    """A copied-but-unedited .env is the case every new user hits."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-...")
    code = cli.main(["run", example, "task", "--no-dotenv"])
    assert code == EXIT_PREFLIGHT
    assert "OPENAI_API_KEY" in capsys.readouterr().err


def test_a_bad_ref_fails_at_preflight(monkeypatch, capsys):
    code = cli.main(["run", "no/such/agent", "task", "--no-dotenv"])
    assert code == EXIT_PREFLIGHT
    assert "no/such/agent" in capsys.readouterr().err


def test_a_missing_env_file_is_named(example, capsys):
    code = cli.main(["run", example, "task", "--env-file", "/nonexistent/.env"])
    assert code == EXIT_PREFLIGHT
    assert "/nonexistent/.env" in capsys.readouterr().err


def test_the_preflight_echo_says_where_the_environment_came_from(example, monkeypatch, capsys):
    """"Why did it use that key" should be answerable without a debugger."""
    _fake(monkeypatch, text_reply("Action items\n- Ray: draft"))
    cli.main(["run", example, "task", "--no-dotenv"])
    err = capsys.readouterr().err
    assert "teacup/note-taker" in err and "gpt-5-mini" in err
    assert "process environment only" in err


# --- output separation -------------------------------------------------------


def test_the_answer_goes_to_stdout_and_the_ledger_to_stderr(example, monkeypatch, capsys):
    """So `teacup run ... > answer.txt` leaves a clean file with the accounting still
    on the terminal."""
    _fake(monkeypatch, text_reply("Action items\n- Ray: draft by Friday"))
    cli.main(["run", example, "notes", "--no-dotenv"])

    out, err = capsys.readouterr()
    assert out.strip() == "Action items\n- Ray: draft by Friday"
    assert "Total" in err  # the ledger
    assert "Total" not in out


def test_quiet_suppresses_the_echo_and_ledger_but_not_the_answer(example, monkeypatch, capsys):
    _fake(monkeypatch, text_reply("the answer"))
    cli.main(["run", example, "notes", "--no-dotenv", "-q"])
    out, err = capsys.readouterr()
    assert out.strip() == "the answer"
    assert err.strip() == ""


def test_a_task_of_dash_is_read_from_stdin(example, monkeypatch, capsys):
    _fake(monkeypatch, text_reply("ok"))
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("piped notes"))
    cli.main(["run", example, "-", "--no-dotenv", "--json"])
    assert json.loads(capsys.readouterr().out)["task"] == "piped notes"


# --- exit codes --------------------------------------------------------------


def test_exit_0_when_the_goal_is_met(example, monkeypatch):
    _fake(monkeypatch, *GOOD)
    assert cli.main(["run", example, "notes", "--no-dotenv"]) == EXIT_OK


def test_exit_1_when_the_run_completed_but_the_goal_was_not_met(example, monkeypatch):
    """An honest, completed, under-budget run that did not do the job. Distinguishable
    from a crash on purpose: CI reads these."""
    _fake(monkeypatch, text_reply("What would you like me to do?"))
    assert cli.main(["run", example, "notes", "--no-dotenv"]) == EXIT_GOAL_NOT_MET


def test_exit_2_when_the_budget_stopped_the_run(example, monkeypatch):
    _fake(monkeypatch, text_reply("anything"))
    assert cli.main(["run", example, "notes", "--no-dotenv", "--budget", "0"]) == EXIT_BUDGET


def test_exit_3_when_the_run_raised(example, monkeypatch):
    from teacup_run import model as model_mod

    def boom(model, messages, tools=()):
        raise RuntimeError("upstream is unhappy")

    monkeypatch.setattr(model_mod, "_call_openai", boom)
    assert cli.main(["run", example, "notes", "--no-dotenv"]) == EXIT_ERROR


def test_budget_and_error_are_told_apart_by_a_value_not_a_string(example, monkeypatch):
    """`stop_reason` is written for a human; parsing it to pick an exit code would
    break the moment the wording changed, which is why `stop_kind` exists."""
    _fake(monkeypatch, text_reply("x"))
    cli.main(["run", example, "notes", "--no-dotenv", "--budget", "0", "--json"])


# --- --json ------------------------------------------------------------------


def test_json_puts_exactly_one_object_on_stdout(example, monkeypatch, capsys):
    _fake(monkeypatch, text_reply("Action items\n- Ray: draft by Friday"))
    cli.main(["run", example, "notes", "--no-dotenv", "--json"])

    out = capsys.readouterr().out
    payload = json.loads(out)  # parses whole, so nothing else was printed
    assert out.count("\n") == 1


def test_the_json_shape_matches_the_documented_one(example, monkeypatch, capsys):
    """docs/execution.md §7 is the contract; this is it, key for key."""
    _fake(monkeypatch, *GOOD)
    cli.main(["run", example, "notes", "--no-dotenv", "--json"])
    d = json.loads(capsys.readouterr().out)

    assert set(d) == {
        "agent", "model", "task", "answer", "goal", "attempts", "tool_calls",
        "cost", "usage", "budget", "stopped", "dry_run", "elapsed_s", "exit_code",
    }
    assert set(d["agent"]) == {"name", "version", "ref"}
    assert set(d["goal"]) == {"met", "checks", "failed", "reasons"}
    assert set(d["cost"]) == {"model", "tool", "compute", "total"}
    assert set(d["usage"]) == {"input_tokens", "output_tokens", "cached_input_tokens"}
    assert set(d["budget"]) == {"usd", "remaining"}
    assert set(d["stopped"]) == {"early", "kind", "reason"}
    assert d["exit_code"] == EXIT_OK


def test_a_crashed_run_does_not_report_its_goal_as_met(example, monkeypatch, capsys):
    """No verdict has two causes. A package with no checks that completed did meet its
    empty goal; a run that stopped early never got a verdict, which is not passing."""
    from teacup_run import model as model_mod

    monkeypatch.setattr(
        model_mod, "_call_openai", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    cli.main(["run", example, "notes", "--no-dotenv", "--json"])
    d = json.loads(capsys.readouterr().out)

    assert d["stopped"]["early"] is True and d["stopped"]["kind"] == "error"
    assert d["goal"]["met"] is False
    assert d["exit_code"] == EXIT_ERROR


def test_budget_remaining_is_rounded_like_the_costs(example, monkeypatch, capsys):
    _fake(monkeypatch, text_reply("Action items\n- Ray: draft"))
    cli.main(["run", example, "notes", "--no-dotenv", "--json", "--budget", "0.10"])
    remaining = json.loads(capsys.readouterr().out)["budget"]["remaining"]
    assert remaining == round(remaining, 6)


# --- --dry-run ---------------------------------------------------------------


def test_dry_run_makes_no_model_call(example, monkeypatch, capsys):
    called = _fake(monkeypatch, text_reply("should never be reached"))
    code = cli.main(["run", example, "notes", "--no-dotenv", "--dry-run"])

    assert code == EXIT_OK
    assert called == []
    assert "says nothing about whether the agent is any good" in capsys.readouterr().err


def test_dry_run_still_refuses_a_missing_variable(example, monkeypatch):
    """It is a wiring check, and "am I configured to run this" is half the wiring."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert cli.main(["run", example, "notes", "--no-dotenv", "--dry-run"]) == EXIT_PREFLIGHT


# --- settings chain ----------------------------------------------------------


def test_the_config_file_supplies_a_budget_and_the_flag_beats_it(example, monkeypatch, capsys, tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("defaults:\n  budget_usd: 0.02\n", encoding="utf-8")
    monkeypatch.setenv("TEACUP_CONFIG", str(cfg))
    _fake(monkeypatch, text_reply("Action items\n- Ray: draft"))

    cli.main(["run", example, "notes", "--no-dotenv", "--json", "--budget", "0.50"])
    assert json.loads(capsys.readouterr().out)["budget"]["usd"] == 0.50


def test_the_config_file_can_only_point_at_secrets_never_hold_them(tmp_path, monkeypatch):
    """The separation that keeps this file safe to commit to a dotfiles repo."""
    from teacup_run.config import Config, load_config

    cfg = tmp_path / "config.yaml"
    cfg.write_text("env_file: ~/somewhere/secrets.env\n", encoding="utf-8")
    monkeypatch.setenv("TEACUP_CONFIG", str(cfg))

    loaded = load_config()
    assert loaded.env_file == Path("~/somewhere/secrets.env").expanduser()
    assert not any("KEY" in f.upper() for f in Config.__dataclass_fields__)


def test_an_absent_config_file_is_a_valid_state(tmp_path, monkeypatch):
    from teacup_run.config import Config, load_config

    monkeypatch.setenv("TEACUP_CONFIG", str(tmp_path / "nope.yaml"))
    monkeypatch.delenv("TEACUP_HOME", raising=False)
    assert load_config() == Config()


def test_the_config_budget_beats_the_packages_own(example, monkeypatch, capsys, tmp_path):
    """§4's precedence, and the half that is easy to get backwards. The person who owns
    the machine caps what a downloaded package may spend; a package declaring $5 must
    not override a config that says $0.50."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("defaults:\n  budget_usd: 0.02\n", encoding="utf-8")
    monkeypatch.setenv("TEACUP_CONFIG", str(cfg))
    _fake(monkeypatch, *GOOD)

    cli.main(["run", example, "notes", "--no-dotenv", "--json"])
    d = json.loads(capsys.readouterr().out)
    assert d["budget"]["usd"] == 0.02  # not the manifest's 0.25


def test_the_manifest_budget_is_used_when_the_config_names_none(example, monkeypatch, capsys, tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("defaults:\n  budget_usd: null\n", encoding="utf-8")
    monkeypatch.setenv("TEACUP_CONFIG", str(cfg))
    _fake(monkeypatch, *GOOD)

    cli.main(["run", example, "notes", "--no-dotenv", "--json"])
    assert json.loads(capsys.readouterr().out)["budget"]["usd"] == 0.25
