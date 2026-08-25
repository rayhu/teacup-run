# note-taker

The OpenGraft example agent: raw meeting notes in, owned action items out.

```python
from opengraft import AutoAgent

agent = AutoAgent.from_pretrained("examples/note-taker")
agent.add_skill("concise-style")
agent.set_budget(0.25)

result = agent.run("Notes: Ray ships the pricing page Friday. Mei sends numbers Wednesday.")
print(result.answer)
print(result.render_ledger())
```

Needs `OPENAI_API_KEY` for a real run. The library's own test suite exercises this
agent with a faked model, so nothing here requires a key to develop against.

## Layout

```
agent.yaml -> note_taker/agent.yaml   # symlink: manifest visible at the agent root
AGENT.md                              # agent card
note_taker/                           # the shipped package
├── agent.yaml                        # manifest
├── prompts/system.md                 # instructions
├── skills/concise-style/SKILL.md     # an optional capability
├── tools.py                          # @tool functions
└── checks.py                         # @check predicates — what "done" means
evals/benchmark.yaml                  # development scaffolding, need not ship
```

## Evaluate it

```python
report = agent.eval(budget=1.00, compare="goal_loop")
print(report)
```

`compare="goal_loop"` runs every task twice — once with the goal checked and
retried, once with the loop off — and reports quality **and** cost for both,
because "is it better?" is not answerable without "better per dollar?".
