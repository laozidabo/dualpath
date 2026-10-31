# 第三批：默认反例发现 vs 规则交互反例发现

状态：**已设计并通过离线校验，尚未进行真实模型运行**。本目录是独立批次，不修改 V001–V008 的题目、协议或原始记录。

## 目的与假设

第二轮实验显示，验证模式的失效点主要在「反例发现覆盖不足」：V008 的缺陷需要同时组合「相同左端点、嵌套、不同开闭状态」三条规则才会暴露，而默认发现只生成了孤立边界案例；一次普通自检反而修好了它。

本批检验的假设只有一个：**在预先冻结的新题集上，明确要求「规则交互」的反例发现（`interaction_focus`）能否比默认发现多修复缺陷、多检出预设变异，同时不引入退步，且费用可接受。** 这是一次探索性小样本试验，不作显著性、泛化或优越性结论。

## 设计

- **8 道新题（I001–I008）**：整数比例分配、语义化版本比较、通配符匹配、时长解析、稳定去重、整数令牌桶、配置解析、连续区间合并。题目强调多条规则同时起作用时的边界与优先级。
- 每题含参考实现、隐藏测试与 1 个单规则变异体；离线校验要求参考实现通过全部隐藏测试、变异体至少被一条隐藏测试抓住（`validate.py`）。
- **4 个对照臂**，共享同一份初稿：
  - `direct`：初稿本身；
  - `self_1`：一次「反馈 → 重写」；
  - `verified_default`：验证模式，默认反例发现，最多 3 个案例；
  - `verified_interaction`：验证模式，`interaction_focus=True`，其余参数完全相同。
- 每 3 题轮换 3 个非 direct 臂的先后顺序；隐藏测试和参考实现不进入生成请求。
- 模型温度 0.2、关闭 thinking、每次最多 2048 token；单次重复。
- 预算：本批全局增量保守上限 2.0 元，单条件每题上限 0.35 元；此前实验累计保守记账 4.74333148 元，本批完成后累计仍低于原授权 10 元。
- 任一接口错误、截断或结构失败时保留已有输出、完成当前任务后停止；不重抽题、不替换题。

## 运行

需要 Linux、bubblewrap、可用的用户命名空间，以及有效的 `DEEPSEEK_API_KEY`。密钥可以放在仓库根目录的 `.env`（已被 `.gitignore` 忽略），程序启动时读取；不会覆盖 shell 中已有变量，也不会把密钥写入结果目录。

```bash
# 1. 离线校验并冻结；冻结后 run.py 会拒绝任何代码/题库/协议变化
venv/bin/python experiments/interaction_pilot/validate.py

# 2. 零费用演练（脚本化响应，只验证管线，不产生实验数据）
venv/bin/python experiments/interaction_pilot/run.py --scripted --output /tmp/interaction-scripted

# 3. 真实运行（花费计入本地保守账本）
venv/bin/python experiments/interaction_pilot/run.py --output verify-runs/interaction-pilot

# 4. 离线汇总（不调用模型）
venv/bin/python experiments/interaction_pilot/analyze.py --output verify-runs/interaction-pilot
```

输出目录必须是新目录。`results/` 保存逐题记录，`ledger.json` 保存请求与费用，`summary.json` 保存逐题评分与案例审计，`analysis.json` / `analysis.md` 是离线汇总。

## 指标

- 隐藏测试通过数，以及相对 `direct` 的修复数/退步数；
- 两个验证臂的状态分布（`checks_passed`、`repaired`、`no_cases` 等）；
- 生成反例数、被接受数、参考实现是否同意被接受案例的期望值（分歧视为错误预期信号）；
- 预设变异检出数（被接受反例作为测试运行在变异体上，任一失败即检出）；
- 调用数、截断数、错误数与峰值价保守记账。

## 边界

- 题目与隐藏测试仍由本次实现者编写，不是独立第三方基准；单模型、单次采样。
- `interaction_focus` 只改发现阶段的提示词，不增加案例数量、调用次数或修正次数。
- 通过隐藏测试不等于实现正确；未检出的变异体也不证明反例发现无效。
- 本批结果只用于判断「规则交互发现是否值得继续投入」，不能外推为一般性结论。
