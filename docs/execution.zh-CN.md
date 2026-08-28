# 执行一个 agent

**状态：** 提案，等待 review。以下内容尚未实现。
**范围：** `teacup run` 的设计。它所实现的那条规则 —— 执行一个 agent 不应该需要写
Python —— 写在 [README](../README.zh-CN.md) 里。
**翻译：** 本文是 [execution.md](execution.md) 的中文版。英文版为原文，两者不一致时以
英文版为准。

---

## 1. 命令

```
teacup run <ref> <task>

  --budget USD          覆盖 manifest 中的 budget
  --model NAME          覆盖 manifest 中的 model
  --skill NAME          启用一个 package 内的 skill（可重复）
  --no-goal-loop        只尝试一次，跳过外层 loop
  --env-file PATH       显式指定 .env（§3）
  --no-dotenv           不去当前工作目录搜索 .env（§3）
  --config PATH         显式指定配置文件（§4）
  --json                在 stdout 输出机器可读的结果（§7）
  --dry-run             做 preflight 和接线，但不调用 model（§6）
  -q, --quiet           不输出 preflight 回显和 ledger
```

`<ref>` 是本地路径、hub 中的名字，或一个 git URL —— 与 `AutoAgent.from_pretrained`
接受的完全一致。[`registry.resolve`](../src/teacup_run/registry.py) 已经做了这层解析，
CLI 只是它的第二个调用者。

当 `<task>` 为 `-` 时，任务从 stdin 读取，这样笔记可以直接 pipe 进来。

后续的子命令，每个都是对现有函数的薄包装：`pull`（`registry.clone`）、
`eval`（`AutoAgent.eval`）、`publish`（`AutoAgent.push_to_hub`）、`inspect`（打印
manifest、tools、checks、skills、budget）。`run` 一个都不依赖。

## 2. Preflight

每次运行，按这个顺序：

1. 把 ref 解析成一个 package 目录。
2. 加载并校验 manifest。
3. 解析环境变量（§3）。
4. 检查 `environment.required` —— 每个名字都存在，且不是占位符。
5. 回显 agent 名称与版本、model、budget、tools、skills，以及**环境变量来自哪个来源**，
   让「为什么用了那个 key」不用调试器就能回答。
6. 到这一步才调用 model。

