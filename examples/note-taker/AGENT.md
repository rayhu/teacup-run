# teacup/note-taker

> Agent card. `agent.yaml` is the machine-readable manifest; this is the page a
> human reads before deciding to pull the agent.

**Version** 0.1.0 · **Runtime** Teacup Run · **Lineage** root agent (no upstream)

## What it does

Reads raw meeting notes and produces action items that name an owner, so the
list is actionable rather than a summary of the conversation.

## When to use it

Good fit:

- Turning messy notes into a list someone can be held to.
- A worked example of the Teacup Run package format: tools that write to shared
  state, goal checks that read it, and a budget that stops the run.

Poor fit:

- Transcription, summarising for its own sake, or anything needing context from
  outside the notes you paste in.

## Interface

| | |
|---|---|
| Input | Raw meeting notes as text |
| Output | An `Action items` list, plus `Open questions` for unowned decisions |
| Tools | `save_action_item(what, owner, when)`, `list_action_items()` |
| Model | `gpt-5-mini` (fallback `gpt-5`) |
| Requires | `OPENAI_API_KEY` |
| Budget | $0.25, ≤20 tool calls, ≤120s |

## Goal

> Every action item is recorded with an owner, and the answer lists them.

| Check | Fails when |
|---|---|
| `non_empty` | The answer is blank |
| `no_question_back` | It ends by asking the user what to do |
| `has_action_items` | Nothing was recorded |
| `every_item_has_owner` | An item was saved with no owner |
| `items_appear_in_answer` | Something was recorded but left out of the answer |

Up to 3 attempts. A failed check's message becomes the next attempt's input, and
the run keeps the **best** attempt, not the last.

## Skills

`concise-style` — one scannable line per item. Off by default; enable with
`agent.add_skill("concise-style")`.
