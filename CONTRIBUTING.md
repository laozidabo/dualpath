# 参与 DualPath

欢迎提交 Issue 和 Pull Request。提交前请先阅读 [SECURITY.md](SECURITY.md)，不要在任何位置写入真实 API Key。

## 核心约束

v0.2 的目标是「失败先行、证据驱动」。修改时请保持以下约束：

1. **证据优先。** review 的 `rule_quote` 必须能在任务或提供的 context 原文中定位（比较时归一化空白）；能执行检查的 finding 优先给出可执行检查，无法自动验证的必须标为 `manual`；规则有歧义时审阅状态记为 `ambiguous`。证据不足时不得为了凑数编造。
2. **不得把模型判断写成正确性证明。** review 通过、verify 的 `checks_passed`、findings 被标为 `supported`，都只表示已有证据下的状态，不是「保证正确」。文档和提交信息都要避免这类表述。
3. **不做概率宣传。** v0.2 的 review / verify 不输出概率或风险百分比。旧 `run` 模式的概率分级仅作为遗留行为保留，不为它增加新功能或夸大其含义。
4. **测试与文档同步。** 修改命令行参数、JSON / Markdown 输出结构、运行状态或退出码时，必须同时更新测试与 `README.md`、`docs/` 中的对应说明。
5. **不伪造实验证据。** 不重抽已冻结的题目，不修改原始运行记录来制造改善，不把离线演示说成模型能力实验。新增实验需另立冻结协议和输出目录。

## 开发流程

```bash
python -m venv venv
venv/bin/python -m pip install -e ".[dev]"
venv/bin/pytest -q
venv/bin/ruff check .
venv/bin/python examples/verify_offline.py   # 需要 Linux + bubblewrap；不调用模型
```

`verify` 依赖 Linux、bubblewrap 和可用的用户命名空间；不可用时相关测试会自动 skip，不要改成在宿主机执行。真实模型调用会消耗费用，调试优先使用 `examples/verify_offline.py` 或脚本化模型。

## Pull Request

请在描述中说明：

- 修改内容与原因；
- 测试方式和结果（`pytest -q`、`ruff check .`）；
- 是否影响命令行参数、JSON / Markdown 结构或运行状态；
- 是否新增或改变了证据含义（例如某个状态从「已验证」变成「仅人工检查」）。

涉及 `src/` 与 `tests/` 的改动请保持相互对应；文档改动可以单独提交。
