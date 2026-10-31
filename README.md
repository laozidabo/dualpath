# DualPath

失败先行、证据驱动的 AI 任务审查与验证工具。

v0.2 不再用概率或风险等级描述模型猜测，而是要求每条失败项都尽量带上可核对的原文依据和可检验的信号：

- `dualpath review`：面向通用任务，先生成草稿，再独立列出失败清单并逐条审阅；
- `dualpath verify`：面向 Python 函数，独立提出反例、盲审预期值、在隔离沙箱中执行；
- `dualpath run`：旧的双线路 + 概率分级模式，保留但已弃用，计划在 v0.3 移除。

两轮小样本实验的结论是克制的：目前没有证据表明 DualPath 或验证模式优于普通自检。完整记录见 [实验与成果索引](reports/README.md)。

## 快速开始

要求 Python 3.10 或更高版本，以及可用的 DeepSeek API Key。

```bash
git clone https://github.com/laozidabo/dualpath.git
cd dualpath
python -m venv venv
venv/bin/python -m pip install -e ".[dev]"

# 方式一：环境变量
export DEEPSEEK_API_KEY="你的 DeepSeek API Key"

# 方式二：写入当前工作目录的 .env（程序启动时自动读取）
```

```bash
venv/bin/dualpath review "为一个开源 Python 项目设计并实现命令行待办事项管理器"
```

`review` 和 `verify` 默认使用 `deepseek-flash`，可通过 `--model` 或环境变量覆盖。程序启动时读取**当前工作目录**的 `.env`，只补充缺失的环境变量，不覆盖 shell 中已有的同名变量。不要将真实密钥写进命令行参数或提交到仓库，参见 [SECURITY.md](SECURITY.md)。

## review：通用任务的失败清单

```bash
venv/bin/dualpath review "为下面的接口写一份错误处理规范：……" \
  --context docs/api.md \
  --context examples/response.json \
  --max-findings 5 \
  --json review.json \
  --markdown review.md
```

一次 review 的执行过程：

1. 草稿线路直接完成用户任务；
2. 失败发现线路只阅读任务和 `--context` 提供的资料，提出最多 `--max-findings` 条具体失败模式；
3. 独立审阅线路判断每条失败模式是否有原文支持、检查项是否可用，给出 `supported`、`ambiguous` 或 `rejected`；
4. 只有被支持的失败模式会交给修正线路整合进最终结果。

一次 review 最多发起 4 次模型调用（草稿、发现、审阅各一次；有被支持的失败模式时再加一次修正）。没有 supported finding 时为 3 次；失败发现阶段遇到 JSON 格式问题时会重问一次，最多 5 次；返回空清单时只调用 2 次（草稿、发现）。默认产出 5 条以内的 finding，不输出概率、百分比或风险等级。

### 参数

| 参数 | 说明 |
| --- | --- |
| `--context FILE` | 附加任务资料，可重复；单个文件不超过 32 KiB、合计不超过 64 KiB；finding 的 `rule_quote` 必须出现在任务或这些资料中 |
| `--max-findings N` | 最多保留的失败模式数量，默认 5，允许 1–10 |
| `--provider` | `deepseek`（默认）或 `openai-compatible` |
| `--model` | 模型名称 |
| `--api-key` | 不推荐；优先使用环境变量或 `.env` |
| `--base-url` | 覆盖 API 地址 |
| `--json PATH` | 保存完整记录（任务、草稿、findings、最终结果、调用次数） |
| `--markdown PATH` | 保存可阅读版本 |
| `--quiet` | 隐藏过程输出 |

### 输出与状态

每条 finding 包含：`id`、`title`、`mechanism`、`trigger`、`rule_quote`、`rule_source`、`check_kind`、`check`、`expected_signal`、`status`、`review_reason`。其中：

