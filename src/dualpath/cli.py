"""DualPath 命令行入口。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from . import __version__
from .llm import DeepSeekModel, OpenAICompatibleModel
from .orchestrator import DualPath
from .reporting import ConsoleReporter
from .search import DuckDuckGoSearch, NoSearch


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dualpath",
        description="直接解决与错误路线并行推进，按概率展示问题，最后共同汇聚。",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="执行一个双线路任务")
    run.add_argument("task", help="要执行的任务")
    run.add_argument(
        "--provider",
        choices=["deepseek", "openai-compatible"],
        default=os.getenv("DUALPATH_PROVIDER", "deepseek"),
        help="模型提供方，默认使用 DeepSeek",
    )
    run.add_argument("--model", help="模型名称；也可用 DUALPATH_MODEL 设置")
    run.add_argument("--api-key", help="API Key；建议通过环境变量提供")
    run.add_argument("--base-url", help="覆盖默认 API 地址")
    run.add_argument("--max-problems", type=int, default=5, help="错误线路最多探索的问题数")
    run.add_argument("--search-results", type=int, default=4, help="每个问题最多使用的检索结果数")
    run.add_argument("--no-search", action="store_true", help="不搜索，所有概率由 AI 自行判断")
    run.add_argument("--json", dest="json_path", help="把完整执行记录保存为 JSON")
    run.add_argument("--markdown", dest="markdown_path", help="把完整执行记录保存为 Markdown")
    run.add_argument("--quiet", action="store_true", help="隐藏过程信息，只输出最终结果")
    verify = subparsers.add_parser("verify", help="用独立反例验证一个 Python 函数（实验模式）")
    verify.add_argument("task", help="完整函数规范")
    verify.add_argument("--function", required=True, help="需要实现的公开函数名")
    verify.add_argument("--budget", type=float, required=True, help="本次目录内累计保守费用上限，人民币元")
    verify.add_argument("--output", required=True, help="新的记录目录；避免覆盖此前结果")
    verify.add_argument("--max-cases", type=int, default=3, choices=range(1, 6))
    verify.add_argument("--interaction-focus", action="store_true", help="优先发现多条规则共同作用的反例")
    return parser


def create_model(args: argparse.Namespace, parser: argparse.ArgumentParser):
    if args.provider == "deepseek":
        api_key = args.api_key or os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            parser.error("使用 DeepSeek 时，请设置 DEEPSEEK_API_KEY 或传入 --api-key")
        return DeepSeekModel(
            model=args.model or os.getenv("DUALPATH_MODEL") or os.getenv("DEEPSEEK_MODEL") or "deepseek-v4-flash",
            api_key=api_key,
            base_url=args.base_url,
        )

    api_key = args.api_key or os.getenv("DUALPATH_API_KEY") or os.getenv("OPENAI_API_KEY")
    model = args.model or os.getenv("DUALPATH_MODEL") or os.getenv("OPENAI_MODEL")
    if not api_key:
        parser.error("请设置 DUALPATH_API_KEY、OPENAI_API_KEY 或传入 --api-key")
    if not model:
        parser.error("使用 OpenAI 兼容接口时，请通过 --model 指定模型")
    return OpenAICompatibleModel(model=model, api_key=api_key, base_url=args.base_url)


def save_text(path: str, content: str) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


async def run_task(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    model = create_model(args, parser)
    search = NoSearch() if args.no_search else DuckDuckGoSearch()
    engine = DualPath(
        model=model,
        search=search,
        reporter=ConsoleReporter(quiet=args.quiet),
        max_problems=args.max_problems,
        search_results=args.search_results,
    )
    result = await engine.run(args.task)
    if args.json_path:
        save_text(args.json_path, json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if args.markdown_path:
        save_text(args.markdown_path, result.to_markdown())
    print("\n最终结果：\n")
    print(result.final_output)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "run":
        try:
            asyncio.run(run_task(args, parser))
        except KeyboardInterrupt:
            parser.exit(130, "\n执行已取消。\n")
    elif args.command == "verify":
        from .budgeted import BudgetedDeepSeek, Ledger, units
        from .sandbox import extract_code, sandbox_support
        from .verification import VerifiedDualPath

        key = os.getenv("DEEPSEEK_API_KEY")
        if not key:
            parser.error("请在本机设置 DEEPSEEK_API_KEY；不要将密钥写入命令行参数")
        if not sandbox_support()["supported"]:
            parser.error("验证模式需要 Linux、bubblewrap 和可用的用户命名空间")
        try:
            units(args.budget)
            output = Path(args.output).expanduser()
            output.mkdir(parents=True, exist_ok=False)
            with Ledger(output / "ledger.json", args.budget) as ledger:
                engine = VerifiedDualPath(BudgetedDeepSeek(key, ledger), max_cases=args.max_cases, interaction_focus=args.interaction_focus)
                result = asyncio.run(engine.run(args.task, args.function))
                save_text(str(output / "result.json"), json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
                save_text(str(output / "solution.py"), extract_code(result.final_output))
                print(f"状态：{result.status}；保守记账：{ledger.spent_units / 1_000_000:.6f} 元")
                print(result.final_output)
                if result.status not in {"checks_passed", "repaired"}:
                    parser.exit(2, "验证未完成或未通过；已有初稿保存在结果中。\n")
        except KeyboardInterrupt:
            parser.exit(130, "\n执行已取消；未确认请求的预算预留额已保留。\n")
        except (ValueError, OSError) as error:
            parser.exit(2, "无法启动验证：" + type(error).__name__ + "\n")
