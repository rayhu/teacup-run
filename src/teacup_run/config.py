"""`~/.config/teacup/config.yaml` — settings, and deliberately never secrets.

Two chains meet in the CLI and must not be merged. This module orders **settings**:
CLI flags, then `TEACUP_*` environment variables, then this file, then the agent's
manifest, then built-in defaults. `env.py` orders **credentials**, and a config file
that could set `OPENAI_API_KEY` directly would undo that separation — which is why the
schema has an `env_file` *pointer* and no place to put a value.

That is also what keeps this file safe to commit. A file like this ends up in a
dotfiles repository whether or not anyone intended it to, so it holds where the secrets
live and not the secrets.

Absent is a valid state. Every key has a default and the CLI must work with no config
file at all — the first run of a freshly installed tool is exactly the run that has
none.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

__all__ = ["Config", "load_config", "config_path"]

ENV_VAR = "TEACUP_CONFIG"
DEFAULT_PATH = Path("~/.config/teacup/config.yaml")
DEFAULT_HUB = Path("~/.teacup/agents")
DEFAULT_BUDGET_USD = 1.00


@dataclass(frozen=True)
class Config:
    """Resolved settings. Field names match the YAML keys they come from."""

    env_file: Path | None = None
    budget_usd: float | None = DEFAULT_BUDGET_USD
    model: str | None = None  # None: whatever the manifest asks for
    hub_path: Path = field(default_factory=lambda: DEFAULT_HUB.expanduser())
    auto_pull: bool = False
    ledger: bool = True
    json: bool = False
    source: Path | None = None  # which file this came from, for the preflight echo


def config_path(explicit: str | Path | None = None) -> Path:
    """Where the config would be read from, whether or not it exists.

    Precedence is the settings chain's own: an explicit `--config` wins, then
    `TEACUP_CONFIG`, then the XDG location. Returned even when absent so a caller can
    say *which* file it looked for, which is the difference between "no config" and
    "your config is somewhere else than you think".
    """
    if explicit is not None:
        return Path(explicit).expanduser()
    if os.environ.get(ENV_VAR):
        return Path(os.environ[ENV_VAR]).expanduser()
    return DEFAULT_PATH.expanduser()


def load_config(explicit: str | Path | None = None) -> Config:
    """Read the config file, or return defaults if there is none."""
    path = config_path(explicit)
    if not path.is_file():
        return Config()

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a YAML mapping, got {type(raw).__name__}")

    defaults = _mapping(raw, "defaults")
    hub = _mapping(raw, "hub")
    output = _mapping(raw, "output")

    env_file = raw.get("env_file")
    budget = defaults.get("budget_usd", DEFAULT_BUDGET_USD)

    # TEACUP_HOME already existed before this file did and is documented as winning
    # over hub.path — an environment variable beats a config file in the settings
    # chain, and reversing that for one key would make the chain unpredictable.
    hub_path = os.environ.get("TEACUP_HOME") or hub.get("path") or DEFAULT_HUB
    return Config(
        env_file=Path(env_file).expanduser() if env_file else None,
        budget_usd=None if budget is None else float(budget),
        model=defaults.get("model") or None,
        hub_path=Path(hub_path).expanduser(),
        auto_pull=bool(hub.get("auto_pull", False)),
        ledger=bool(output.get("ledger", True)),
        json=bool(output.get("json", False)),
        source=path,
    )


def _mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, dict):
        raise ValueError(f"config key {key!r} must be a mapping, got {type(value).__name__}")
    return value
