# Teacup Run

**Build on agents, not from scratch.**

Teacup Run is an open-source library and registry for discovering, running, extending, evaluating, and publishing AI agents.

The idea is simple:

```python
agent = AutoAgent.from_pretrained("alice/deep-research")

agent.add_skill("pdf-analysis")
agent.set_budget(5)

result = agent.run("Research this company")

agent.push_to_hub("ray/deep-research-plus")
```

AI models became dramatically easier to build on when developers could load a pretrained model, adapt it, evaluate it, and publish the result for others to reuse.

We believe agents need the same abstraction.

## Try it

```bash
pip install -e ".[openai]"       # or ".[anthropic]", ".[google]"
export OPENAI_API_KEY=...        # or put it in .env and call teacup_run.load_env()
```

```python
from teacup_run import AutoAgent

agent = AutoAgent.from_pretrained("examples/note-taker")
agent.add_skill("concise-style")
agent.set_budget(0.25)

result = agent.run("""Notes: Ray ships the pricing page Friday.
Mei sends revised discount numbers Wednesday. Nobody owns the migration doc.""")

print(result.answer)
print(result.render_ledger(agent.budget))
```

```text
Action items
- Ray: ship the pricing page (by Friday)
- Mei: send revised discount-tier numbers (Wednesday)
- unassigned: write migration doc for legacy importer

Open questions
- Who will own the migration doc for the legacy importer?

Task completed

Model calls          $0.00
Tool calls           $0.02
Compute              $0.01
──────────────────────────
Total                $0.04

Budget               $0.20
Remaining            $0.16
```

[`examples/note-taker`](examples/note-taker) is a complete package in the format
described below: a manifest, prompts, a skill, `@tool` functions, and `@check`
predicates that decide when the work is actually done.

### What v0.1 does not do

Named, so nobody has to discover it: no MCP servers, no sub-agents or handoffs,
no memory, no streaming, no async, no registry server, and — beyond one
sandboxed-subprocess backend for `framework: teacup-agent-cli` — no general
adapter system for other frameworks; a Teacup Run package otherwise runs on
Teacup Run's own loop. See [`docs/backends.md`](docs/backends.md).

## Why Teacup Run?

The agent ecosystem is growing quickly, but reuse is still surprisingly difficult.

Today, useful agents are scattered across repositories, frameworks, runtimes, demos, and proprietary platforms. An agent built with one framework is often difficult to import into another project. Extending somebody else's agent frequently means reading their entire codebase and rebuilding substantial parts of it.

There is no widely adopted equivalent of:

```python
AutoModel.from_pretrained(...)
```

for agents.

Teacup Run aims to change that.

An agent should be a **portable, composable, versioned, evaluable software artifact**.

You should be able to:

1. Find an agent that already does most of what you need.
2. Load it with one line of code.
3. Run it locally.
4. Add a skill, tool, model, memory system, or instruction.
5. Fork it into your own agent.
6. Evaluate whether your version is actually better.
7. Publish it for somebody else to build upon.

## The Core Workflow

### 1. Pull

```python
agent = AutoAgent.from_pretrained("alice/deep-research")
```

### 2. Extend

Agents should be composable.

```python
agent.add_skill("financial-analysis")
agent.add_skill("pdf-analysis")
agent.add_tool(my_mcp_server)
```

Or change its underlying components:

```python
agent.set_model("provider/model")
agent.set_memory(my_memory)
agent.extend_instructions(
    "Always verify material claims using primary sources."
)
```

### 3. Run

Executing an agent must not require writing Python.

```bash
teacup run <ref> "<task>"
```

`<ref>` is the same reference `from_pretrained` takes: a local path, a name in
the hub, or a git URL. The design is in [docs/execution.md](docs/execution.md).

From Python, the same run:

```python
result = agent.run(
    "Analyze Acme Corp and identify its three largest strategic risks."
)
```

### 4. Evaluate

```python
report = agent.eval(
    benchmark="company-research",
    budget=5
)
```

### 5. Publish

```python
agent.push_to_hub("ray/deep-research-finance")
```

Now another developer can build on your work.

```python
agent = AutoAgent.from_pretrained(
    "ray/deep-research-finance"
)
```

That creates a simple flywheel:

```text
discover
   ↓
pull
   ↓
run
   ↓
fork
   ↓
extend
   ↓
evaluate
   ↓
publish
   ↓
someone else discovers it
   ↺
```

## Agents Are More Than Prompts

A Teacup Run agent is not simply a system prompt.

An agent may contain:

