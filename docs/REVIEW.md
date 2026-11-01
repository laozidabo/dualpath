# review 模式说明（v0.2）

`dualpath review` 面向通用任务：先生成草稿，再独立枚举具体失败模式，逐条核对原文依据和检查项，只把获得支持的失败模式并入最终结果。它**不输出概率、百分比或风险等级**，也不把审阅意见写成正确性证明。

## 输入

```bash
dualpath review "任务或方案原文" \
  [--context FILE ...] \
  [--max-findings N] \
  [--provider deepseek|openai-compatible] \
  [--model NAME] \
  [--api-key KEY] \
  [--base-url URL] \
  [--json PATH] [--markdown PATH] [--quiet]
```

| 参数 | 说明 |
| --- | --- |
| `task` | 必填，任务或方案原文；不能为空 |
| `--context FILE` | 可重复。附件资料，UTF-8 文本；单个文件不超过 32 KiB，合计不超过 64 KiB。引用时以文件名为来源标识，同名文件会加 `#序号` |
| `--max-findings N` | 失败模式数量上限，默认 5，允许 1–10 |
| `--provider` | `deepseek`（默认）或 `openai-compatible` |
| `--model` | 模型名称；默认 `deepseek-flash` |
| `--api-key` | 不推荐；优先使用环境变量或工作目录 `.env` |
| `--base-url` | 覆盖 API 地址 |
| `--json PATH` | 保存完整 JSON 记录 |
| `--markdown PATH` | 保存 Markdown 记录 |
| `--quiet` | 隐藏过程输出，只打印最终结果 |

程序启动时读取当前工作目录的 `.env`，只补充缺失的环境变量，不覆盖已有变量。API Key 也可以来自 `DEEPSEEK_API_KEY`（DeepSeek）或 `DUALPATH_API_KEY` / `OPENAI_API_KEY`（OpenAI 兼容接口）。

## 执行流程

1. **草稿与失败发现并行启动。** 草稿线路直接完成任务；失败发现线路只看到任务和 `--context`，看不到草稿。
2. **失败发现必须通过结构校验。** 模型需返回 `{"findings": [...]}`；数量 0–`--max-findings`，字段必须与约定完全一致，`rule_quote` 必须能在任务或资料原文中定位。返回空清单时直接采用草稿，状态记为 `no_findings`，不进入审阅和修正。
3. **盲审。** 独立审阅线路只看到任务和失败清单，看不到草稿和最终结果；逐条给出 `accept`、`reject` 或 `ambiguous`，且必须恰好覆盖全部失败模式。
4. **有条件修正。** 只有 `accept`（即 `supported`）的失败模式会交给修正线路并入草稿；没有 supported 时直接采用草稿，不进行无证据重写。
5. **失败可降级。** 草稿阶段失败返回 `model_failed`；发现或审阅失败时保留草稿并给出对应状态，不伪造成功。

四个阶段都接收相同的完整任务和 context；失败发现与盲审仍不接收草稿。盲审必须结合规则上下文、例外和优先级判断，不只相信 finding 中摘录的文字。该修复增加了附带资料时的输入 token，但不增加调用次数。

空白草稿按 `model_failed` 处理；空白修正按 `review_failed` 处理并保留草稿。审阅或修正阶段的超时、连接失败和接口拒绝也会保留草稿，不向调用方丢失已有结果。兼容接口返回明确的非 `stop` 结束标记（例如 `length`、`content_filter`）时拒绝该响应；接口不提供结束标记时无法检测截断。非法枚举字段的类型按结构错误处理：发现阶段至多重问一次，盲审阶段直接降级。

模型调用次数：最多 4 次（草稿、发现、审阅各一次；有被支持的失败模式时再加一次修正）。没有 supported finding 时为 3 次；返回空清单时为 2 次（草稿、发现），不进入审阅和修正；失败发现阶段遇到 JSON 格式问题时会重问一次，因此最多 5 次；审阅阶段不重试。网络请求不自动重试，默认超时 60 秒。

## 输出 schema

`--json` 保存的顶层结构：

```json
{
  "task": "任务原文",
  "status": "completed",
  "model_calls": 4,
  "started_at": "2026-10-31T00:00:00Z",
  "finished_at": "2026-10-31T00:00:10Z",
  "draft": "草稿正文",
  "final_output": "最终结果正文",
  "findings": [
    {
      "id": "F1",
      "title": "短标题",
      "mechanism": "失败如何发生",
      "trigger": "触发条件",
      "rule_quote": "任务或资料中的连续原文",
      "rule_source": "task",
      "check_kind": "executable",
      "check": "具体检查步骤或输入",
      "expected_signal": "预期观察到的信号",
      "status": "supported",
      "review_reason": "审阅理由"
    }
  ]
}
```

