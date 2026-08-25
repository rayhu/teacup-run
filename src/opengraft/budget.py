"""Budget as a constraint, not a receipt.

The ceiling is checked *before* each model call and each tool call. Checking
after the fact produces an accurate invoice for a run you did not want to pay
for; checking before is what makes `budget=5` mean something.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

__all__ = [
    "Budget",
    "BudgetExceeded",
    "Ledger",
    "ModelPrice",
    "TOOL_CALL_USD",
    "price_for",
]


class BudgetExceeded(RuntimeError):
    """Raised before an action that would exceed the run's ceiling."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens."""

    input: float
    cached_input: float
    output: float

    def cost(self, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0) -> float:
        fresh = max(0, input_tokens - cached_input_tokens)
        return (
            fresh * self.input
            + cached_input_tokens * self.cached_input
            + output_tokens * self.output
        ) / 1_000_000


# Illustrative prices so a sample run produces plausible numbers. Check the
# provider's pricing page before trusting any figure a run prints.
PRICES: dict[str, ModelPrice] = {
    "gpt-5": ModelPrice(1.25, 0.125, 10.00),
    "gpt-5-mini": ModelPrice(0.25, 0.025, 2.00),
    "claude-opus-5": ModelPrice(5.00, 0.50, 25.00),
    "claude-sonnet-5": ModelPrice(3.00, 0.30, 15.00),
    "claude-haiku-4-5": ModelPrice(1.00, 0.10, 5.00),
    "gemini-flash-latest": ModelPrice(0.30, 0.03, 2.50),
}
DEFAULT_PRICE = ModelPrice(1.00, 0.10, 5.00)

TOOL_CALL_USD = 0.005
COMPUTE_USD_PER_SECOND = 0.0004


def price_for(model: str) -> ModelPrice:
    """Longest-prefix match, so `gpt-5-mini-2026-01-01` prices as `gpt-5-mini`."""
    best: tuple[int, ModelPrice] = (0, DEFAULT_PRICE)
    for name, price in PRICES.items():
        if model.startswith(name) and len(name) > best[0]:
            best = (len(name), price)
    return best[1]


@dataclass
class ModelCallRecord:
    model: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float


@dataclass
class ToolCallRecord:
    name: str
    cost_usd: float


@dataclass
class Ledger:
    """What a run actually spent."""

    model_calls: list[ModelCallRecord] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    stopped_at: float | None = None

    def record_model_call(
        self, model: str, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0
    ) -> ModelCallRecord:
        cost = price_for(model).cost(input_tokens, output_tokens, cached_input_tokens)
        record = ModelCallRecord(model, input_tokens, output_tokens, cached_input_tokens, cost)
        self.model_calls.append(record)
        return record

    def record_tool_call(self, name: str, cost_usd: float = TOOL_CALL_USD) -> ToolCallRecord:
        record = ToolCallRecord(name, cost_usd)
        self.tool_calls.append(record)
        return record

    def stop_clock(self) -> None:
        self.stopped_at = time.monotonic()

    @property
    def elapsed_s(self) -> float:
        return (self.stopped_at or time.monotonic()) - self.started_at

    @property
    def model_cost(self) -> float:
        return sum(c.cost_usd for c in self.model_calls)

    @property
    def tool_cost(self) -> float:
        return sum(c.cost_usd for c in self.tool_calls)

    @property
    def compute_cost(self) -> float:
        return self.elapsed_s * COMPUTE_USD_PER_SECOND

    @property
    def total_cost(self) -> float:
        return self.model_cost + self.tool_cost + self.compute_cost

    @property
    def input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.model_calls)

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.model_calls)

    @property
    def cached_input_tokens(self) -> int:
        return sum(c.cached_input_tokens for c in self.model_calls)

    def render(self, budget: "Budget | None" = None, header: str = "Task completed") -> str:
        def line(label: str, amount: float) -> str:
            return f"{label:<18}{'$' + format(amount, ',.2f'):>8}"

        out = [header, ""]
        out.append(line("Model calls", self.model_cost))
        out.append(line("Tool calls", self.tool_cost))
        out.append(line("Compute", self.compute_cost))
        out.append("─" * 26)
        out.append(line("Total", self.total_cost))
        if budget is not None and budget.usd is not None:
            out.append("")
            out.append(line("Budget", budget.usd))
            out.append(line("Remaining", max(0.0, budget.usd - self.total_cost)))
        return "\n".join(out)

    def render_usage(self) -> str:
        return (
            f"{len(self.model_calls)} model call(s), {len(self.tool_calls)} tool call(s), "
            f"{self.input_tokens:,} in / {self.cached_input_tokens:,} cached / "
            f"{self.output_tokens:,} out tokens, {self.elapsed_s:.1f}s"
        )


@dataclass(frozen=True)
class Budget:
    """A ceiling. Any of the three limits stops the run when reached."""

    usd: float | None = None
    max_tool_calls: int | None = None
    deadline_s: float | None = None

    def check(self, ledger: Ledger) -> None:
        """Raise if the next action would exceed the ceiling."""
        if self.usd is not None and ledger.total_cost >= self.usd:
            raise BudgetExceeded(f"spent ${ledger.total_cost:,.2f} of ${self.usd:,.2f} budget")
        if self.max_tool_calls is not None and len(ledger.tool_calls) >= self.max_tool_calls:
            raise BudgetExceeded(f"reached the {self.max_tool_calls}-tool-call limit")
        if self.deadline_s is not None and ledger.elapsed_s >= self.deadline_s:
            raise BudgetExceeded(f"reached the {self.deadline_s:.0f}s deadline")

    @classmethod
    def of(cls, value: "Budget | float | int | None") -> "Budget":
        """`budget=5` means five dollars; a Budget passes through."""
        if isinstance(value, Budget):
            return value
        if value is None:
            return cls()
        return cls(usd=float(value))
