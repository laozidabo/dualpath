"""DualPath 命令行入口。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from . import __version__
from .llm import (
    DEFAULT_MODEL,
    DeepSeekModel,
    DualPathError,
    OpenAICompatibleModel,
)
from .orchestrator import DualPath
from .reporting import ConsoleReporter, ReviewConsoleReporter
from .review import ReviewEngine, ReviewResult
from .search import DuckDuckGoSearch, NoSearch

ENV_FILE = Path(".env")
REVIEW_SUCCESS_STATUSES = {"completed", "no_findings", "draft_only"}


def load_env_file(path: Path = ENV_FILE) -> None:
    """从工作目录 .env 补充环境变量；不覆盖已经存在的变量。"""
    try:
        if not path.is_file():
            return
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value


def positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError("必须是整数") from None
    if value < 1:
        raise argparse.ArgumentTypeError("必须大于等于 1")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dualpath",
        description="DualPath：失败先行、证据驱动的任务校验框架。",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    review = subparsers.add_parser("review", help="评审任务/方案：失败清单 + 原文依据 + 检查项")
    review.add_argument("task", help="要评审的任务或方案")
    review.add_argument(
        "--context",
        action="append",
        metavar="FILE",
        help="附加资料；失败模式的 rule_quote 可以引用其中的原文",
    )
    review.add_argument("--max-findings", type=int, default=5, choices=range(1, 11), metavar="1..10")
    _add_model_options(review)
    review.add_argument("--json", dest="json_path", help="把完整评审记录保存为 JSON")
    review.add_argument("--markdown", dest="markdown_path", help="把完整评审记录保存为 Markdown")
    review.add_argument("--quiet", action="store_true", help="隐藏过程信息，只输出最终结果")

    run = subparsers.add_parser("run", help="[已弃用] 旧的双线路 + 概率分级模式")
    run.add_argument("task", help="要执行的任务")
    _add_model_options(run)
    run.add_argument("--max-problems", type=positive_int, default=5, help="错误线路最多探索的问题数")
    run.add_argument("--search-results", type=positive_int, default=4, help="每个问题最多使用的检索结果数")
    run.add_argument("--no-search", action="store_true", help="不搜索，所有概率由 AI 自行判断")
    run.add_argument("--json", dest="json_path", help="把完整执行记录保存为 JSON")
    run.add_argument("--markdown", dest="markdown_path", help="把完整执行记录保存为 Markdown")
    run.add_argument("--quiet", action="store_true", help="隐藏过程信息，只输出最终结果")

    verify = subparsers.add_parser("verify", help="用独立反例验证一个 Python 函数（需要 bwrap 与 DeepSeek key）")
    verify.add_argument("task", help="完整函数规范")
    verify.add_argument("--function", required=True, help="需要实现的公开函数名")
    verify.add_argument("--budget", type=float, required=True, help="本次目录内累计保守费用上限，人民币元")
    verify.add_argument("--output", required=True, help="新的记录目录；避免覆盖此前结果")
    verify.add_argument("--max-cases", type=int, default=3, choices=range(1, 6))
    verify.add_argument("--interaction-focus", action="store_true", help="优先发现多条规则共同作用的反例（实验选项）")
    return parser


def _add_model_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        choices=["deepseek", "openai-compatible"],
        default=os.getenv("DUALPATH_PROVIDER", "deepseek"),
        help="模型提供方，默认使用 DeepSeek",
    )
    parser.add_argument("--model", help="模型名称；也可用 DUALPATH_MODEL 设置")
    parser.add_argument("--api-key", help="API Key；建议通过环境变量或 .env 提供")
    parser.add_argument("--base-url", help="覆盖默认 API 地址")


def create_model(args: argparse.Namespace):
    if args.provider == "deepseek":
        api_key = args.api_key or os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise DualPathError("使用 DeepSeek 时，请设置 DEEPSEEK_API_KEY 或读取工作目录 .env")
        return DeepSeekModel(
            model=args.model or os.getenv("DUALPATH_MODEL") or os.getenv("DEEPSEEK_MODEL") or DEFAULT_MODEL,
            api_key=api_key,
            base_url=args.base_url,
        )

    api_key = args.api_key or os.getenv("DUALPATH_API_KEY") or os.getenv("OPENAI_API_KEY")
    model = args.model or os.getenv("DUALPATH_MODEL") or os.getenv("OPENAI_MODEL")
    if not api_key:
        raise DualPathError("请设置 DUALPATH_API_KEY、OPENAI_API_KEY 或读取工作目录 .env")
    if not model:
        raise DualPathError("使用 OpenAI 兼容接口时，请通过 --model 指定模型")
    return OpenAICompatibleModel(model=model, api_key=api_key, base_url=args.base_url)


def load_context_files(paths: list[str] | None) -> dict[str, str]:
    context: dict[str, str] = {}
    for raw in paths or []:
        path = Path(raw).expanduser()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            raise DualPathError(f"无法读取 context 文件：{raw}（{type(error).__name__}）") from None
        name = path.name
        if name in context:
            name = f"{path.name}#{len(context) + 1}"
        context[name] = text
    return context


def save_text(path: str, content: str) -> None:
    target = Path(path).expanduser()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as error:
        raise DualPathError(f"无法写入 {path}（{type(error).__name__}）") from None


def save_review_outputs(result: ReviewResult, args: argparse.Namespace) -> None:
    if args.json_path:
        save_text(args.json_path, json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if args.markdown_path:
        save_text(args.markdown_path, result.to_markdown())


def run_review(args: argparse.Namespace) -> int:
    model = create_model(args)
    context = load_context_files(args.context)
    engine = ReviewEngine(
        model=model,
        max_findings=args.max_findings,
        reporter=ReviewConsoleReporter(quiet=args.quiet),
    )
    result = asyncio.run(engine.run(args.task, context=context))
    save_review_outputs(result, args)
    print("\n最终结果：\n")
    print(result.final_output)
    return 0 if result.status in REVIEW_SUCCESS_STATUSES else 2


def run_legacy(args: argparse.Namespace) -> int:
    model = create_model(args)
    search = NoSearch() if args.no_search else DuckDuckGoSearch()
    engine = DualPath(
        model=model,
        search=search,
        reporter=ConsoleReporter(quiet=args.quiet),
        max_problems=args.max_problems,
        search_results=args.search_results,
    )
    result = asyncio.run(engine.run(args.task))
    if args.json_path:
        save_text(args.json_path, json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if args.markdown_path:
        save_text(args.markdown_path, result.to_markdown())
    print("\n最终结果：\n")
    print(result.final_output)
    return 0


def run_verify(args: argparse.Namespace) -> int:
    from .budgeted import BudgetedDeepSeek, Ledger, units
    from .sandbox import extract_code, sandbox_support
    from .verification import VerifiedDualPath

    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise DualPathError("请在本机设置 DEEPSEEK_API_KEY；不要将密钥写入命令行参数")
    if not sandbox_support()["supported"]:
        raise DualPathError("验证模式需要 Linux、bubblewrap 和可用的用户命名空间")
    units(args.budget)
    output = Path(args.output).expanduser()
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise DualPathError(f"输出目录已存在：{output}") from None
    with Ledger(output / "ledger.json", args.budget) as ledger:
        engine = VerifiedDualPath(
            BudgetedDeepSeek(key, ledger),
            max_cases=args.max_cases,
            interaction_focus=args.interaction_focus,
        )
        result = asyncio.run(engine.run(args.task, args.function))
        save_text(str(output / "result.json"), json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        save_text(str(output / "solution.py"), extract_code(result.final_output))
        print(f"状态：{result.status}；保守记账：{ledger.spent_units / 1_000_000:.6f} 元")
        print(result.final_output)
    return 0 if result.status in {"checks_passed", "repaired"} else 2


def main(argv: list[str] | None = None) -> None:
    load_env_file()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "review":
            code = run_review(args)
        elif args.command == "run":
            print(
                "警告：`dualpath run` 基于未校准的概率分级，将在 v0.3 移除；请改用 `dualpath review`。",
                file=sys.stderr,
            )
            code = run_legacy(args)
        else:
            code = run_verify(args)
    except KeyboardInterrupt:
        parser.exit(130, "\n执行已取消。\n")
    except (DualPathError, ValueError) as error:
        parser.exit(2, f"错误：{error}\n")
    if code:
        parser.exit(code)
