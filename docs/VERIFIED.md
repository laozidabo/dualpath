# Python 函数验证模式（实验性）

原有 DualPath 和 run 命令保留。VerifiedDualPath 为代码任务加入可执行证据，暂不适用于开放式研究或通用项目规划。

## 流程

1. 初稿生成与反例发现并行。反例发现只看到任务，看不到初稿。
2. 另一轮模型调用盲审每个反例的输入范围、规则优先级和预期值；看不到候选代码和运行结果。只有 accept 的反例可进入执行。
3. 在隔离进程中运行接纳的反例。全部通过则直接保留初稿，不进行无证据重写。
4. 发现失败后最多修正一次。修正必须通过全部已接纳检查，包括初稿已通过的检查，才替换初稿。
5. 修正退步、复核失败、截断或基础设施故障时保留初稿，并记录状态。初稿生成失败则没有可交付代码。

“独立”指输入和调用分离，默认仍是同一个模型，错误可能相关。引用题目原文并通过模型复核，也不保证预期值正确。checks_passed 和 repaired 只表示通过所列检查，不能推出整体正确或没有其他退步。

## 运行

需要 Python 3.10+、Linux、/usr/bin/python3、bubblewrap，以及允许用户命名空间隔离的内核配置。不可用时停止，不降级到宿主机执行。

先在本机设置 DEEPSEEK_API_KEY，再运行：

```bash
python -m venv venv
venv/bin/python -m pip install -e ".[dev]"
venv/bin/dualpath verify \
  "实现 abs_value(x)，x 是整数。返回整数的绝对值。" \
  --function abs_value --budget 0.30 --output verify-runs/example
```

输出目录必须是新目录，以免覆盖旧结果。result.json 记录全部状态、反例和执行观察；solution.py 是最终保留的代码；ledger.json 记录请求、响应、token 和保守费用。日志不保存密钥或请求头，但会保存任务和代码，应按私密材料管理。

V008 的小样本实验显示，孤立边界案例可能漏掉多条规则共同作用的缺陷。可添加 `--interaction-focus`，要求发现阶段优先构造排序、嵌套、合并、相等键、状态转换和规则优先级的组合输入。它不改变调用次数、案例数量或发布规则；result.json 会记录 `discovery_strategy`。该选项目前只有离线流程测试，尚无新的模型效果证据，默认行为及旧实验提示词保持不变。

返回码：通过已接纳检查为 0；验证未完成、没有可用检查或修正未通过为 2；取消为 130。返回码 0 也不是正确性证明。

预算接口限定 deepseek-flash，temperature=0.2，关闭 thinking，每次最多输出 2048 token。不自动重试网络请求。结构化反例有且仅有一次格式恢复机会，该调用同样记账。

费用按 2026-10-31 官方高峰价保守估算，输入缓存未命中/命中分别为 2/0.04 元每百万 token，输出 8 元每百万 token。请求前按 UTF-8 字节数的两倍加余量、最大输出 token 预留费用；成功按供应商用量结算，未知失败或中断保留全部预留额。账本采用微元整数和文件排他锁；缺失用量或超出预留额时停止新调用。

这是本地估算保护，不是供应商账户的硬限额。价格改变后须更新实现。不同输出目录有独立预算；CLI 不汇总用户其他进程或其他目录的花费。

价格来源：[DeepSeek 官方价格页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。

## 零费用演示和测试

```bash
venv/bin/python examples/verify_offline.py
venv/bin/pytest -q
```

演示使用预先编写的响应、真实隔离执行，展示修正成功和退步回退。它不是模型能力实验。测试还覆盖截断 JSON、非有限数字、字面量注入、超额调用、并发预算、中断/重启预留额、主机文件/环境/网络隔离，以及输入代码的时间和内存限制。

## Python 接口

```python
from dualpath import VerifiedDualPath, Completion

# 自定义 model 提供 async complete(system, user)。
# 推荐返回 Completion(text, finish_reason="stop") 以识别输出截断。
# 兼容原模型返回纯字符串；此时无法检查 finish_reason。
result = await VerifiedDualPath(model, max_cases=3).run(task, "function_name")
print(result.status, result.final_output)
```

可传入 initial=共享初稿，以支持配对实验。此时初稿不计入本次 engine 调用数；实验统计必须另外加回生成初稿的成本。

## 边界

- 只支持有限 Python 字面量参数、返回值相等和六种指定异常；不支持复杂对象、空集合的 set() 表达式、容差断言、交互和第三方依赖。
- 预期通过 ast.literal_eval 且限制长度、节点数、深度；不执行模型生成的测试代码。
- 沙箱限制内存、进程、文件输出、执行时间，并隔离网络和宿主环境；默认每项检查 4 秒。
- 沙箱保障进程和文件系统隔离，不保证防御恶意代码篡改同一 Python 进程的测试机制。该模式用于评估普通模型生成的函数。
- 接纳的有限反例不能验证所有性质，包括未覆盖输入、全部返回类型和副作用。真正的效果评测仍需模型看不到的独立测试集。
