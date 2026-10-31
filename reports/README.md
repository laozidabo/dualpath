# 实验与成果索引

2026-10-31。两轮实验、审计、实现和后续选项均已归档。真实 API 密钥、私密配置、虚拟环境和运行缓存不属于发布材料。

| 成果 | 入口 | 结论或用途 |
| --- | --- | --- |
| 第一轮：30 题对照与审计 | [完整报告](original-results.md) | 直接回答 26/30，五轮自检 28/30，原版 DualPath 28/30；未显示独有收益 |
| 第一轮全部源码、题目、响应、审计 | [复现归档](../artifacts/dualpath-experiment-reproducibility.zip) | 保留原始快照和校验清单 |
| 第二轮：8 题验证模式 | [完整报告](verified-results.md) | 直接回答 7/8，一轮自检 8/8，验证模式 7/8；三轮自检 6/6，另有两题缺失 |
| 第二轮题目、冻结协议、分析代码 | [实验目录](../experiments/verified_pilot/) | 原批次和补充批次分别冻结，未重抽失败题 |
| 第二轮原始响应、费用和评分 | [运行记录](../verify-runs/) | 含认证失败、连接错误和截断记录 |
| 验证模式、隔离执行、预算保护 | [使用说明](../docs/VERIFIED.md) | 通过有限案例不等于整体正确 |
| 规则交互发现选项 | [使用说明](../docs/VERIFIED.md) | 有离线测试，尚无真实模型效果证据 |
| 历史报告、补丁、源码与复现包 | [归档说明](../artifacts/README.md) | 旧版本按原样保留，最新结论以本目录为准 |

当前代码 63 项测试通过。两轮累计保守记账 4.74333148 元，含未知失败预留，不是供应商实际账单；原总预算为 10 元。

## 本机离线复核

在仓库根目录执行：

```bash
python -m venv venv
venv/bin/python -m pip install -e '.[dev]'
venv/bin/pytest -q
venv/bin/python experiments/verified_pilot/replay_frozen.py
venv/bin/python experiments/verified_pilot/analyze.py
```

需要 Linux、bubblewrap、可用的用户命名空间。复放和统计不调用模型，无需 API 密钥。`replay_frozen.py` 使用归档中的冻结源码；当前源码已加入后续选项，因此直接运行旧协议的付费入口会因源码散列变化而拒绝执行，这是预期保护。

`analyze.py` 会重新生成合并分析 JSON。第一轮复验方法在其归档中的 README-audit.md；解压到独立目录后操作，以保留现有原始记录。