- `rule_quote` 必须是任务或 context 中的连续原文片段（比较时归一化空白），由程序校验；
- `check_kind` 为 `executable`（可执行检查）或 `manual`（需要人工确认）；
- `status` 为 `supported`（原文支持且检查可用）、`ambiguous`（存在歧义）或 `rejected`（不支持或不可用）。

运行状态：

| 状态 | 含义 |
| --- | --- |
| `completed` | 完成审阅并产出最终结果 |
| `no_findings` | 没有发现满足证据条件的失败模式，采用草稿 |
| `draft_only` | 没有获得支持的失败模式，采用草稿 |
| `probe_failed` | 失败发现阶段不可用，保留草稿 |
| `review_failed` | 审阅阶段不可用，保留草稿 |
| `model_failed` | 草稿阶段不可用，没有最终结果 |

退出码：`0` 表示流程完成（`completed`、`no_findings`、`draft_only`）；`2` 表示运行失败或降级（`probe_failed`、`review_failed`、`model_failed`、输入或环境错误）；`130` 表示用户取消。详细 schema 见 [docs/REVIEW.md](docs/REVIEW.md)。

`no_findings` 不等于任务正确，`supported` 也不等于已被证明。review 是审阅工具，不是正确性证明。

## verify：Python 函数的可执行证据

`verify` 面向代码任务：针对一个 Python 函数，独立提出具体反例、盲审预期值、在 bubblewrap 沙箱中隔离执行，只有在观察到失败后才修正一次。

需要 Python 3.10+、Linux、`/usr/bin/python3`、bubblewrap 和允许用户命名空间隔离的内核配置，以及 `DEEPSEEK_API_KEY`。沙箱不可用时会停止，不会降级到宿主机执行。

```bash
venv/bin/dualpath verify \
  "实现 abs_value(x)，x 是整数。返回整数的绝对值。" \
  --function abs_value --budget 0.30 --output verify-runs/example
```

输出目录必须是新目录，以免覆盖旧结果。`result.json` 记录全部状态、反例和执行观察，`solution.py` 是最终保留的代码，`ledger.json` 记录请求、token 与保守费用。退出码：通过已接纳检查为 `0`；验证未完成、没有可用检查、修正未通过或启动失败为 `2`；取消为 `130`。不自动重试网络请求，费用按输出目录独立记账。

完整的流程、预算细节、`--interaction-focus` 实验选项和边界见 [docs/VERIFIED.md](docs/VERIFIED.md)。

## 实验结论

两轮实验都是自建小样本，每题只运行一次，不能据此宣称一般性优越性，也不支持显著性结论。

| 实验 | 直接作答 | 普通自检 | 改进方法 |
| --- | ---: | ---: | ---: |
| 第一轮：30 道 Python 函数题 | 26/30 | 五轮自审 28/30 | 原 DualPath 28/30 |
| 第二轮：8 道 Python 函数题 | 7/8 | 一轮自检 8/8 | 验证模式 7/8 |

第一轮中，五轮自审与原 DualPath 每一道题的通过/失败完全一致，DualPath 的估算费用高约 22%，没有观察到独有收益。第二轮中，一轮自检 8/8；三轮自检在有结果的 6 题上 6/6（两题因接口/截断缺失），不能与其他方法的 8 题分母直接比较；验证模式 7/8，漏掉的 V008 被一轮自检修好。两轮都没有「DualPath 或验证模式优于普通自检」的证据。

两轮累计保守记账 4.74333148 元，包含未知失败请求的预留，是本地估算，不是供应商账单。原始记录、冻结协议、离线复现和逐题分析见 [reports/README.md](reports/README.md)。

## Legacy：已弃用的 run 模式

`dualpath run` 是 v0.1 的双线路执行模式：线路一直接解决，线路二探索错误路线，问题按模型估计的概率分级，最后共同汇聚。v0.2 保留其行为以兼容旧脚本，但启动时会向 stderr 打印弃用警告，并计划在 v0.3 移除。

```bash
# 仍然可用，但不建议用于新工作
venv/bin/dualpath run "你的任务"
```