### findings 字段

| 字段 | 来源 | 说明 |
| --- | --- | --- |
| `id` | 模型 | 形如 `F1`，同一次运行内唯一 |
| `title` | 模型 | 最多 120 字 |
| `mechanism` | 模型 | 失败机理，最多 500 字 |
| `trigger` | 模型 | 触发条件，最多 500 字 |
| `rule_quote` | 模型 | 任务或 context 中的连续原文，至少 8 个字符，最多 500 字；比较时归一化空白 |
| `rule_source` | 程序 | 引文来源：`task` 或 context 文件名（同名文件带 `#序号`） |
| `check_kind` | 模型 | `executable`（可执行检查）或 `manual`（人工确认） |
| `check` | 模型 | 具体检查步骤或输入，最多 1000 字 |
| `expected_signal` | 模型 | 检查通过或失败时应观察到的信号，最多 500 字 |
| `status` | 程序 | `supported` / `ambiguous` / `rejected`；审阅未完成时内部保留 `pending` |
| `review_reason` | 程序 | 审阅给出的简短理由；`pending` 时为空 |

`rule_quote` 的校验是在任务和各 context 文件原文中做空白归一化后的连续子串匹配。它保证引文出现在材料中，但不保证引文完整覆盖规则，也不保证规则本身正确。

### 状态

| finding 状态 | 含义 |
| --- | --- |
| `supported` | 原文支持，且检查项具体可用；会进入修正阶段 |
| `ambiguous` | 原文或规则存在歧义，不进入修正阶段 |
| `rejected` | 原文不支持、引文不成立或检查项不可用；不进入修正阶段 |
| `pending` | 审阅未完成时的内部状态，例如审阅阶段失败、记录中尚未写入结论 |

运行状态：

| 状态 | 含义 | 最终结果 |
| --- | --- | --- |
| `completed` | 审阅完成，且至少有一条 supported finding | 修正后的结果 |
| `no_findings` | 失败发现返回空清单；只发生草稿与发现 2 次调用，不进入审阅和修正 | 草稿 |
| `draft_only` | 有失败清单，但没有 supported finding | 草稿 |
| `probe_failed` | 失败发现阶段失败：模型错误、超时或结构校验未通过 | 草稿 |
| `review_failed` | 审阅或修正阶段失败：模型错误、超时或结构校验未通过 | 草稿 |
| `model_failed` | 草稿阶段失败，没有可交付结果 | 空字符串 |

状态只描述流程走到哪一步，不表示任务正确或失败清单完整。

## 退出码

| 退出码 | 场景 |
| --- | --- |
| `0` | `completed`、`no_findings`、`draft_only`（流程完成，有可交付的最终结果） |
| `2` | `probe_failed`、`review_failed`、`model_failed`；输入错误、context 读取失败、输出写入失败、缺少密钥等可预期错误（用户取消为 130） |
| `130` | 用户按 Ctrl-C 取消 |

退出码 0 只表示流程完成，不表示审阅结论正确。即使退出码为 2，失败发现或审阅阶段的部分结果也可能已保存在 JSON / Markdown 记录中。

## Markdown 记录

`--markdown` 输出包含：任务、状态、模型调用次数、起止时间、最终结果，以及按 `supported` / `ambiguous` / `rejected` / `pending` 分组的失败清单。每条 finding 展示机理、触发条件、规则依据（含来源）、检查方式和预期信号；当最终结果与草稿不同时，额外附上草稿。

## 作为库使用

```python
import asyncio
from dualpath import ReviewEngine
from dualpath.llm import DeepSeekModel


async def main():
    engine = ReviewEngine(DeepSeekModel(), max_findings=5)
    result = await engine.run("任务原文", context={"spec.md": "资料原文"})
    print(result.status, result.model_calls)
    print(result.final_output)
    for finding in result.findings:
        print(finding.id, finding.status, finding.title)


asyncio.run(main())
```

`model` 只需提供 `async complete(system, user) -> str`。ReviewEngine 直接调用 `complete`，并自行对失败发现和审阅结果做严格 JSON 解析与格式重试；它不会使用模型对象上的其他方法。传入自定义模型时，仍需自行保证超时和错误处理。

## 局限与安全

- 失败发现由模型完成，覆盖范围受模型能力限制；`supported` 不等于 finding 真的会在生产中发生。
- 盲审与修正默认使用同一模型，错误可能相关。
- `rule_quote` 只保证文本一致性；`check_kind=manual` 的检查需要人工执行，程序不会验证它。
- `--context` 和任务原文会发送给模型提供方，记录中会保存任务、引用的资料片段和模型输出；完整附件不会另存进 JSON。不要把密钥或无关的敏感数据放进 `--context`；运行记录应按私密材料管理。