```text
agent/
├── agent.yaml          # manifest: identity, model, tools, skills, budget, lineage
├── AGENT.md            # agent card: what it does, when to use it, eval numbers
├── README.md           # quickstart
├── prompts/
├── skills/<skill>/SKILL.md
├── tools.py            # or tools/ , when the agent needs more than one file
├── checks.py           # what "done" means
├── evals/              # the benchmark, so a fork can be measured against upstream
└── tests/
```

One rule makes this layout work: **the agent directory *is* the distributed
artifact.** Publishing copies the directory; pulling gets that copy back. There
is no second, narrower thing inside it that constitutes "the real package", so
there is nothing to reconcile — what you edit is what someone else receives,
prompts, skills, benchmark and all. Two kinds of thing stay behind, both by an
explicit list rather than by where they sit: secrets (`.env`) and local dev
residue (`.git`, `.venv`, caches, `node_modules`, build output).

The rule is worth stating because the obvious alternative fails quietly. Nest
the runtime files in an inner package and the published shape stops matching the
source shape; you then need glue to carry the benchmark and the agent card
across, and whatever the glue forgets is missing only for people downstream.

The cost is that the format reserves names at the agent root — `agent.yaml`,
`prompts/`, `skills/`, `tools.py`, `checks.py`, `evals/`. An agent cannot use
those for anything else. That is the price of one layout instead of two.

An agent with Python dependencies adds a `pyproject.toml` for them. It is not
what makes the directory an agent; the manifest is.

Conceptually, an agent can define:

- instructions
- model requirements
- tools
- MCP servers
- skills
- sub-agents
- memory
- state
- environment requirements
- evaluation suites
- resource policies
- dependencies

The package format should remain readable, hackable, and friendly to Git.

## Framework-Agnostic by Design

Teacup Run is not intended to become yet another agent orchestration framework.

The ecosystem already has excellent frameworks.

Our goal is to provide a portable layer **above and across them**.

A Teacup Run package should eventually be able to wrap agents built with systems such as:

```text
OpenAI Agents SDK
Google ADK
LangGraph
PydanticAI
Microsoft Agent Framework
CrewAI
custom Python agents
```

Teacup Run should not require developers to rewrite working agents simply to participate in the ecosystem.

Existing frameworks should become backends, not competitors.

## Skills as Composable Capabilities

Agents should be extensible without being rewritten.

A **tool** gives an agent an action it can perform.

A **skill** gives an agent a reusable capability for accomplishing a class of tasks.

For example:

```python
agent = AutoAgent.from_pretrained("community/general-assistant")

agent.add_skill("legal-research")
agent.add_skill("citation-verification")
```

A skill may package instructions, examples, tools, workflows, policies, sub-agents, and evaluations.

Skills should themselves be publishable and reusable.

```python
skill = Skill.from_pretrained("community/legal-research")

agent.add_skill(skill)
```

This makes agents closer to composable software than static applications.

## Budget Is a First-Class Primitive

Agents consume resources.

A useful agent should therefore be evaluated not only by whether it succeeds, but by **how efficiently it succeeds**.

Teacup Run treats budget as part of execution:

```python
result = agent.run(
    task,
    budget=USD(5)
)
```

A budget is a real resource constraint, not merely metadata.

The runtime should account for resources such as:

```text
LLM input tokens
LLM output tokens
cached tokens
tool/API calls
sandbox compute
external services
wall-clock time
```

Every run should produce a cost ledger:

```text
Task completed

Model calls       $1.82
Tool calls        $0.21
Compute           $0.08
──────────────────────
Total             $2.11

Budget            $5.00
Remaining         $2.89
```

Eventually, agents should be able to adapt their behavior to the resources available to them.

A $0.50 run and a $50 run of the same agent may use very different strategies.

## Quality Under Constraints

Raw success rate is not enough.

Suppose three agents solve the same task:

| Agent | Success Rate | Quality | Average Cost |
|---|---:|---:|---:|
| Agent A | 94% | 0.95 | $18.40 |
| Agent B | 91% | 0.92 | $2.80 |
| Agent C | 96% | 0.97 | $73.00 |

Which one is best?

It depends on the budget.

Teacup Run aims to make **quality under resource constraints** a standard way to evaluate agents.

Instead of asking only:

> Which agent has the highest score?

we should be able to ask:

> Which agent produces the best result with $1?

or:

> Which agent maximizes task quality under a $20 budget and a ten-minute deadline?

Conceptually:

```text
maximize    expected task quality

subject to  cost <= budget
            latency <= deadline
```

This produces an **agent efficiency frontier**.

