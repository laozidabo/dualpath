# 实验与成果索引

2026-11-01。三轮实验、审计、实现和后续选项均已归档。真实 API 密钥、私密配置、虚拟环境和运行缓存不属于发布材料。

| 成果 | 入口 | 结论或用途 |
| --- | --- | --- |
| 第一轮：30 题对照与审计 | [完整报告](original-results.md) | 直接回答 26/30，五轮自检 28/30，原版 DualPath 28/30；未显示独有收益 |
| 第一轮全部源码、题目、响应、审计 | [复现归档](../artifacts/dualpath-experiment-reproducibility.zip) | 保留原始快照和校验清单 |
| 第二轮：8 题验证模式 | [完整报告](verified-results.md) | 直接回答 7/8，一轮自检 8/8，验证模式 7/8；三轮自检 6/6，另有两题缺失 |
| 第二轮题目、冻结协议、分析代码 | [实验目录](../experiments/verified_pilot/) | 原批次和补充批次分别冻结，未重抽失败题 |
| 第二轮原始响应、费用和评分 | [运行记录](../verify-runs/) | 含认证失败、连接错误和截断记录 |
| 第三批：8 题默认发现 vs 规则交互发现 | [完整报告](interaction-pilot-results.md) | 四臂隐藏测试均 8/8（初稿全对）；变异检出 4/13 → 9/13；隐藏测试无退步，I005 覆盖回退 |
| 第三批题目、冻结协议、分析代码 | [实验目录](../experiments/interaction_pilot/) | 8 题、13 个变异体，冻结前离线校验通过 |
| 第三批原始响应、费用和评分 | [运行记录](../verify-runs/interaction-pilot/) | 56 次成功调用、0 错误、0 截断 |
| 第三批冻结全流程离线复放 | [审计结果](interaction-replay-audit.json) | 56 次请求、8 题评分、13 个变异体与费用逐项复核，无模型调用 |
| 验证模式、隔离执行、预算保护 | [使用说明](../docs/VERIFIED.md) | 通过有限案例不等于整体正确 |
| 规则交互发现选项 | [使用说明](../docs/VERIFIED.md) | 第三批真实小样本：变异检出 4/13 → 9/13；修复收益未测到 |
| 历史报告、补丁、源码与复现包 | [归档说明](../artifacts/README.md) | 旧版本按原样保留，最新结论以本目录为准 |

当前代码 118 项测试通过；修复与审计详情见 [本轮推进记录](progress-2026-11-01.md)。v0.2 起 `review` 取代概率叙事，`verify` 升为代码任务主推；三轮累计保守记账 4.93607448 元（第三批 0.192743 元），含未知失败预留，不是供应商实际账单；原总预算为 10 元。

## 本机离线复核

在仓库根目录执行：

```bash
python -m venv venv
venv/bin/python -m pip install -e '.[dev]'
venv/bin/pytest -q
venv/bin/python experiments/verified_pilot/replay_frozen.py
venv/bin/python experiments/verified_pilot/analyze.py
venv/bin/python experiments/interaction_pilot/replay_frozen.py
```

需要 Linux、bubblewrap、可用的用户命名空间。复放和统计不调用模型，无需 API 密钥。`replay_frozen.py` 使用归档中的冻结源码；当前源码已加入后续选项，因此直接运行旧协议的付费入口会因源码散列变化而拒绝执行，这是预期保护。

`analyze.py` 会重新生成合并分析 JSON。第一轮复验方法在其归档中的 README-audit.md；解压到独立目录后操作，以保留现有原始记录。
