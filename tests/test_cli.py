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
from teacup_run.manifest import AgentSpec
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
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TEACUP_CONFIG", str(tmp_path / "no-such-config.yaml"))
    monkeypatch.setenv("TEACUP_HOME", str(tmp_path / "hub"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def example() -> str:
    """An absolute ref resolved against the repo, not the cwd — `_isolate` chdirs into
    a tmp_path, and resolving against the cwd would break every test from anywhere but
    the repo root anyway."""
    return str(REPO_ROOT / EXAMPLE)


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

    # All three branches, not just the one examples/note-taker happens to use. Patching
    # only `_call_openai` fails *open*: `--model claude-opus-5` bypasses the fake
    # entirely, and it is an accident of this environment (no `anthropic` installed)
    # that such a test errors instead of calling out. The `dev` extra pins openai, so
    # the OpenAI branch really is live on a `.[dev]` install.
    for provider in ("_call_openai", "_call_anthropic", "_call_google"):
        monkeypatch.setattr(model_mod, provider, fake_provider)
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


def test_budget_and_error_are_told_apart_by_a_value_not_a_string(example, monkeypatch, capsys):
    """`stop_reason` is written for a human; parsing it to pick an exit code would break
    the moment the wording changed, which is why `stop_kind` exists. Asserted on the
    value rather than on the exit code, which `test_exit_2`/`test_exit_3` already
    cover — an earlier version of this test asserted nothing at all."""
    _fake(monkeypatch, text_reply("x"))
    cli.main(["run", example, "notes", "--no-dotenv", "--budget", "0", "--json"])
    budget_stop = json.loads(capsys.readouterr().out)["stopped"]

    from teacup_run import model as model_mod

    monkeypatch.setattr(
        model_mod, "_call_openai", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    cli.main(["run", example, "notes", "--no-dotenv", "--json"])
    error_stop = json.loads(capsys.readouterr().out)["stopped"]

    assert budget_stop["kind"] == "budget" and error_stop["kind"] == "error"
    assert budget_stop["reason"] != error_stop["reason"]


# --- --json ------------------------------------------------------------------


def test_json_puts_exactly_one_object_on_stdout(example, monkeypatch, capsys):
    _fake(monkeypatch, text_reply("Action items\n- Ray: draft by Friday"))
    cli.main(["run", example, "notes", "--no-dotenv", "--json"])

    out = capsys.readouterr().out
    json.loads(out)  # parses whole, so nothing else was printed
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


# --- the non-native backend --------------------------------------------------


def test_a_sandboxed_run_that_stopped_early_does_not_exit_zero(example, monkeypatch):
    """`AutoAgent.run` hands `framework != "teacup"` to `run_external`, whose Results
    set `stopped_early` and a prose reason. Without a `stop_kind` the exit table read
    them as success, so a timed-out sandboxed run reported 0 to CI while its own JSON
    said `stopped.early: true`."""
    from teacup_run import cli as cli_mod
    from teacup_run.budget import Ledger
    from teacup_run.loop import Result

    def stopped(*a, **k):
        return Result(
            answer="",
            ledger=Ledger(),
            stopped_early=True,
            stop_reason="sandboxed run timed out after 30s",
            stop_kind="error",
        )

    monkeypatch.setattr(cli_mod.AutoAgent, "run", lambda self, *a, **k: stopped())
    assert cli.main(["run", example, "notes", "--no-dotenv"]) == EXIT_ERROR


def test_an_unclassified_early_stop_is_not_reported_as_success(example, monkeypatch):
    """Belt and braces for a backend that forgets to say why it stopped: `stopped_early`
    with no kind must still not be 0."""
    from teacup_run import cli as cli_mod
    from teacup_run.budget import Ledger
    from teacup_run.loop import Result

    monkeypatch.setattr(
        cli_mod.AutoAgent,
        "run",
        lambda self, *a, **k: Result(answer="", ledger=Ledger(), stopped_early=True, stop_reason="?"),
    )
    assert cli.main(["run", example, "notes", "--no-dotenv"]) == EXIT_ERROR


def test_an_overspent_budget_reports_a_negative_remainder(example, monkeypatch, capsys):
    """The budget is checked *before* a call, so the call that trips it has already been
    paid for and a run can overshoot. Clamping the JSON field at zero would make
    `budget.usd - cost.total` disagree with `budget.remaining`, leaving the size of the
    overrun only in the prose reason."""
    _fake(monkeypatch, *GOOD)
    cli.main(["run", example, "notes", "--no-dotenv", "--json", "--budget", "0.0000001"])
    d = json.loads(capsys.readouterr().out)

    assert d["budget"]["remaining"] == pytest.approx(
        d["budget"]["usd"] - d["cost"]["total"], abs=1e-6
    )


def test_the_human_ledger_still_floors_the_remainder_at_zero(example, monkeypatch, capsys):
    """A negative allowance is not a thing to show a person; the clamp is a display
    choice and stays in render()."""
    _fake(monkeypatch, *GOOD)
    cli.main(["run", example, "notes", "--no-dotenv", "--budget", "0.0000001"])
    err = capsys.readouterr().err
    assert "Remaining" in err
    assert "-$" not in err and "$-" not in err


def test_a_quoted_placeholder_is_still_a_placeholder(example, monkeypatch):
    """env.py strips quotes off a value it loads; missing_environment must strip them
    the same way, or an exported OPENAI_API_KEY='sk-...' is a placeholder to one and a
    real key to the other."""
    monkeypatch.setenv("OPENAI_API_KEY", "'sk-...'")
    assert cli.main(["run", example, "task", "--no-dotenv"]) == EXIT_PREFLIGHT


def test_the_model_flag_reaches_the_run(example, monkeypatch, capsys):
    """Untested until now, and the flag whose absence made the provider fake fail open."""
    _fake(monkeypatch, *GOOD)
    cli.main(["run", example, "notes", "--no-dotenv", "--json", "--model", "claude-opus-5"])
    assert json.loads(capsys.readouterr().out)["model"] == "claude-opus-5"


def test_no_config_file_leaves_the_packages_own_budget_alone(example, monkeypatch, capsys):
    """§4 puts built-in defaults *last*, after the manifest. Parking the 1.00 default on
    the config tier made "no config file" — the documented normal state — silently give
    a package 4x the ceiling its author declared."""
    _fake(monkeypatch, *GOOD)
    cli.main(["run", example, "notes", "--no-dotenv", "--json"])
    assert json.loads(capsys.readouterr().out)["budget"]["usd"] == 0.25


def test_a_git_url_is_refused_unless_the_config_allows_pulling(tmp_path, monkeypatch, capsys):
    """Fetching a ref runs the package's own Python: `registry.resolve` clones and
    `from_pretrained` imports the clone's tools.py. `hub.auto_pull` is documented as the
    gate, and without it one shell command executes a stranger's code — under
    `--dry-run` too, before it decides not to call a model."""
    code = cli.main(["run", "https://example.invalid/some/agent.git", "task", "--no-dotenv", "--dry-run"])
    assert code == EXIT_PREFLIGHT
    err = capsys.readouterr().err
    assert "auto_pull" in err and "runs the package's own Python" in err


def test_the_gate_lets_a_local_path_through(example, monkeypatch):
    """Only the first, network-touching resolution is gated; a package already on disk
    is not a fetch."""
    _fake(monkeypatch, *GOOD)
    assert cli.main(["run", example, "notes", "--no-dotenv"]) == EXIT_OK


def test_a_config_path_that_does_not_exist_is_an_error(example, tmp_path, capsys):
    """Typed on the command line this second. Falling back to defaults would run the
    agent under settings nobody asked for and say nothing."""
    code = cli.main(["run", example, "task", "--no-dotenv", "--config", str(tmp_path / "typo.yaml")])
    assert code == EXIT_PREFLIGHT
    assert "typo.yaml" in capsys.readouterr().err


def test_a_config_env_file_that_is_absent_is_not_fatal(example, tmp_path, monkeypatch, capsys):
    """The config is documented as safe to commit to a dotfiles repo, so it is expected
    to land on machines where that path does not exist and where the process
    environment supplies the credentials instead."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"env_file: {tmp_path / 'absent.env'}\n", encoding="utf-8")
    monkeypatch.setenv("TEACUP_CONFIG", str(cfg))
    _fake(monkeypatch, *GOOD)

    assert cli.main(["run", example, "notes", "--no-dotenv"]) == EXIT_OK
    assert "absent.env" in capsys.readouterr().err  # said, not silently ignored


@pytest.mark.parametrize(
    "sandbox_kwargs, expect_kind",
    [
        ({"timed_out": True, "returncode": None, "stdout": "", "stderr": ""}, "error"),
        ({"timed_out": False, "returncode": 2, "stdout": "", "stderr": "boom"}, "error"),
        ({"timed_out": False, "returncode": 0, "stdout": "not json", "stderr": ""}, "error"),
    ],
    ids=["timed out", "crashed", "unparsable json"],
)
def test_every_early_stop_from_the_external_backend_says_why(
    monkeypatch, tmp_path, sandbox_kwargs, expect_kind
):
    """Driven through `run_external` itself, not a stand-in Result.

    Each of these built a Result with `stopped_early=True` and a prose reason and no
    `stop_kind`, so `cli._exit_code` — which branches on the kind — read a timed-out
    sandboxed run as success and returned 0 to CI.
    """
    from teacup_run import external_cli
    from teacup_run.sandbox import SandboxResult

    monkeypatch.setattr(
        external_cli,
        "run_sandboxed",
        lambda *a, **k: SandboxResult(elapsed_s=0.0, limits_applied=True, **sandbox_kwargs),
    )
    spec = AgentSpec.load(Path(REPO_ROOT / "examples/teacup-agent-bridge"))
    result = external_cli.run_external(spec, "task", budget=0.1, live=False)

    assert result.stopped_early is True
    assert result.stop_kind == expect_kind, result.stop_reason
