from __future__ import annotations

import uuid
from typing import Any

from agent.config import Settings, get_settings
from agent.graph.state import TradeState
from agent.llm.analyzer import MarketAnalyzer
from agent.llm.prompts import base_asset_for_symbol, normalize_symbol, resolve_worker_symbol
from agent.llm.schemas import AnalysisResult, TradeSignal
from agent.storage.repository import DecisionRepository


def should_auto_execute(signal: TradeSignal, settings: Settings | None = None) -> bool:
    """P2.5 — only BUY/SELL proceed when LLM_AUTO_EXECUTE is enabled."""
    cfg = settings or get_settings()
    return cfg.llm_auto_execute and signal.is_actionable


def apply_aggressive_nudge(
    signal: TradeSignal,
    market_data: dict[str, Any],
    settings: Settings,
) -> TradeSignal:
    """PoC 激进模式：LLM 返回 HOLD 时，按余额兜底推动买卖闭环。"""
    if settings.trading_style != "aggressive" or signal.action != "HOLD":
        return signal

    balance = market_data.get("balance") or {}
    free = balance.get("free") or {}
    symbol = resolve_worker_symbol(market_data=market_data, settings=settings)
    base = base_asset_for_symbol(symbol)
    last = float(market_data.get("last") or 0)
    usdt = float(free.get("USDT") or 0)
    base_qty = float(free.get(base) or 0)
    min_usdt = settings.poc_min_trade_usdt
    max_usdt = settings.max_trade_amount

    base_notional = base_qty * last if last > 0 else 0.0
    if base_notional >= min_usdt and base_qty > 0:
        return TradeSignal(
            action="SELL",
            symbol=symbol,
            amount=base_qty,
            confidence=0.9,
            reason="激进兜底：持有底仓，卖出以完成 PoC 买卖闭环（模拟盘）",
        )

    if usdt >= min_usdt:
        buy_amount = min(max_usdt, usdt * 0.8)
        buy_amount = max(min_usdt, buy_amount)
        return TradeSignal(
            action="BUY",
            symbol=symbol,
            amount=round(buy_amount, 2),
            confidence=0.9,
            reason="激进兜底：主动买入以启动 PoC 买卖闭环（模拟盘）",
        )

    return signal


def cap_signal_amount(signal: TradeSignal, market_data: dict[str, Any], settings: Settings) -> TradeSignal:
    """激进 PoC：将下单量裁剪到 max_trade_amount 内，避免风控误拒。"""
    if signal.action not in {"BUY", "SELL"} or signal.amount is None:
        return signal
    last = float(market_data.get("last") or 0)
    max_usdt = settings.max_trade_amount
    if signal.action == "BUY":
        capped = min(float(signal.amount), max_usdt)
        if capped != signal.amount:
            return signal.model_copy(
                update={
                    "amount": round(capped, 2),
                    "reason": signal.reason + f"（裁剪至 {capped:.2f} USDT 上限）",
                }
            )
        return signal
    if last <= 0:
        return signal
    base = base_asset_for_symbol(resolve_worker_symbol(market_data=market_data, settings=settings))
    max_base = (max_usdt * 0.99) / last
    if float(signal.amount) > max_base:
        return signal.model_copy(
            update={
                "amount": round(max_base, 8),
                "reason": signal.reason + f"（裁剪至 {max_base:.8f} {base} 名义≤{max_usdt} USDT）",
            }
        )
    return signal


def correct_aggressive_action(
    signal: TradeSignal,
    market_data: dict[str, Any],
    settings: Settings,
) -> TradeSignal:
    """激进模式：无 BTC 时不应 SELL，有 USDT 时应优先 BUY。"""
    if settings.trading_style != "aggressive":
        return signal
    balance = market_data.get("balance") or {}
    free = balance.get("free") or {}
    symbol = resolve_worker_symbol(market_data=market_data, settings=settings)
    base = base_asset_for_symbol(symbol)
    usdt = float(free.get("USDT") or 0)
    base_qty = float(free.get(base) or 0)
    last = float(market_data.get("last") or 0)
    min_usdt = settings.poc_min_trade_usdt

    if signal.action == "SELL" and base_qty * last < min_usdt and usdt >= min_usdt:
        buy_amount = min(settings.max_trade_amount, usdt * 0.8)
        buy_amount = max(min_usdt, buy_amount)
        return TradeSignal(
            action="BUY",
            symbol=symbol,
            amount=round(buy_amount, 2),
            confidence=0.9,
            reason="激进修正：无可用底仓，改为买入启动闭环",
        )
    return signal


async def run_analysis_agent(
    market_data: dict[str, Any],
    *,
    settings: Settings | None = None,
    persist: bool = True,
) -> AnalysisResult:
    """
    Analysis Agent node: market snapshot → structured BUY/SELL/HOLD signal.
    Persists to decision_logs when persist=True.
    """
    cfg = settings or get_settings()
    analyzer = MarketAnalyzer(cfg)
    signal, raw_output, prompt_summary, usage = await analyzer.analyze(market_data)
    signal = apply_aggressive_nudge(signal, market_data, cfg)
    signal = correct_aggressive_action(signal, market_data, cfg)
    signal = cap_signal_amount(signal, market_data, cfg)
    auto_execute = should_auto_execute(signal, cfg)
    from agent.metrics import record_llm_call

    record_llm_call(provider=cfg.llm_provider, action=signal.action)

    decision_id: str | None = None
    if persist:
        decision_id = str(uuid.uuid4())
        repo = DecisionRepository()
        await repo.save(
            decision_id=decision_id,
            model_used=f"{cfg.llm_provider}:{cfg.llm_model}",
            prompt_summary=prompt_summary,
            raw_output=raw_output or signal.reason,
            parsed_signal=signal.to_dict(),
            prompt_tokens=(usage or {}).get("prompt_tokens"),
            completion_tokens=(usage or {}).get("completion_tokens"),
            total_tokens=(usage or {}).get("total_tokens"),
        )

    return AnalysisResult(
        signal=signal,
        raw_output=raw_output,
        model_used=f"{cfg.llm_provider}:{cfg.llm_model}",
        prompt_summary=prompt_summary,
        auto_execute=auto_execute,
        decision_id=decision_id,
        usage=usage,
    )


def apply_analysis_to_state(state: TradeState, result: AnalysisResult) -> TradeState:
    """Merge analysis output into LangGraph state (used by P3 supervisor)."""
    return {
        **state,
        "llm_signal": result.signal.to_dict(),
        "llm_auto_execute": result.auto_execute,
        "analysis_result": result.model_dump(mode="json"),
        "decision_id": result.decision_id,
    }
