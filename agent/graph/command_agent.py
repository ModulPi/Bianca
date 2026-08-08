from __future__ import annotations

import json
import re
from typing import Any

from agent.config import Settings, get_settings
from agent.graph.orchestrator import append_chat_directive, append_chat_message, get_chat_messages
from agent.llm.analyzer import MarketAnalyzer
from agent.runner import get_runner


CHAT_SYSTEM = """你是 Bianca 交易 Agent 的指令解析器。用户用自然语言下达命令。
请输出 JSON（不要 markdown），格式：
{
  "intent": "start_agent|stop_agent|pause_symbol|resume_symbol|set_conservative|set_aggressive|query_status|unknown",
  "symbol": "BTCUSDT 或 ETHUSDT 或 null",
  "reply": "给用户的简短中文回复"
}
只解析意图，不要编造交易结果。"""


def _parse_json_loose(text: str) -> dict[str, Any]:
    text = text.strip()
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        text = match.group(0)
    return json.loads(text)


def _rule_parse(message: str) -> dict[str, Any] | None:
    msg = message.strip().lower()
    sym = None
    for s in ("btcusdt", "ethusdt", "btc", "eth"):
        if s in msg.replace(" ", ""):
            sym = "BTCUSDT" if "btc" in s else "ETHUSDT"
            break

    if any(w in msg for w in ("启动", "开始", "start", "跑起来")):
        return {"intent": "start_agent", "symbol": sym, "reply": "好的，我来启动 Agent。"}
    if any(w in msg for w in ("停止", "停下", "stop", "关闭")):
        return {"intent": "stop_agent", "symbol": sym, "reply": "好的，我来停止 Agent。"}
    if any(w in msg for w in ("暂停", "pause")) and sym:
        return {"intent": "pause_symbol", "symbol": sym, "reply": f"好的，暂停 {sym} 的自动决策。"}
    if any(w in msg for w in ("恢复", "resume", "继续")) and sym:
        return {"intent": "resume_symbol", "symbol": sym, "reply": f"好的，恢复 {sym}。"}
    if any(w in msg for w in ("保守", "谨慎", "conservative")):
        return {"intent": "set_conservative", "symbol": sym, "reply": "已切换为保守风格（需重启 Agent 后完全生效）。"}
    if any(w in msg for w in ("激进", "aggressive")):
        return {"intent": "set_aggressive", "symbol": sym, "reply": "已切换为激进风格（需重启 Agent 后完全生效）。"}
    if any(w in msg for w in ("状态", "怎么样", "意见", "status", "?")):
        return {"intent": "query_status", "symbol": sym, "reply": "正在查询当前状态…"}
    return None


async def _llm_parse(message: str, settings: Settings) -> dict[str, Any]:
    analyzer = MarketAnalyzer(settings)
    result = await analyzer._chat(
        [
            {"role": "system", "content": CHAT_SYSTEM},
            {"role": "user", "content": message},
        ],
        max_tokens=256,
    )
    content = result.content or "{}"
    try:
        return _parse_json_loose(content)
    except json.JSONDecodeError:
        return {"intent": "unknown", "symbol": None, "reply": content[:500]}


async def _apply_intent(parsed: dict[str, Any], *, session_id: str | None) -> dict[str, Any]:
    intent = parsed.get("intent", "unknown")
    symbol = parsed.get("symbol")
    if symbol:
        symbol = str(symbol).upper()
        if symbol == "BTC":
            symbol = "BTCUSDT"
        elif symbol == "ETH":
            symbol = "ETHUSDT"

    runner = get_runner()
    actions: list[dict[str, Any]] = []

    if intent == "start_agent":
        if not runner.running:
            await runner.start()
        actions.append({"action": "resume_all"})
    elif intent == "stop_agent":
        if runner.running:
            await runner.stop()
        actions.append({"action": "pause_all"})
    elif intent == "pause_symbol" and symbol:
        actions.append({"action": "pause_symbol", "symbol": symbol, "session_id": session_id})
    elif intent == "resume_symbol" and symbol:
        actions.append({"action": "resume_symbol", "symbol": symbol, "session_id": session_id})
    elif intent == "set_conservative":
        actions.append({"action": "set_style", "style": "conservative"})
    elif intent == "set_aggressive":
        actions.append({"action": "set_style", "style": "aggressive"})

    for act in actions:
        await append_chat_directive({**act, "session_id": session_id})

    if intent == "query_status":
        snap = await runner.get_snapshot()
        sym = symbol or (snap.symbols[0] if snap.symbols else "BTCUSDT")
        worker = snap.workers.get(sym)
        wtxt = f"{sym} 最近状态 {worker.last_status}" if worker else "无 Worker 数据"
        parsed["reply"] = (
            f"Agent {'运行中' if snap.running else '已停止'}，"
            f"模式 {snap.execution_mode}，{wtxt}。"
        )

    return {"intent": intent, "symbol": symbol, "reply": parsed.get("reply", ""), "actions": actions}


async def handle_chat_message(
    message: str,
    *,
    session_id: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    await append_chat_message("user", message)

    parsed = _rule_parse(message)
    if parsed is None and cfg.llm_configured:
        try:
            parsed = await _llm_parse(message, cfg)
        except Exception as exc:  # noqa: BLE001
            parsed = {"intent": "unknown", "symbol": None, "reply": f"理解失败：{exc}"}
    if parsed is None:
        parsed = {
            "intent": "unknown",
            "symbol": None,
            "reply": "暂未理解该指令。可试试：启动 Agent、暂停 BTC、查询状态。",
        }

    result = await _apply_intent(parsed, session_id=session_id)
    await append_chat_message("assistant", result["reply"], meta={"intent": result["intent"]})
    history = await get_chat_messages(limit=50)
    return {**result, "messages": history}
