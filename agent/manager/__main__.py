"""经理层 CLI（阶段 B）：给一个目标，产出计划。**不下任何单。**

    PYTHONIOENCODING=utf-8 python -m agent.manager "把回撤控制在 5% 以内，仓位别超过一半"

模型走 `.env` 里的 `LLM_PROVIDER` / `LLM_MODEL` / `LLM_API_KEY`
（2026-10-11 起默认 DeepSeek 官方 API，本地 Ollama 线搁置，见设计文档 §6.4.1）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

sys.path.insert(0, ".")

from agent.config import Settings  # noqa: E402
from agent.manager.loop import Manager  # noqa: E402


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("goal", nargs="?", default=None, help="自然语言交易目标")
    parser.add_argument("--max-rounds", type=int, default=6)
    parser.add_argument("--transcript", action="store_true", help="把实际发出的 messages 打出来")
    args = parser.parse_args(argv)

    if not args.goal:
        parser.error("给一个目标，例如：python -m agent.manager 「把回撤控制在 5% 以内」")

    settings = Settings()
    manager = Manager(settings=settings)

    section("0. 这次是谁在跑")
    print(f"  provider      {manager.endpoint.provider}")
    print(f"  model         {manager.endpoint.model}")
    print(f"  endpoint      {manager.endpoint.chat_completions_url}")
    print(f"  工具（只读）   {', '.join(manager.registry.names())}")
    print(f"  目标          {args.goal}")

    run = asyncio.run(manager.plan(args.goal, max_rounds=args.max_rounds))

    section("1. 它调了哪些工具")
    if not run.invocations:
        print("  （一个都没调）")
    for inv in run.invocations:
        mark = "OK " if inv.ok else "FAIL"
        detail = json.dumps(inv.arguments, ensure_ascii=False)
        print(f"  [{mark}] 第{inv.round}轮 {inv.name} {detail}")
        if not inv.ok:
            print(f"         -> {inv.error}")

    section("2. 计划")
    if run.plan is None:
        if any(e.startswith("跟模型说话失败") for e in run.errors):
            # 连不上模型。**别把这句写成「模型没给计划」** —— 那是在替它背锅。
            print("  **没能问上模型**，所以谈不上有没有计划。")
            for e in run.errors:
                if e.startswith("跟模型说话失败"):
                    print(f"  {e}")
        else:
            print(f"  **没拿到结构化计划**（plan_shape={run.plan_shape}）")
            print("  它最后说的是：")
            print(f"  {run.final_text or '（空）'}")
    else:
        print(f"  目标复述   {run.plan.objective}")
        for i, step in enumerate(run.plan.steps, 1):
            print(f"  {i}. {step.what}")
            if step.why:
                print(f"     依据：{step.why}")
        if run.plan.stop_conditions:
            print("  终止条件：")
            for cond in run.plan.stop_conditions:
                print(f"    - {cond}")
        if run.plan.assumptions:
            print("  前提假设：")
            for a in run.plan.assumptions:
                print(f"    - {a}")
        if run.plan.risks:
            print("  风险：")
            for r in run.plan.risks:
                print(f"    - {r}")
        missing = run.plan.steps_without_reason
        if missing:
            print(f"  **这些步骤没写依据：{missing}**")

    section("3. 账")
    print(f"  轮次        {run.rounds}")
    print(f"  每轮形状    {run.shapes or '（一轮都没跑成）'}")
    print(f"  plan_shape  {run.plan_shape}")
    print(f"  usage       {json.dumps(run.usage, ensure_ascii=False)}")
    if run.errors:
        print(f"  错误 {len(run.errors)} 条：")
        for e in run.errors:
            print(f"    - {e}")

    if args.transcript:
        section("4. 实际发出的 messages（ADR-019 R1）")
        for i, m in enumerate(run.transcript):
            body = str(m.get("content") or "")[:200]
            print(f"  [{i}] {m.get('role')}: {body}")

    print("\n注意：阶段 B 的经理**只有只读工具**，这一轮没有、也不可能产生任何订单。")
    return 0 if run.plan is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
