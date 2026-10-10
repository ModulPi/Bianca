"""经理层前置实测：当前配的 LLM 到底能不能工具调用（function calling）。

**为什么要先测。** §5 风险 #4 写着"本地模型不支持 function-calling -> 切到 Ollama 后
经理层直接不可用"，对策是"**必须先实测**，不能假设可切换"。经理层的整个形态取决于这个
答案：能用工具就是"编排者"，不能用就只剩"每次叫一次 LLM 拿一段文本"这一条路。

而且**两边的假设都要测**：
  - "本地模型不支持" —— 可能过时了（Ollama 的 `deepseek-r1` 现在 family 是 qwen3）
  - "云端 deepseek 一定支持" —— 也没验过

这个脚本只跑到"模型有没有回 tool_calls"为止，**不执行任何工具**。

    PYTHONIOENCODING=utf-8 python scripts/check_llm_tools.py
    PYTHONIOENCODING=utf-8 python scripts/check_llm_tools.py --provider deepseek
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

sys.path.insert(0, ".")

import httpx  # noqa: E402

from agent.config import Settings  # noqa: E402
from agent.llm.provider import resolve_llm_endpoint  # noqa: E402

# 一个最小但真实形状的工具集：一个只读查询、一个会改状态的动作。
# 后者刻意放进来 —— 如果模型连"哪个工具会改状态"都分不出来，经理层就不能给它权限。
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_position",
            "description": "查询当前账户里某个资产的持仓数量与市值。只读。",
            "parameters": {
                "type": "object",
                "properties": {"asset": {"type": "string", "description": "资产符号，如 BTC"}},
                "required": ["asset"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "place_order",
            "description": "提交一笔市价单。**会真的动钱。**",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "side": {"type": "string", "enum": ["BUY", "SELL"]},
                    "amount": {"type": "number"},
                },
                "required": ["symbol", "side", "amount"],
            },
        },
    },
]

# 问题刻意只可能命中只读的那个工具 —— 用它来验"选得对不对"，
# 而不只是"有没有回 tool_calls"。
QUESTION = "我现在手里有多少 BTC？只查一下，不要下单。"


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def show(label: str, value: object) -> None:
    print(f"  {label:<22} {value}")


def line(ok: bool, text: str) -> None:
    print(f"  [{'OK ' if ok else 'FAIL'}] {text}")


async def probe(settings: Settings) -> int:
    ep = resolve_llm_endpoint(settings)

    section("0. 这次测的是谁")
    show("provider", ep.provider)
    show("model", ep.model)
    show("endpoint", ep.chat_completions_url)
    show("timeout", ep.timeout)
    show("api_key", "有" if ep.api_key else "无")

    payload = {
        "model": ep.model,
        "messages": [
            {"role": "system", "content": "你可以调用工具。能用工具回答就调工具，不要凭空猜。"},
            {"role": "user", "content": QUESTION},
        ],
        "tools": TOOLS,
        "temperature": 0.2,
    }
    headers = {"Content-Type": "application/json"}
    if ep.api_key:
        headers["Authorization"] = f"Bearer {ep.api_key}"

    section("1. 发一次带 tools 的请求")
    show("问题", QUESTION)
    show("给了几个工具", f"{len(TOOLS)}（{', '.join(t['function']['name'] for t in TOOLS)}）")

    try:
        async with httpx.AsyncClient(timeout=ep.timeout) as client:
            resp = await client.post(ep.chat_completions_url, headers=headers, json=payload)
    except httpx.ConnectError as exc:
        line(False, f"连不上：{exc}")
        print("         endpoint 没起？本地模型要先 `ollama serve`。")
        return 1
    except httpx.TimeoutException:
        line(False, f"超时（{ep.timeout}s）—— 推理模型首次加载会慢，可把 LLM_TIMEOUT 调大重试")
        return 1

    show("HTTP", resp.status_code)
    if resp.status_code != 200:
        line(False, f"非 200：{resp.text[:280]}")
        return 1

    data = resp.json()
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    calls = msg.get("tool_calls") or []
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""

    section("2. 回执里有什么")
    show("content 长度", len(content))
    show("reasoning 长度", len(reasoning))
    show("tool_calls 条数", len(calls))
    show("usage", json.dumps(data.get("usage"), ensure_ascii=False))

    if not calls:
        line(False, "**模型没有回 tool_calls** —— 这个模型不能做经理层的工具编排")
        if content:
            show("它回的话", content[:300])
        print("         对策见设计文档 §5 风险 #4：经理层需要保留「非工具」降级路径，")
        print("         或换一个支持工具调用的模型/供应商。")
        return 1

    line(True, "模型回了 tool_calls —— 工具调用这条路是通的")
    for i, c in enumerate(calls):
        fn = (c.get("function") or {})
        show(f"  调用 {i}", f"{fn.get('name')}  args={fn.get('arguments')}")

    section("3. 它选对了吗")
    names = [(c.get("function") or {}).get("name") for c in calls]
    line("get_position" in names, "选了只读的 get_position（问题只可能命中它）")
    line("place_order" not in names, "**没有**去碰会动钱的 place_order")

    called = next((c for c in calls if (c.get("function") or {}).get("name") == "get_position"), None)
    if called:
        try:
            args = json.loads((called["function"].get("arguments") or "{}"))
        except json.JSONDecodeError as exc:
            line(False, f"arguments 不是合法 JSON：{exc}")
            return 1
        line(str(args.get("asset", "")).upper() == "BTC", f"参数解析正确：{args}")

    print("\n结论：见上面每一行的 [ OK ] / [FAIL]。")
    print("注意这里**只验证到「模型会不会调工具」**，没执行任何工具，也没验证多轮往返。")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["deepseek", "ollama"], default=None,
                        help="覆盖 .env 里的 LLM_PROVIDER，好拿两个都测一遍")
    parser.add_argument("--model", default=None, help="覆盖模型名")
    parser.add_argument("--base-url", default=None,
                        help="覆盖 LLM_BASE_URL。测云端要显式给，因为 .env 里指向的是本地 Ollama")
    args = parser.parse_args()

    settings = Settings()
    update: dict = {}
    if args.provider:
        update["llm_provider"] = args.provider
    if args.model:
        update["llm_model"] = args.model
    if args.base_url:
        update["llm_base_url"] = args.base_url
    if update:
        settings = settings.model_copy(update=update)

    print("经理层前置实测 —— 当前 LLM 能不能工具调用（function calling）")
    return await probe(settings)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
