# Changelog

本项目的主要变更记录在此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.2.0] - 2026-10-31

### Added

- 新增 `dualpath review`：面向通用任务的失败清单审查。每条 finding 包含机理、触发条件、必须能在任务或资料原文中定位的 `rule_quote`、检查方式、预期信号和审阅状态，不输出概率数字。
- 新增 `docs/REVIEW.md`，说明 review 的输入、findings 字段、检查方式、运行状态与退出码。
- 新增 `docs/DESIGN.md`，记录 v0.2 去掉概率分级的理由、保留「失败先行 + 可验证证据」的依据和 v0.3 计划。
- 新增 GitHub Actions CI：`ruff` 静态检查，以及 Python 3.10–3.13 测试矩阵（含 bubblewrap 安装与离线验证演示）。
- 新增 Issue 表单、`SECURITY.md` 和本 Changelog。

### Changed

- 项目定位改为「失败先行、证据驱动」：`verify` 升为代码任务的主推模式，`review` 用于通用任务。
- 默认模型改为 `deepseek-flash`。
- 程序启动时自动读取工作目录 `.env`，只补充缺失的环境变量，不覆盖已有环境变量。
- 运行时健壮性：模型请求默认 60 秒超时且不自动重试，接口错误、截断或非法 JSON 会给出明确的运行状态；review 只有 `supported` finding 才进入修正，其余 finding 保留状态但不并入最终结果；`run` 在部分阶段失败时降级并记录 `status` / `failures`，不再让单条线路失败毁掉整次运行。
- 统一退出码：成功或流程完成为 `0`，运行失败或输入/环境问题为 `2`，用户取消为 `130`。
- `pyproject.toml`：版本 0.2.0，新增 `ruff` 开发依赖、ruff 配置、项目链接和 Python 3.13 分类器。

### Deprecated

- `dualpath run`（旧双线路 + 概率分级模式）保留但已弃用，启动时向 stderr 打印弃用警告，计划在 v0.3 移除。迁移方式见 README 的 Legacy 小节。

## [0.1.0] - 2026-10-30

### Added

- 初始版本：`dualpath run` 双线路执行（直接解决 + 错误路线探索），按模型估计的概率对问题分级，最后共同汇聚。
- `dualpath verify` 实验模式：独立提出反例、盲审预期值、bubblewrap 隔离执行，仅在观察到失败后修正一次。
- DeepSeek 及 OpenAI 兼容接口、可选互联网检索、JSON / Markdown 记录。
- 两轮小样本实验、审计记录与离线复现材料，见 `reports/README.md`。
