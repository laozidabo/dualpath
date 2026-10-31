"""Python 函数的独立反例探索、题意复核与隔离执行。

模型复核不是正确性证明。结果区分题意判断与可重复的执行事实。
失败保留初稿；修正仅在全部接纳检查通过后发布。
"""
from __future__ import annotations

import ast
import asyncio
import json
import keyword
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Protocol

from .llm import JsonProtocolError, parse_strict_json
from .sandbox import extract_code, grade, sandbox_support

DIRECT = """[VERIFY_DIRECT]
实现用户给定的 Python 函数，只使用标准库。严格遵守题目，不补充未规定的规则。
只交付一个完整 Python 代码块，包含必要导入，不执行演示、不读标准输入。"""
DISCOVER = """[VERIFY_DISCOVER]
你独立阅读函数规范，不查看任何候选代码。提出最多 {max_cases} 个具体边界测试。
只针对规范明确规定的合法输入，或明确要求抛异常的输入；不要自行添加限制。
每个测试必须引用题目中连续、原样的规则文本。规则有歧义则不要猜测预期结果。
只输出完整 JSON：{{"cases":[{{"id":"C1","rule":"题目原文",
"reason":"简短说明","args":"(1,)","kwargs":"{{}}",
"expect":{{"kind":"return","value":"2"}}}}]}}。
args 是 Python 元组字面量，kwargs 是字符串键字典字面量。
返回值也用 Python 字面量字符串，可表示元组、列表、字典和集合，不可包含函数调用。
要求抛异常时 expect 改为 {{"kind":"raises","type":"ValueError"}}。
至多使用 ValueError、TypeError、KeyError、IndexError、ZeroDivisionError、OverflowError。
不输出测试代码，不输出概率。每个 reason 不超过 200 字，尽量简短以避免截断。"""
INTERACTION_DISCOVER = DISCOVER + """
额外覆盖要求：优先构造两条及以上规则同时起作用的输入，不要只测孤立边界。
若规范涉及排序、嵌套、合并、相等键、状态转换或规则优先级，必须组合这些条件，
并改变输入顺序、开闭状态或相等关系来检查它们之间的相互影响。"""
REVIEW = """[VERIFY_REVIEW]
独立审查下列测试与题意是否一致。你看不到候选代码，不要迎合测试作者。
逐一检查输入是否属于规定范围、期望值是否能从规范推出，尤其检查规则优先级。
只输出 JSON：{"decisions":[{"id":"C1","verdict":"accept","reason":"推导理由"}]}。
每个测试必须且只能出现一次。verdict 只能为 accept/reject/ambiguous。
不能确定时选 ambiguous；题目没有规定的行为选 reject。不要修改输入或预期值。
这只是模型题意判断，不能视为形式化证明。"""
REPAIR = """[VERIFY_REPAIR]
根据任务、候选代码和已执行的测试修正实现。测试预期经过独立模型审查，但仍可能有误。
核对规范；不得硬编码测试答案、篡改测试环境或削弱已满足的规则。
给出完整 Python 代码块，包含全部必要导入。保持所有已通过测试和其他任务要求。"""


@dataclass(frozen=True)
class Completion:
    text: str
    finish_reason: str = "unknown"


class Model(Protocol):
    async def complete(self, system: str, user: str) -> str | Completion: ...


ProtocolError = JsonProtocolError


class CallLimit(RuntimeError):
    pass


def strict_json(text: str):
    """仅接受完整 JSON 或一个完整围栏，绝不从截断数组抢救子对象。"""
    return parse_strict_json(text)