第 4、5 步的存在，是为了让缺失的 key 在任何花费之前就失败，而不是跑到一半 401。
[`env.py:17`](../src/teacup_run/env.py#L17) 已经会拒绝占位符值（`sk-...`、
`your-key-here`）；它尚未覆盖的是「值根本不存在」，而那恰恰是每个新用户都会撞上的情况。

**第 4 步不能挪进 `AgentSpec.validate()`。** 后者是在 `from_pretrained()` 内部跑的，
而测试套件是用一个伪造的 model、在完全没有 key 的情况下加载 `examples/note-taker` 的
—— 今天 `env -u OPENAI_API_KEY pytest` 是通过的。在那里强制检查会让这个库无法离线
使用。preflight 调用的是另一个独立的查询：

```python
def missing_environment(self) -> tuple[str, ...]:
    """Declared environment variables that are absent or still placeholders."""
```

## 3. 环境变量

CLI 永远不读 agent package 内部的 `.env`。`.env` 从不进 git、也从不随包发布 ——
[`.gitignore`](../.gitignore) 在任意层级忽略它，[`registry.publish`](../src/teacup_run/registry.py)
通过 `ignore_patterns("__pycache__", "*.pyc", ".env")` 把它剥掉 —— 所以一个依赖它的
package，在别人 publish 的那一刻就会坏掉。凭据属于 agent 运行的那个环境，不属于制品。
package 只声明它需要的那些**名字**，通过 `environment.required`。

CLI 转而去这些地方找：

| # | 来源 | 面向 |
|---:|---|---|
| 1 | 已经导出的进程环境变量 | 生产环境：容器、systemd、CI secrets |
| 2 | `--env-file PATH` | 单次调用，显式指定 |
| 3 | 配置文件中的 `env_file:`（§4） | 发布出去、被安装的工具 |
| 4 | 从当前工作目录向上搜索到的 `.env` | 仅限本地开发 |

这两类来源的解析方式不同，实现时必须分开：**文件只会选中一个**（2，否则 3，否则 4），
而**规则 1 是逐个变量生效**、压过那个文件提供的值。一个导出了 `OPENAI_API_KEY`、同时
又挂载了一个装着过期 key 的 `.env` 的容器，拿到的是导出的那个值。
`load_env(override=False)` 现在就是这个行为。

规则 4 就是 [`load_env()`](../src/teacup_run/env.py) 现有的 cwd 向上搜索，原样不动，
可用 `--no-dotenv` 关闭。它是开发期的便利设施，不是正式机制。

**后续项：** [`.gitignore`](../.gitignore) 放行了 `.env.example`，但这个文件不存在。
变量名该放在那里 —— 已提交、不含值、新贡献者复制一份就能用。

## 4. 配置

`~/.config/teacup/config.yaml`，遵循 XDG。用 YAML，和 `agent.yaml`、
`benchmark.yaml` 一致。可被 `TEACUP_CONFIG` 覆盖，或按次调用用 `--config PATH`
覆盖。文件不存在是合法状态：每个键都有默认值，CLI 在完全没有配置文件时也必须能跑。
hub 缓存仍留在 [`hub_path()`](../src/teacup_run/registry.py) 指定的位置，
即 `~/.teacup/agents`。

```yaml
# ~/.config/teacup/config.yaml —— 只放设置，永远不放 secret。
env_file: ~/.config/teacup/secrets.env

defaults:
  budget_usd: 1.00
  model: null          # null：听 manifest 的

hub:
  path: ~/.teacup/agents
  auto_pull: false

output:
  ledger: true
  json: false
```

**配置文件不存放任何 secret 值，只存放一个指向 secret 所在位置的指针。** 正是这一点让
它可以安全地提交进 dotfiles 仓库 —— 而不管我们是否希望，这类文件最终都会进 dotfiles
仓库。

设置的优先级，从高到低：CLI flag，然后 `TEACUP_*` 环境变量（`TEACUP_HOME` 已经
存在，且必须继续压过 `hub.path`），然后配置文件，然后 agent 的 manifest，最后是内置
默认值。

这条链排的是**设置**；§3 排的是**凭据**。两者绝不能合并 —— 一个能直接设置
`OPENAI_API_KEY` 的配置文件会推翻 §3。

## 5. 输出与退出码

- **stdout** —— `result.answer`，除此之外什么都没有。
- **stderr** —— preflight 回显与 cost ledger。
- `--json` —— stdout 上一个 JSON 对象，且 stdout 上*没有别的东西*。

于是 `teacup run ... > answer.txt` 会留下一个干净的文件，同时 ledger 仍显示在终端
上；`teacup run ... --json | jq .cost.total` 也能正常工作。

| 退出码 | 含义 |
|---:|---|
| 0 | 已完成；goal 达成，或未声明任何 goal check |
| 1 | 已完成；goal 未达成 |
| 2 | 提前停止：超出 budget |
| 3 | 提前停止：运行时错误 |
| 4 | 没能开始：ref 无效、manifest 非法，或环境变量缺失 |

退出码 2 和 3 需要改动库本身。[`loop.py`](../src/teacup_run/loop.py) 目前把两者压进了
同一个字符串 —— `BudgetExceeded` 把 `stop_reason` 设为 `exc.reason`，普通异常设为
`f"{type(exc).__name__}: {exc}"` —— 靠 parse 这个字符串来区分是个坏味道。给 `Result`
加一个判别字段：

```python
stop_kind: str | None = None   # "budget" | "error" | None
```

## 6. `--dry-run`

执行除 model 调用之外的一切：解析、校验、preflight、import `tools.py` 和 `checks.py`、
构造 tool schema、渲染一份全是零的 ledger。它回答的是「这个 package 接线正确吗？我的
配置能跑它吗？」—— 不需要 key，不产生花费。

它是一次接线检查，不是一次运行，文档里也必须这样描述 —— 它说不了这个 agent 好不好。
一个录制过的或被 stub 掉的 model（`--replay`，建立在现有的 `model_fn` 接缝上）是另一个
特性。

## 7. `--json` 的形状

```json
{
  "agent":   {"name": "teacup/note-taker", "version": "0.1.0", "ref": "examples/note-taker"},
  "model":   "gpt-5-mini",
  "task":    "Notes: ...",
  "answer":  "Action items\n- Ray: ...",
  "goal":    {"met": true, "checks": {"non_empty": true}, "failed": [], "reasons": []},
  "attempts": 1,
  "tool_calls": ["save_action_item", "list_action_items"],
  "cost":    {"model": 0.0031, "tool": 0.02, "compute": 0.01, "total": 0.0331},
  "usage":   {"input_tokens": 1840, "output_tokens": 220, "cached_input_tokens": 0},
  "budget":  {"usd": 0.25, "remaining": 0.2169},
  "stopped": {"early": false, "kind": null, "reason": null},
  "elapsed_s": 7.4,
  "exit_code": 0
}
```

除两个字段外，其余都能从今天已有的 `Result`、`GoalVerdict`、`Ledger`、`Budget` 上直接
读到。例外是：来自 §5 的 `stopped.kind`，以及 `budget.remaining` —— 后者今天只是
[`Ledger.render`](../src/teacup_run/budget.py#L158) 内部的一个表达式。

## 8. 实施计划

| # | 改动 | 文件 |
|---:|---|---|
| 1 | 给 `Result` 加 `stop_kind`，在两个 `except` 处分别赋值 | `src/teacup_run/loop.py` |
| 2 | `AgentSpec.missing_environment()` | `src/teacup_run/manifest.py` |
| 3 | `Budget.remaining(ledger)` —— 从 `Ledger.render` 里提出来 | `src/teacup_run/budget.py` |
| 4 | 让 `load_env` 的 cwd 搜索可被跳过；它本来就接受显式路径、也本来就返回实际使用的文件，preflight 直接回显该返回值 | `src/teacup_run/env.py` |
| 5 | 配置加载器：读取、默认值、优先级（§4） | `src/teacup_run/config.py` *(新增)* |
| 6 | 从 `AgentSpec` 中删掉没人用的 `entrypoint` 字段 | `src/teacup_run/manifest.py` |
| 7 | `cli.py`：参数解析、preflight、运行、渲染、退出码、`--json` | `src/teacup_run/cli.py` *(新增)* |
| 8 | `[project.scripts] teacup = "teacup_run.cli:main"` | `pyproject.toml` |
| 9 | `.env.example`，只有变量名、没有值 | 仓库根目录 |
| 10 | 测试：preflight 失败、退出码、JSON 形状、`--dry-run` | `tests/test_cli.py` *(新增)* |

第 1 到 6 项都很小，且各自独立可用；第 7 项是主体。第 6 项在兼容性上零成本 ——
`AgentSpec.to_dict` 返回的是 `dict(self.raw)`，所以已有 `agent.yaml` 里的 `entrypoint:`
键在 publish 时仍会原样往返。

测试沿用现有的 `model_fn` 接缝，所以整套 CLI 测试无需 key、不产生花费，和现有测试套件
一致。

## 9. 开放问题

1. **`run` 遇到未解析的 ref 时应该自动 pull 吗？** 还是要求先跑 `teacup pull`？
   自动 pull 更友好；显式 pull 则意味着 `run` 永远不会自己去访问网络。
2. **「goal 未达成」用退出码 1**，会把一次诚实完成、没超预算的运行判成失败。对 CI 来说
   是对的，交互使用时可能意外。保持原样，还是放到 `--strict` 后面？
