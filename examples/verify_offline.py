"""脚本化响应演示，零 API 调用；只验证管线，不代表真实模型能力。"""
import asyncio
import json

from dualpath import VerifiedDualPath

TASK = "实现 abs_value(x)，x 是整数。返回整数的绝对值。"
INITIAL = "def abs_value(x): return x"


class ScriptedModel:
    def __init__(self, repair):
        self.repair = repair

    async def complete(self, system, user):
        stage = system.splitlines()[0]
        if stage == "[VERIFY_DIRECT]":
            return INITIAL
        if stage == "[VERIFY_DISCOVER]":
            return json.dumps({"cases": [
                {"id": id, "rule": "返回整数的绝对值", "reason": "检查正负输入",
                 "args": repr((x,)), "kwargs": "{}", "expect": {"kind": "return", "value": repr(abs(x))}}
                for id,x in [("C1",-2),("C2",5)]
            ]}, ensure_ascii=False)
        if stage == "[VERIFY_REVIEW]":
            return json.dumps({"decisions": [
                {"id": id, "verdict": "accept", "reason": "按整数绝对值规则直接计算"}
                for id in ("C1","C2")
            ]}, ensure_ascii=False)
        if stage == "[VERIFY_REPAIR]":
            return self.repair
        raise ValueError("未知演示阶段")


async def main():
    rows = []
    for label,repair in [("修正成功","def abs_value(x): return abs(x)"),
                         ("退步回退","def abs_value(x): return -x")]:
        result = await VerifiedDualPath(ScriptedModel(repair)).run(TASK,"abs_value")
        rows.append({"scenario": label, "result": result.to_dict()})
    print(json.dumps({"mode": "scripted_offline_no_api", "records": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