def literal(text: str):
    """限制长度与 AST 大小的字面量解析，不运行任意 Python 表达式。"""
    if not isinstance(text, str) or len(text.encode()) > 8192:
        raise ProtocolError("字面量过长或类型错误")
    try:
        tree = ast.parse(text, mode="eval")
        pending = [(tree, 0)]
        count = 0
        while pending:
            node, depth = pending.pop()
            count += 1
            if depth > 24 or count > 2000:
                raise ProtocolError("字面量结构过大")
            # literal_eval 也支持 set()；这里统一禁止调用和名称。
            if isinstance(node, (ast.Call, ast.Name, ast.Attribute, ast.Subscript)):
                raise ProtocolError("只允许字面量")
            pending.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
        value = ast.literal_eval(tree)
        pending_values = [value]
        while pending_values:
            value_item = pending_values.pop()
            if type(value_item) in {tuple, list, set}:
                pending_values.extend(value_item)
            elif type(value_item) is dict:
                pending_values.extend(value_item.keys())
                pending_values.extend(value_item.values())
            elif type(value_item) not in {str, int, float, bool, type(None)}:
                raise ProtocolError("不支持的字面量类型")
            elif type(value_item) is float and not math.isfinite(value_item):
                raise ProtocolError("禁止非有限数字")
        return value
    except (SyntaxError, ValueError, TypeError, RecursionError, OverflowError) as error:
        raise ProtocolError("无效或不支持的 Python 字面量") from error


EXCEPTIONS = {"ValueError", "TypeError", "KeyError", "IndexError", "ZeroDivisionError", "OverflowError"}


@dataclass(frozen=True)
class Case:
    id: str
    rule: str
    reason: str
    args: str
    kwargs: str
    expect: dict[str, str]

    def tests(self, entry_point: str) -> str:
        args, kwargs = literal(self.args), literal(self.kwargs)
        call = f"_dp_solution.{entry_point}(*{args!r}, **{kwargs!r})"
        source = "import solution as _dp_solution\n"
        if self.expect["kind"] == "raises":
            source += (f"try:\n    {call}\nexcept {self.expect['type']}:\n    pass\n"
                       "else:\n    raise AssertionError('expected exception was not raised')\nassert True\n")
        else:
            expected = literal(self.expect["value"])
            source += (
                f"_dp_actual = {call}\n"
                f"_dp_expected = {expected!r}\n"
                "assert _dp_actual == _dp_expected, ('unexpected result', "
                "repr(_dp_actual)[:1000], repr(_dp_expected)[:1000])\n"
            )
        return source


def parse_cases(text: str, task: str, max_cases: int) -> list[Case]:
    data = strict_json(text)
    if (
        not isinstance(data, dict)
        or set(data) != {"cases"}
        or not isinstance(data["cases"], list)
        or len(data["cases"]) > max_cases
    ):
        raise ProtocolError("反例清单结构错误或超过数量限制")
    cases, ids = [], set()
    for row in data["cases"]:
        if not isinstance(row, dict) or set(row) != {"id", "rule", "reason", "args", "kwargs", "expect"}:
            raise ProtocolError("反例字段错误")
        if not isinstance(row["id"], str) or not re.fullmatch(r"C[1-9][0-9]{0,2}", row["id"]) or row["id"] in ids:
            raise ProtocolError("反例 ID 无效或重复")
        for name in ("rule", "reason"):
            if not isinstance(row[name], str) or not row[name].strip() or len(row[name]) > 1000:
                raise ProtocolError("规则或理由缺失/过长")
        if row["rule"] not in task:
            raise ProtocolError("反例规则不是题目原文")
        args, kwargs = literal(row["args"]), literal(row["kwargs"])
        if type(args) is not tuple or type(kwargs) is not dict or not all(type(k) is str for k in kwargs):
            raise ProtocolError("args 必须是元组，kwargs 必须是字符串键字典")
        expectation = row["expect"]
        if not isinstance(expectation, dict):
            raise ProtocolError("预期结构错误")
        if expectation.get("kind") == "return" and set(expectation) == {"kind", "value"}:
            literal(expectation["value"])
        elif (
            expectation.get("kind") == "raises"
            and set(expectation) == {"kind", "type"}
            and isinstance(expectation["type"], str)
            and expectation["type"] in EXCEPTIONS
        ):
            pass
        else:
            raise ProtocolError("预期结构或异常类型错误")
        ids.add(row["id"])
        cases.append(Case(**row))
    return cases