| 旧用法 | v0.2 迁移建议 |
| --- | --- |
| `dualpath run "通用任务"` | `dualpath review "通用任务"` |
| `run --max-problems 5` | `review --max-findings 5`（语义不同：不再有概率或等级） |
| `run --json/--markdown` | `review --json/--markdown` |
| `run` 的按概率分级展示 | 不再提供；review 只按审阅状态组织 finding |
| `run --search-results`、`run --no-search` | 不再提供；review 不联网检索，证据来自任务与 `--context` |
| 代码函数任务 | `dualpath verify "函数规范" --function NAME ...` |

`run` 的旧输出字段保持兼容；v0.2 额外记录 `status`（`completed` 或 `degraded`）和 `failures`，用于说明哪些阶段降级，不再把失败伪装成成功。

## 配置

| 变量 | 说明 |
| --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek API Key |
| `DEEPSEEK_MODEL` | DeepSeek 模型名称，默认 `deepseek-flash` |
| `DEEPSEEK_BASE_URL` | 覆盖 DeepSeek 接口地址 |
| `DUALPATH_PROVIDER` | 默认提供方，默认 `deepseek` |
| `DUALPATH_API_KEY` / `OPENAI_API_KEY` | OpenAI 兼容接口的 Key |
| `DUALPATH_BASE_URL` / `OPENAI_BASE_URL` | OpenAI 兼容接口地址 |
| `DUALPATH_MODEL` / `OPENAI_MODEL` | 模型名称；`DUALPATH_MODEL` 对两种提供方都生效，优先级高于 `DEEPSEEK_MODEL` |

`.env` 自动加载只补缺失项：shell 里已经设置的变量优先。模型名称与价格以 [DeepSeek 官方 API 文档](https://api-docs.deepseek.com/) 为准。

## 开发

```bash
venv/bin/python -m pip install -e ".[dev]"
venv/bin/pytest -q
venv/bin/ruff check .
venv/bin/python examples/verify_offline.py   # 零 API 调用的离线演示，需要 bubblewrap
```

贡献约束见 [CONTRIBUTING.md](CONTRIBUTING.md)：证据优先、不把模型判断写成正确性证明、不做概率宣传、测试与文档同步。

## 局限

- 两轮实验都是自建题，不是公开基准，也没有独立第三方出题；
- 只测试了单一模型、单次采样；没有跨模型、多次重复或显著性证据；
- review 的 finding 覆盖范围受模型发现能力限制；`rule_quote` 只保证引文出现在材料中，不保证规则本身正确或完整；
- verify 只覆盖有限反例和指定异常，通过检查不等于整体正确；
- 闭环（草稿 → 失败发现 → 审阅 → 修正）能整理和暴露问题，但不会凭空引入任务之外的事实；
- 运行记录会保存任务原文和模型输出，可能包含敏感信息，请按私密材料管理。

## 仓库结构

```text
src/dualpath/
├── cli.py            # 命令行入口
├── review.py         # review：草稿、失败发现、审阅、修正
├── verification.py   # verify：反例、盲审、隔离执行、修正
├── sandbox.py        # bubblewrap 隔离执行
├── budgeted.py       # 预算账本与费用保护
├── orchestrator.py   # legacy run 的双线路编排
├── paths.py          # legacy run 的直接线路与错误线路
├── models.py         # 数据模型
├── prompts.py        # 各阶段提示词
├── llm.py            # 模型接口、超时与 JSON 解析
├── reporting.py      # 控制台输出
└── search.py         # legacy run 的检索接口
docs/                 # REVIEW / DESIGN / VERIFIED 说明
reports/              # 两轮实验结论与索引
experiments/          # 冻结的题目、协议与分析脚本
tests/                # 单元与隔离执行测试
examples/             # 离线演示
artifacts/            # 历史归档与校验清单
```

## 许可

MIT License。安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。
