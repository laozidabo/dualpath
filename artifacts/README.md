# 历史交付材料

本目录保留工作过程中已经产生的报告、补丁、校验清单和复现归档。历史文件按原样保存，因此可能写有“认证失败”“待运行”或“尚未推送”等当时状态；这些不代表项目当前状态。最新结果请读 [成果索引](../reports/README.md) 和 [第二轮最终报告](../reports/verified-results.md)。

- `dualpath-experiment-*`、`dualpath-audit-verification.json`、`SHA256SUMS`：第一轮 30 题实验及审计。
- `dualpath-v2-attempt1-source.zip`：第一次认证失败时的源码快照。
- `dualpath-v2-report.md`、`dualpath-v2-audit.json`：首次认证失败时的阶段报告和审计，不是最终实验结论。
- `dualpath-v2-reproducibility.zip`：后续真实实验所用的冻结源码。当前离线复放脚本使用此归档，不覆盖它。
- `dualpath-v2-offline-demo.json`：预编写模型响应驱动的演示，只证明流程，不是模型效果证据。
- `dualpath-v2-results.md`、`dualpath-v2-final-reproducibility.zip`：八题结果与后续规则交互选项的阶段归档。
- `dualpath-v2.patch`：当时源码补丁；仓库当前源码包含后续整理，因此该历史补丁不作为当前 HEAD 的完整补丁。
- `dualpath-v2-SHA256SUMS`、`dualpath-v2-final-SHA256SUMS`：对应阶段的原始校验清单。

完整原始记录及最新汇总也可直接在仓库 [verify-runs](../verify-runs/) 中查看。发布时另外生成根目录 `PUBLICATION_SHA256SUMS`，覆盖本次发布的材料。