## Budgeted Benchmarks

Imagine a Deep Research leaderboard:

### $1 Budget

| Agent | Success | Quality | Avg. Cost |
|---|---:|---:|---:|
| alice/research-fast | 84% | .89 | $0.81 |
| ray/research-lite | 82% | .91 | $0.93 |
| community/research-v3 | 79% | .87 | $0.62 |

### $10 Budget

The ranking may be completely different.

This creates an important incentive for open-source agent development.

A developer can fork an agent and demonstrate:

```text
Upstream

Success:       81%
Average cost:  $3.84

My fork

Success:       89%
Average cost:  $2.31
```

That is much more meaningful than saying:

> "I changed the prompt and I think it works better."

## Agent Lineage

Forking should preserve provenance.

```text
alice/deep-research
        │
        ├── ray/deep-research-finance
        │          │
        │          └── bob/investment-research
        │
        └── jane/deep-research-fast
```

The Hub should understand these relationships.

Developers should be able to see:

- what an agent was derived from
- what changed
- which skills were added
- benchmark differences
- cost differences
- model differences
- downstream forks

Agent development can then become cumulative rather than repetitive.

## What Teacup Run Is Not

Teacup Run is **not** initially trying to be:

- an enterprise agent control plane
- an RBAC product
- an SSO platform
- an enterprise governance suite
- another proprietary agent runtime
- another closed agent marketplace
- another orchestration DSL

Those may be useful problems.

They are not the first problem we want to solve.

The first problem is much simpler:

> **Make agents as easy to reuse, modify, evaluate, and share as pretrained models.**

## Open Source First

Teacup Run is intended to be built in the open.

The initial users are developers, researchers, hackers, and agent builders—not procurement departments.

Early success should be measured by:

- agents published
- agents downloaded
- agents forked
- skills reused
- evaluation runs
- downstream derivatives
- improvements over upstream agents
- active developers

One metric matters especially:

> **How often does someone take another person's agent, improve it, and publish the improvement?**

If that happens routinely, agents have become a reusable open-source artifact.

## The Vision

The model ecosystem became extraordinarily productive once developers stopped training everything from scratch.

We believe the same transition will happen with agents.

Today, thousands of developers independently build research agents, coding agents, browser agents, support agents, financial agents, legal agents, and personal assistants.

Much of that work is repeated.

Instead, we want this:

```python
agent = AutoAgent.from_pretrained("best-agent-I-can-find")

agent.add_skill("something-I-need")

agent.eval(budget=5)

agent.push_to_hub("my-better-agent")
```

Then someone else starts from there.

**Don't build every agent from scratch. Build on each other.**

## Examples

Agent packages live in [`examples/`](examples/).

| Example | Framework | What it shows |
|---|---|---|
| [`note-taker`](examples/note-taker/) | Teacup Run | The reference implementation of the package format: a manifest, prompts, a composable skill, `@tool` functions, `@check` predicates that decide when the work is done, and a budgeted eval suite. |

[`examples/note-taker`](examples/note-taker/) is the package the quickstart above
loads. Its layout is the format:

```text
agent.yaml                            # manifest
AGENT.md                              # agent card
README.md                             # quickstart
prompts/system.md                     # instructions
skills/concise-style/SKILL.md         # an optional capability
tools.py                              # @tool functions
checks.py                             # @check predicates
evals/benchmark.yaml                  # the benchmark
```

Eight files, one level. `agent.push_to_hub("ray/note-taker")` reproduces exactly
that directory in the hub — the library's test suite asserts the two shapes are
equal — so a pulled agent arrives with its card and its benchmark, and
`from_pretrained` → `eval` works for whoever forks it next.

The library's own test suite exercises this agent with a simulated model, so the
whole thing is developable without an API key and without spending anything.

A second example on a different framework is the obvious next one to write —
demonstrating that the package format sits above frameworks rather than
replacing them is the point of the format, and one example cannot show it.

## Status

Teacup Run is an early-stage idea and experimental open-source project.

APIs, package formats, and terminology are expected to evolve.

We are particularly interested in contributors who care about:

- agent interoperability
- portable agent packaging
- skills and composition
- MCP
- reproducible agent execution
- agent evaluation
- cost accounting
- budget-aware reasoning
- benchmarking
- open agent ecosystems

## Contributing

If the idea resonates with you, open an issue, propose a package format, publish an experimental agent, build an adapter, challenge an abstraction, or submit a pull request.

The standard should emerge from builders using it.

---

**Pull. Fork. Improve. Evaluate. Publish.**