def parse_decisions(text: str, cases: list[Case]) -> list[dict[str, str]]:
    data = strict_json(text)
    if not isinstance(data, dict) or set(data) != {"decisions"} or not isinstance(data["decisions"], list):
        raise ProtocolError("题意复核结构错误")
    ids = set()
    for row in data["decisions"]:
        if (not isinstance(row, dict) or set(row) != {"id", "verdict", "reason"}
                or not all(isinstance(v, str) for v in row.values())
                or row["id"] in ids or row["verdict"] not in {"accept", "reject", "ambiguous"}
                or not row["reason"].strip() or len(row["reason"]) > 2000):
            raise ProtocolError("题意复核字段错误")
        ids.add(row["id"])
    if ids != {case.id for case in cases}:
        raise ProtocolError("题意复核必须覆盖全部反例且不可增加 ID")
    return data["decisions"]


@dataclass
class VerificationResult:
    task: str
    entry_point: str
    initial_output: str = ""
    final_output: str = ""
    status: str = "initial_unavailable"
    cases: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    initial_checks: list[dict] = field(default_factory=list)
    repaired_checks: list[dict] = field(default_factory=list)
    attempted_repair: str = ""
    events: list[dict] = field(default_factory=list)
    model_calls: int = 0
    expectation_assurance: str = "model_reviewed_not_proven"

    def to_dict(self):
        return asdict(self)


