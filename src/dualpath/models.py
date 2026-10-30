"""DualPath 的核心数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Evidence:
    """互联网检索得到的真实资料，不允许由模型凭空生成。"""

    title: str
    url: str
    snippet: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


@dataclass
class Problem:
    """错误线路探索到的问题，以及它的概率与等级。"""

    id: str
    title: str
    description: str
    exploration: str
    probability: float
    reasoning: str
    basis: str
    evidence: list[Evidence] = field(default_factory=list)
    level: int = 0

    @property
    def probability_percent(self) -> str:
        return f"{self.probability:.0%}"

    @property
    def basis_label(self) -> str:
        return {
            "internet": "互联网资料",
            "model_judgment": "AI自行判断（无检索资料）",
            "mixed": "互联网资料与AI判断",
        }.get(self.basis, self.basis)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "exploration": self.exploration,
            "probability": self.probability,
            "level": self.level,
            "reasoning": self.reasoning,
            "basis": self.basis,
            "basis_label": self.basis_label,
            "evidence": [item.to_dict() for item in self.evidence],
        }


def classify_problems(problems: list[Problem]) -> list[Problem]:
    """按概率从高到低分级，最高概率固定为一级，相同概率同级。"""

    ordered = sorted(problems, key=lambda item: item.probability, reverse=True)
    distinct_probabilities = sorted({item.probability for item in ordered}, reverse=True)
    levels = {probability: index + 1 for index, probability in enumerate(distinct_probabilities)}
    for problem in ordered:
        problem.level = levels[problem.probability]
    return ordered


@dataclass
class ErrorPathResult:
    output: str
    problems: list[Problem]


@dataclass
class RunResult:
    task: str
    direct_output: str
    error_output: str
    direct_convergence: str
    error_convergence: str
    final_output: str
    problems: list[Problem]
    started_at: str
    finished_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "paths": {
                "direct": self.direct_output,
                "error": self.error_output,
                "direct_convergence": self.direct_convergence,
                "error_convergence": self.error_convergence,
            },
            "problems": [problem.to_dict() for problem in self.problems],
            "final_output": self.final_output,
        }

    def to_markdown(self) -> str:
        lines = [
            "# DualPath 执行结果",
            "",
            f"- 任务：{self.task}",
            f"- 开始时间：{self.started_at}",
            f"- 完成时间：{self.finished_at}",
            "",
            "## 最终结果",
            "",
            self.final_output,
            "",
            "## 问题与概率等级",
            "",
            "最高概率的问题为一级；概率相同的问题同级。",
            "",
        ]
        if not self.problems:
            lines.append("错误线路未发现需要展示的问题。")
        for problem in self.problems:
            lines.extend(
                [
                    f"### {problem.level}级问题：{problem.title}",
                    "",
                    f"- 出现概率：{problem.probability_percent}",
                    f"- 判断依据：{problem.basis_label}",
                    f"- 问题说明：{problem.description}",
                    f"- 错误路线推演：{problem.exploration}",
                    f"- 概率判断理由：{problem.reasoning}",
                    "",
                ]
            )
            if problem.evidence:
                lines.append("资料：")
                for evidence in problem.evidence:
                    title = evidence.title or evidence.url
                    lines.append(f"- [{title}]({evidence.url})：{evidence.snippet}")
                lines.append("")
        lines.extend(
            [
                "## 两条线路的汇聚记录",
                "",
                "### 线路一对错误的吸收",
                "",
                self.direct_convergence,
                "",
                "### 线路二对最终结果的贡献",
                "",
                self.error_convergence,
                "",
                "## 原始线路输出",
                "",
                "### 线路一：直接解决",
                "",
                self.direct_output,
                "",
                "### 线路二：错误的路",
                "",
                self.error_output,
                "",
            ]
        )
        return "\n".join(lines)
