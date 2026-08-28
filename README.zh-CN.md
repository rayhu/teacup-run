# OpenGraft

**不要从零开始造 Agent。在别人的 Agent 之上继续构建。**

OpenGraft 是一个开源的 Agent library 与 registry，用于发现、加载、运行、扩展、评测和发布 AI Agent。

核心想法非常简单：

```python
agent = AutoAgent.from_pretrained("alice/deep-research")

agent.add_skill("pdf-analysis")
agent.set_budget(5)

result = agent.run("研究这家公司")

agent.push_to_hub("ray/deep-research-plus")
```

预训练模型之所以极大提高了 AI 开发效率，一个重要原因是开发者可以直接加载别人训练好的模型，在其基础上修改、微调、评测，然后再次发布，供其他人继续使用。

我们认为 Agent 也需要同样的抽象。

## 为什么需要 OpenGraft？

Agent 生态正在快速发展，但 Agent 的复用仍然非常困难。

今天，有价值的 Agent 分散在不同的 GitHub repository、framework、runtime、demo 和封闭平台中。一个 framework 写出的 Agent，往往很难直接拿到另一个项目中使用。想修改别人的 Agent，经常意味着必须先读完整个代码库，然后重新实现大量已有功能。

Agent 世界目前仍然缺少一个被广泛采用的：

```python
AutoModel.from_pretrained(...)
```

OpenGraft 希望解决这个问题。

一个 Agent 应当成为一种：

**可移植、可组合、可版本化、可评测的软件制品。**

开发者应该能够：

1. 找到一个已经完成大部分需求的 Agent；
2. 一行代码加载它；
3. 在本地直接运行；
4. 给它增加 Skill、Tool、Model、Memory 或 Instruction；
5. Fork 成自己的 Agent；
6. 客观评测自己的版本是否真的更好；
7. 再次发布，让其他开发者继续构建。

## 核心工作流

### 1. Pull

```python
agent = AutoAgent.from_pretrained("alice/deep-research")
```

### 2. Extend

Agent 应当天然支持组合和扩展：

```python
agent.add_skill("financial-analysis")
agent.add_skill("pdf-analysis")
agent.add_tool(my_mcp_server)
```

也可以修改底层组件：

```python
agent.set_model("provider/model")
agent.set_memory(my_memory)
agent.extend_instructions(
    "所有重要结论必须使用一手资料进行验证。"
)
```

### 3. Run

执行一个 agent 不应该需要写 Python。

```bash
opengraft run <ref> "<task>"
```

`<ref>` 就是 `from_pretrained` 接受的那种引用：本地路径、hub 中的名字，或一个
git URL。具体设计见 [docs/execution.zh-CN.md](docs/execution.zh-CN.md)。

同一次运行，从 Python 调用：

```python
result = agent.run(
    "分析 Acme Corp，并找出其三个最大的战略风险。"
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

此后，其他开发者可以直接在你的工作成果上继续开发：

```python
agent = AutoAgent.from_pretrained(
    "ray/deep-research-finance"
)
```

由此形成一个简单的飞轮：

```text
发现
 ↓
加载
 ↓
运行
 ↓
Fork
 ↓
扩展
 ↓
评测
 ↓
发布
 ↓
被其他人再次发现
 ↺
```

## Agent 不只是 Prompt

OpenGraft 中的 Agent 不是简单的一段 system prompt。

一个 Agent package 可以包含：

```text
agent/
├── agent.yaml          # manifest：身份、模型、tools、skills、budget、lineage
├── AGENT.md            # agent card：做什么、何时使用、评测数据
├── README.md           # 快速上手
├── prompts/
├── skills/<skill>/SKILL.md
├── tools.py            # 一个文件不够时也可以是 tools/
├── checks.py           # 「什么算做完」
├── evals/              # benchmark —— fork 才有办法和上游比
└── tests/
```

有一条规则让这个布局真正成立：**agent 目录本身就是被分发的产物。**发布即拷贝这个
目录，pull 拿回来的就是这份拷贝。它内部不存在另一个更窄的、才算「真正的 package」
的东西，因此没有任何需要对齐的地方 —— 你编辑的就是别人收到的，prompts、skills、
benchmark 一并在内。只有两类东西留在原地，且是靠一份显式清单而不是靠它们所在的位置
排除的：密钥（`.env`），以及本地开发残留（`.git`、`.venv`、各种缓存、`node_modules`、
构建产物）。

这条规则值得写出来，是因为那个看起来更自然的替代方案会静默地失败。把运行时文件嵌套
进一个内层 package，发布后的形状就不再等于源码的形状；于是你需要一段胶水代码把
benchmark 和 agent card 搬过去，而胶水漏掉的东西，只有下游的人才会发现缺失。

代价是：格式在 agent 根目录保留了一批名字 —— `agent.yaml`、`prompts/`、`skills/`、
`tools.py`、`checks.py`、`evals/`。agent 不能拿它们作他用。这就是「只有一套布局而不是
两套」的价钱。

有 Python 依赖的 agent 再加一个 `pyproject.toml` 来声明。让这个目录成为 agent 的不是
它，而是 manifest。

从概念上，一个 Agent 可以定义：

- Instructions
- Model requirements
- Tools
- MCP servers
- Skills
- Sub-agents
- Memory
- State
- Environment requirements
- Evaluation suites
- Resource policies
- Dependencies

Package format 应当保持简单、可读、可修改，并且天然适合 Git。

## Framework-Agnostic

OpenGraft **不希望成为又一个 Agent orchestration framework**。

这个领域已经存在很多优秀的 framework。

我们的目标，是建立一个位于它们之上、横跨不同 framework 的可移植层。

未来，一个 OpenGraft package 应当能够包装来自以下系统的 Agent：

```text
OpenAI Agents SDK
Google ADK
LangGraph
PydanticAI
Microsoft Agent Framework
CrewAI
自定义 Python Agent
```

开发者不应该为了加入 OpenGraft 生态，就被迫重写一个已经正常工作的 Agent。

现有 framework 应该成为 OpenGraft 的 **backend，而不是 competitor**。

## Skill：可组合的能力

Agent 应该能够在不重写的情况下获得新能力。

**Tool** 告诉 Agent 它能够执行什么动作。

**Skill** 则赋予 Agent 完成某一类任务的可复用能力。

例如：

```python
agent = AutoAgent.from_pretrained("community/general-assistant")

