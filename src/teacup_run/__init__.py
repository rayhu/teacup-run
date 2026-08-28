"""Teacup Run — build on agents, not from scratch.

    from teacup_run import AutoAgent

    agent = AutoAgent.from_pretrained("examples/note-taker")
    agent.add_skill("citation-style")
    agent.set_budget(5)
    result = agent.run("Summarise the meeting notes")
    print(result.answer)
    print(result.render_ledger())
"""

from .auto import AutoAgent
from .budget import Budget, BudgetExceeded, Ledger
from .env import load_env
from .goal import Attempt, GoalVerdict, check
from .loop import Result, run
from .manifest import AgentSpec, ManifestError
from .model import Reply, ToolCall, Usage, call_model
from .registry import RegistryError
from .tools import Tool, tool

__version__ = "0.1.0"

__all__ = [
    "AgentSpec",
    "Attempt",
    "AutoAgent",
    "Budget",
    "BudgetExceeded",
    "GoalVerdict",
    "Ledger",
    "ManifestError",
    "RegistryError",
    "Reply",
    "Result",
    "Tool",
    "ToolCall",
    "Usage",
    "__version__",
    "call_model",
    "check",
    "load_env",
    "run",
    "tool",
]
