"""执行过程与最终问题的用户展示。"""

from __future__ import annotations

from .models import Finding, Problem


class Reporter:
    def run_started(self, task: str) -> None:
        pass

    def direct_completed(self) -> None:
        pass

    def problems_ready(self, problems: list[Problem]) -> None:
        pass

    def convergence_started(self) -> None:
        pass

    def run_completed(self, status: str = "completed") -> None:
        pass


class ConsoleReporter(Reporter):
    def __init__(self, quiet: bool = False) -> None:
        self.quiet = quiet

    def write(self, message: str) -> None:
        if not self.quiet:
            print(message, flush=True)

    def run_started(self, task: str) -> None:
        self.write(f"任务：{task}")
        self.write("线路一（直接解决）与线路二（错误的路）已同时启动。")

    def direct_completed(self) -> None:
        self.write("线路一已形成直接解决结果。")

    def problems_ready(self, problems: list[Problem]) -> None:
        self.write("\n线路二发现的问题（按出现概率排序）：")
        for problem in problems:
            self.write(
                f"  {problem.level}级｜{problem.probability_percent}｜"
                f"{problem.title}｜{problem.basis_label}"
            )

    def convergence_started(self) -> None:
        self.write("\n两条线路开始汇聚，共同完成最终任务。")

    def run_completed(self, status: str = "completed") -> None:
        if status == "degraded":
            self.write("双线路执行完成（降级：部分阶段失败，已保留可用结果）。")
        else:
            self.write("双线路执行完成。")


class ReviewConsoleReporter:
    """review 模式的轻量进度展示。"""

    def __init__(self, quiet: bool = False) -> None:
        self.quiet = quiet

    def write(self, message: str) -> None:
        if not self.quiet:
            print(message, flush=True)

    def run_started(self, task: str) -> None:
        self.write(f"任务：{task}")
        self.write("草稿与失败发现已同时启动。")

    def draft_ready(self) -> None:
        self.write("草稿已生成。")

    def findings_ready(self, findings: list[Finding]) -> None:
        self.write(f"\n失败清单（{len(findings)} 条）：")
        for finding in findings:
            self.write(f"  {finding.id}｜{finding.status_label}｜{finding.title}")

    def run_completed(self, status: str) -> None:
        labels = {
            "completed": "评审完成。",
            "no_findings": "评审完成：没有发现失败模式，采用草稿。",
            "draft_only": "评审完成：没有获得支持的失败模式，采用草稿。",
            "probe_failed": "失败发现阶段不可用，已保留草稿。",
            "review_failed": "审阅阶段不可用，已保留草稿。",
            "model_failed": "草稿阶段不可用，没有最终结果。",
        }
        self.write(labels.get(status, f"评审结束（{status}）。"))