agent.add_skill("legal-research")
agent.add_skill("citation-verification")
```

一个 Skill 可以包含：

- instructions
- examples
- tools
- workflows
- policies
- sub-agents
- evals

Skill 本身也应该能够发布和复用：

```python
skill = Skill.from_pretrained("community/legal-research")

agent.add_skill(skill)
```

这样，Agent 就不再只是一个静态应用，而更接近一种真正可以组合的软件。

## Budget 是一等公民

Agent 会消耗资源。

因此，一个 Agent 是否优秀，不能只看它能不能完成任务，还应该看：

> **它以多高的效率完成了任务。**

OpenGraft 把 Budget 直接作为执行过程的一部分：

```python
result = agent.run(
    task,
    budget=USD(5)
)
```

Budget 不是简单的 metadata，而应该是真正的资源约束。

Runtime 应该统一记录：

```text
LLM input tokens
LLM output tokens
cached tokens
Tool/API calls
sandbox compute
external services
wall-clock time
```

每次执行都应该产生一份完整的成本账本：

```text
任务完成

模型调用          $1.82
Tool 调用         $0.21
Compute           $0.08
──────────────────────
总成本            $2.11

预算              $5.00
剩余              $2.89
```

最终，Agent 应该能够根据可用资源主动调整自己的策略。

同一个 Agent 在 **$0.50** 和 **$50** 的预算下，可以采用完全不同的执行方式。

## 约束条件下的质量

单纯比较 success rate 是不够的。

假设三个 Agent 执行完全相同的任务：

| Agent | 成功率 | 质量 | 平均成本 |
|---|---:|---:|---:|
| Agent A | 94% | 0.95 | $18.40 |
| Agent B | 91% | 0.92 | $2.80 |
| Agent C | 96% | 0.97 | $73.00 |

哪一个最好？

答案取决于你的预算。

OpenGraft 希望把 **Quality Under Constraints（约束条件下的质量）** 变成 Agent 的标准评测方式。

我们不应该只问：

> 哪个 Agent 得分最高？

而应该能够问：

> 在 $1 的预算下，哪个 Agent 能得到最好的结果？

或者：

> 在 $20 预算和十分钟 deadline 的约束下，哪个 Agent 能最大化任务质量？

从概念上说：

```text
最大化：   预期任务质量

约束：     成本 <= Budget
           延迟 <= Deadline
```

这样就可以得到一条 **Agent Efficiency Frontier（Agent 效率前沿）**。

## Budgeted Benchmark

例如，一个 Deep Research 排行榜：

### $1 Budget

| Agent | 成功率 | 质量 | 平均成本 |
|---|---:|---:|---:|
| alice/research-fast | 84% | .89 | $0.81 |
| ray/research-lite | 82% | .91 | $0.93 |
| community/research-v3 | 79% | .87 | $0.62 |

到了：

### $10 Budget

排行榜可能完全不同。

这会给开源 Agent 开发带来一种很重要的激励机制。

开发者可以 Fork 一个已有 Agent，然后证明：

```text
Upstream

成功率：       81%
平均成本：     $3.84

我的 Fork

成功率：       89%
平均成本：     $2.31
```

这比：

> “我改了一下 prompt，感觉效果更好。”

有意义得多。

## Agent Lineage

Fork 应该保留完整的来源关系：

```text
alice/deep-research
        │
        ├── ray/deep-research-finance
        │          │
        │          └── bob/investment-research
        │
        └── jane/deep-research-fast
