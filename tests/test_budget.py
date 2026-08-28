"""A budget is a constraint, not a receipt: it must stop a run, not describe one."""

from __future__ import annotations

import pytest

from teacup_run.budget import Budget, BudgetExceeded, Ledger, price_for


def test_prices_match_on_longest_prefix():
    assert price_for("gpt-5-mini-2026-01-01") == price_for("gpt-5-mini")
    assert price_for("gpt-5") != price_for("gpt-5-mini")


def test_an_unknown_model_still_prices():
    assert price_for("some-new-model").input > 0


def test_cached_input_is_cheaper_than_fresh_input():
    price = price_for("gpt-5")
    fresh = price.cost(1_000_000, 0)
    cached = price.cost(1_000_000, 0, cached_input_tokens=1_000_000)

    assert cached < fresh


def test_the_ledger_totals_what_was_spent():
    ledger = Ledger()
    ledger.record_model_call("gpt-5", 1_000_000, 100_000)
    ledger.record_tool_call("search")

    assert ledger.model_cost == pytest.approx(1.25 + 1.0)
    assert ledger.tool_cost == pytest.approx(0.005)
    assert ledger.total_cost > ledger.model_cost


def test_a_dollar_ceiling_stops_the_run():
    budget = Budget(usd=1.00)
    ledger = Ledger()
    budget.check(ledger)  # nothing spent yet

    ledger.record_model_call("gpt-5", 1_000_000, 0)  # $1.25

    with pytest.raises(BudgetExceeded, match="budget"):
        budget.check(ledger)


def test_a_tool_call_ceiling_stops_the_run():
    budget = Budget(max_tool_calls=2)
    ledger = Ledger()
    ledger.record_tool_call("a")
    budget.check(ledger)
    ledger.record_tool_call("b")

    with pytest.raises(BudgetExceeded, match="tool-call limit"):
        budget.check(ledger)


def test_a_bare_number_means_dollars():
    assert Budget.of(5).usd == 5.0
    assert Budget.of(None).usd is None
    assert Budget.of(Budget(usd=2, max_tool_calls=3)).max_tool_calls == 3


def test_the_ledger_renders_the_readme_shape():
    ledger = Ledger()
    ledger.record_model_call("gpt-5", 100_000, 10_000)
    rendered = ledger.render(budget=Budget(usd=5.00))

    assert "Task completed" in rendered
    assert "Model calls" in rendered
    assert "Total" in rendered
    assert "Remaining" in rendered
