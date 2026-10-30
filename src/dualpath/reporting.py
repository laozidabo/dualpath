"""执行过程与最终问题的用户展示。"""

from __future__ import annotations

from .models import Problem


class Reporter:
    def run_started(self, task: str) -> None:
        pass

    def direct_completed(self) -> None:
        pass

    def problems_ready(self, problems: list[Problem]) -> None:
        pass

    def convergence_started(self) -> None:
        pass

    def run_completed(self) -> None:
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

    def run_completed(self) -> None:
        self.write("双线路执行完成。")