class VerifiedDualPath:
    """最多一次 JSON 恢复、一次代码修正；任何验证故障均不发布修正。"""

    def __init__(self, model: Model, *, max_cases: int = 3, max_model_calls: int = 6,
                 timeout: float = 4, discovery_retries: int = 1, interaction_focus: bool = False):
        if type(max_cases) is not int or not 1 <= max_cases <= 5:
            raise ValueError("max_cases 必须在 1..5")
        if type(max_model_calls) is not int or not 2 <= max_model_calls <= 10:
            raise ValueError("max_model_calls 必须在 2..10")
        if type(discovery_retries) is not int or discovery_retries not in {0, 1}:
            raise ValueError("discovery_retries 必须为 0 或 1")
        if type(interaction_focus) is not bool:
            raise TypeError("interaction_focus 必须是布尔值")
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("timeout 必须在 (0,30]")
        self.model, self.max_cases, self.max_model_calls = model, max_cases, max_model_calls
        self.timeout, self.discovery_retries = timeout, discovery_retries
        self.interaction_focus = interaction_focus

    async def run(self, task: str, entry_point: str, *, initial: str | None = None) -> VerificationResult:
        if not isinstance(task, str) or not task.strip() or len(task.encode()) > 32_768:
            raise ValueError("任务为空或过长")
        if (
            not isinstance(entry_point, str)
            or not entry_point.isidentifier()
            or keyword.iskeyword(entry_point)
            or entry_point.startswith("_")
        ):
            raise ValueError("entry_point 必须是公开 Python 函数名")
        if initial is not None and not isinstance(initial, str):
            raise TypeError("initial 必须是文本")
        result = VerificationResult(task=task, entry_point=entry_point)
        if self.interaction_focus:
            result.events.append({"discovery_strategy": "interaction_focus"})

        async def complete(system, user):
            if result.model_calls >= self.max_model_calls:
                raise CallLimit("本次模型调用上限已到")
            result.model_calls += 1
            number = result.model_calls
            try:
                raw = await self.model.complete(system, user)
                response = Completion(raw) if isinstance(raw, str) else raw
                if not isinstance(response, Completion) or not isinstance(response.text, str):
                    raise ProtocolError("模型返回类型错误")
                result.events.append(
                    {
                        "call": number,
                        "stage": system.splitlines()[0],
                        "finish_reason": response.finish_reason,
                    }
                )
                if response.finish_reason not in {"stop", "unknown"}:
                    raise ProtocolError("模型响应未完整结束")
                return response.text
            except Exception as error:
                # 只记录错误类型，避免连接器异常携带密钥或请求头。
                result.events.append({"call": number, "stage": system.splitlines()[0], "error": type(error).__name__})
                raise

        async def discover():
            prompt = INTERACTION_DISCOVER if self.interaction_focus else DISCOVER
            for attempt in range(self.discovery_retries + 1):
                try:
                    text = await complete(
                        prompt.format(max_cases=self.max_cases),
                        json.dumps(
                            {"task": task, "entry_point": entry_point, "format_retry": attempt > 0},
                            ensure_ascii=False,
                        ),
                    )
                    return parse_cases(text, task, self.max_cases)
                except ProtocolError:
                    result.events.append(
                        {
                            "stage": "discovery_parse",
                            "attempt": attempt + 1,
                            "outcome": "invalid_or_truncated",
                        }
                    )
                    if attempt == self.discovery_retries:
                        raise

        async def direct():
            return initial if initial is not None else await complete(DIRECT, task)

        support = sandbox_support()
        if not support["supported"]:
            # 环境不支持时，连付费生成也不启动。
            result.initial_output = result.final_output = initial or ""
            result.status = "sandbox_unavailable"
            return result
        futures = [asyncio.create_task(direct()), asyncio.create_task(discover())]
        try:
            draft, proposed = await asyncio.gather(*futures, return_exceptions=True)
        finally:
            for future in futures:
                if not future.done():
                    future.cancel()
            await asyncio.gather(*futures, return_exceptions=True)
        for item in (draft, proposed):
            if isinstance(item, asyncio.CancelledError):
                raise item
        if isinstance(draft, BaseException) or not draft.strip():
            return result
        result.initial_output = result.final_output = draft
        if isinstance(proposed, BaseException):
            result.status = "discovery_unavailable"
            return result
        result.cases = [asdict(c) for c in proposed]
        if not proposed:
            result.status = "no_cases"
            return result
        try:
            reviewed = await complete(REVIEW, json.dumps({"task": task, "cases": result.cases}, ensure_ascii=False))
            result.decisions = parse_decisions(reviewed, proposed)
        except Exception:
            result.status = "review_unavailable"
            return result
        accepted_ids = {d["id"] for d in result.decisions if d["verdict"] == "accept"}
        accepted = [c for c in proposed if c.id in accepted_ids]
        if not accepted:
            result.status = "no_accepted_cases"
            return result

        async def execute(output):
            code = extract_code(output)
            checks = []
            for case in accepted:
                outcome = await asyncio.to_thread(grade, code, case.tests(entry_point), self.timeout)
                checks.append({"id": case.id, **outcome})
            return checks

        result.initial_checks = await execute(draft)
        if any(c["status"] == "infrastructure_error" for c in result.initial_checks):
            result.status = "verification_unavailable"
            return result
        if all(c["passed"] for c in result.initial_checks):
            result.status = "checks_passed"
            return result
        try:
            result.attempted_repair = await complete(
                REPAIR,
                json.dumps(
                    {
                        "task": task,
                        "candidate": draft,
                        "accepted_cases": [asdict(c) for c in accepted],
                        "observations": result.initial_checks,
                    },
                    ensure_ascii=False,
                ),
            )
        except Exception:
            result.status = "repair_unavailable"
            return result
        result.repaired_checks = await execute(result.attempted_repair)
        if any(c["status"] == "infrastructure_error" for c in result.repaired_checks):
            result.status = "verification_unavailable"
        elif all(c["passed"] for c in result.repaired_checks):
            result.final_output = result.attempted_repair
            result.status = "repaired"
        else:
            result.status = "repair_rejected"
        return result
