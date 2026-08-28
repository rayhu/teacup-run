"""Shared fixtures. Every test here runs offline: the model is faked at the one
seam the library has for it, `call_model`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from teacup_run.model import Reply, ToolCall, Usage

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def note_taker_path() -> Path:
    return EXAMPLES / "note-taker"


class FakeModel:
    """A scripted model. Each queued reply is returned in turn.

    Records every prompt it was given, which is how the goal-loop tests assert
    that feedback — not a bare repeat of the task — drives the second attempt.
    """

    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def __call__(self, model: str, messages: list[dict], tools=()) -> Reply:
        self.calls.append(list(messages))
        if not self.replies:
            return text_reply("(the fake model ran out of scripted replies)")
        reply = self.replies.pop(0)
        return reply

    @property
    def prompts(self) -> list[str]:
        """The user prompt of each call — one per attempt, plus tool turns."""
        return [m[-1].get("content") or "" for m in self.calls]


def text_reply(text: str, *, input_tokens: int = 100, output_tokens: int = 20) -> Reply:
    return Reply(text=text, usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens))


def tool_reply(name: str, arguments: dict, *, call_id: str = "call-1") -> Reply:
    return Reply(
        tool_calls=(ToolCall(id=call_id, name=name, arguments=arguments),),
        usage=Usage(input_tokens=100, output_tokens=20),
    )


@pytest.fixture
def fake_model():
    return FakeModel


@pytest.fixture
def git_hub(tmp_path: Path) -> Path:
    """An empty hub directory that is a git repository."""
    hub = tmp_path / "hub" / "agents"
    hub.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=hub, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=hub, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=hub, check=True)
    return hub


@pytest.fixture
def agent_copy(tmp_path: Path, note_taker_path: Path) -> Path:
    """A writable copy of the example agent, for tests that publish it."""
    target = tmp_path / "note-taker"
    shutil.copytree(note_taker_path, target)
    return target
