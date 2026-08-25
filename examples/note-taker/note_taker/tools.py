"""Tools for the note-taker.

Plain functions. A leading `artifacts` parameter is injected by the run — the
model never sees it — which is how the goal checks in checks.py get to look at
what the tools actually recorded.
"""

from __future__ import annotations

from typing import Any

from opengraft import tool


@tool
def save_action_item(artifacts: dict, what: str, owner: str, when: str = "") -> dict:
    """Record one action item found in the notes.

    Args:
        what: The thing that must be done, in one short line.
        owner: Who owns it. Use "unassigned" only when nobody can be identified.
        when: The deadline exactly as the notes state it, e.g. "next Friday".
    """
    items: list[dict[str, Any]] = artifacts.setdefault("action_items", [])
    item = {"what": what.strip(), "owner": owner.strip(), "when": when.strip()}
    if not item["what"]:
        return {"saved": False, "error": "an action item needs a description"}
    items.append(item)
    return {"saved": True, "count": len(items), "item": item}


@tool
def list_action_items(artifacts: dict) -> dict:
    """Return every action item recorded so far in this run."""
    items = artifacts.get("action_items", [])
    return {"count": len(items), "items": items}