```

Hub 应该理解这些关系。

开发者应该能够看到：

- Agent 来源于哪个 Agent；
- 修改了什么；
- 增加了哪些 Skills；
- Benchmark 有什么变化；
- 成本有什么变化；
- Model 有什么变化；
- 后来又产生了哪些 downstream forks。

这样 Agent 的开发才能真正变成**累积式创新，而不是不断重复造轮子**。

## OpenGraft 暂时不做什么

OpenGraft 初期**不打算**成为：

- Enterprise Agent Control Plane
- RBAC 产品
- SSO 平台
- 企业 Governance Suite
- 又一个专有 Agent Runtime
- 又一个封闭 Agent Marketplace
- 又一个 Orchestration DSL

这些可能都是有价值的问题。

但它们不是我们首先想解决的问题。

我们首先想解决的问题非常简单：

> **让 Agent 像预训练模型一样容易复用、修改、评测和分享。**

## Open Source First

OpenGraft 希望从一开始就在开放社区中构建。

第一批用户应该是：

**开发者、研究人员、Hacker 和 Agent Builder，而不是企业采购部门。**

早期成功指标应该是：

- 发布了多少 Agent；
- 下载了多少 Agent；
- 产生了多少 Fork；
- Skill 被复用了多少次；
- 执行了多少 Eval；
- 产生了多少 downstream derivative；
- 有多少 Fork 超越了 upstream；
- 有多少活跃开发者。

其中有一个指标尤其重要：

> **一个开发者拿到另一个人的 Agent，改进它，然后再次发布——这种事情发生得有多频繁？**

如果这种行为变得普遍，我们才真正创造出了一种新的、可复用的开源软件制品。

## 愿景

预训练模型生态之所以产生巨大生产力，一个重要原因是开发者终于不需要每次都从头训练模型。

我们相信 Agent 也会发生同样的转变。

今天，成千上万的开发者正在分别开发：

- Research Agent
- Coding Agent
- Browser Agent
- Customer Support Agent
- Financial Agent
- Legal Agent
- Personal Assistant

其中大量工作其实是在重复。

我们希望未来变成：

```python
agent = AutoAgent.from_pretrained("best-agent-I-can-find")

agent.add_skill("something-I-need")

agent.eval(budget=5)

agent.push_to_hub("my-better-agent")
```

然后，下一个人直接从这里继续。

**不要每次从零开始构建 Agent。让我们在彼此的成果之上继续构建。**

## 示例

Agent package 放在 [`examples/`](examples/) 目录下。

| 示例 | Framework | 展示了什么 |
|---|---|---|
| [`note-taker`](examples/note-taker/) | OpenGraft | package format 的参考实现：manifest、prompts、可组合的 skill、`@tool` 函数、决定「什么算做完」的 `@check` 断言，以及一套带预算的 eval。 |

[`examples/note-taker`](examples/note-taker/) 就是上面 quickstart 加载的那个
package。它的目录结构即是这套格式本身：

```text
agent.yaml                            # manifest
AGENT.md                              # agent card
README.md                             # 快速上手
prompts/system.md                     # instructions
skills/concise-style/SKILL.md         # 一个可选能力
tools.py                              # @tool 函数
checks.py                             # @check 断言
evals/benchmark.yaml                  # benchmark
```

八个文件，一层。`agent.push_to_hub("ray/note-taker")` 在 hub 里复现的就是这个目录
—— 本库的测试断言了两者形状相等 —— 所以 pull 下来的 agent 自带 agent card 和
benchmark，下一个 fork 它的人，`from_pretrained` → `eval` 直接就能跑。

本库自己的测试套件用一个模拟的 model 驱动这个 agent，所以整个开发过程不需要
API key，也不产生任何花费。

下一个显然该写的，是一个基于**另一个 framework** 的示例 —— package format 位于
各 framework 之上而非取代它们，这正是这套格式的意义所在，而一个示例说明不了这件事。

## 项目状态

OpenGraft 目前仍处于早期构想和实验性开源阶段。

API、package format 和术语都可能快速变化。

我们尤其欢迎对以下方向感兴趣的贡献者：

- Agent interoperability
- Portable agent packaging
- Skills 与 composition
- MCP
- Reproducible agent execution
- Agent evaluation
- Cost accounting
- Budget-aware reasoning
- Benchmarking
- Open agent ecosystem

## 参与贡献

如果你认同这个方向，可以：

- 提 Issue
- 提议新的 Agent package format
- 发布实验性的 Agent
- 开发 framework adapter
- 挑战现有 abstraction
- 提交 Pull Request

标准不应该由一个团队关起门来设计。

**标准应该从真正使用它的开发者社区中生长出来。**

---

**Pull. Fork. Improve. Evaluate. Publish.**