"""What "done" means for this agent.

Deterministic predicates over the run's own output. Each returns "" when it
passes, or a sentence describing the fix — phrased as an instruction, because a
failure is fed back to the model as the next attempt's input.
"""

from __future__ import annotations

from teacup_run import Attempt, check


@check
def has_action_items(attempt: Attempt) -> str:
    """At least one action item was recorded."""
    if attempt.artifacts.get("action_items"):
        return ""
    return (
        "You recorded no action items, so nothing is tracked. Call save_action_item "
        "once for each commitment in the notes. If the notes truly contain none, say "
        "that in one line under 'Open questions'."
    )


@check
def every_item_has_owner(attempt: Attempt) -> str:
    """No item was recorded without a named owner."""
    ownerless = [
        item["what"]
        for item in attempt.artifacts.get("action_items", [])
        if not item.get("owner")
    ]
    if not ownerless:
        return ""
    listed = "; ".join(ownerless[:3])
    return (
        f"These items have no owner: {listed}. Re-save each one with the person named "
        "in the notes, or with 'unassigned' if nobody can be identified."
    )


@check
def items_appear_in_answer(attempt: Attempt) -> str:
    """The answer actually lists what was recorded."""
    items = attempt.artifacts.get("action_items", [])
    if not items:
        return ""
    missing = [item["what"] for item in items if item["what"].lower() not in attempt.answer.lower()]
    if not missing:
        return ""
    listed = "; ".join(missing[:3])
    return (
        f"You recorded these items but left them out of the answer: {listed}. "
        "The answer must list every item you saved."
    )
