"""破线判定与强制平仓计划的测试（纯逻辑，不出网、不碰库）。"""

from __future__ import annotations

import pytest

from agent.config import Settings
from agent.portfolio.valuation import Position
from agent.risk.monitor import plan_closes


def _s(**over):
    base = {"stop_loss_pct": 0.10, "max_drawdown_pct": 0.05, "trade_symbol": "BTCUSDT"}
    base.update(over)
    return Settings(**base)


def _pos(asset: str, qty: float, price: float | None, value: float | None):
    return Position(asset=asset, quantity=qty, price=price, value_quote=value)


# ---------------------------------------------------------------- 回撤清仓


def test_drawdown_breach_clears_everything():
    """账户级信号 → 清空全部非现金持仓（ADR-029）。"""
    result = plan_closes(
        positions=[
            _pos("USDT", 500.0, 1.0, 500.0),
            _pos("BTC", 0.001, 100000.0, 100.0),
            _pos("ETH", 0.05, 3000.0, 150.0),
        ],
        highs={},
        drawdown_pct=0.06,
        settings=_s(),
    )
    assert result.should_close is True
    assert {i.asset for i in result.instructions} == {"BTC", "ETH"}  # 现金不"平"
    assert all(i.rule == "drawdown" for i in result.instructions)
    assert all(i.symbol.endswith("USDT") for i in result.instructions)


def test_drawdown_breach_with_no_positions_is_not_an_instruction():
    """已经全是现金时不要凭空造一笔平仓单 —— 那会在交易所留下无意义的订单。"""
    result = plan_closes(
        positions=[_pos("USDT", 500.0, 1.0, 500.0)],
        highs={},
        drawdown_pct=0.9,
        settings=_s(),
    )
    assert result.should_close is False
    assert any("没有可平的持仓" in n for n in result.notes)


def test_drawdown_within_limit_closes_nothing():
    result = plan_closes(
        positions=[_pos("BTC", 0.001, 100000.0, 100.0)],
        highs={"BTC": 100000.0},
        drawdown_pct=0.01,
        settings=_s(),
    )
    assert result.should_close is False


def test_unknown_drawdown_is_noted_not_guessed():
    """算不出回撤时不能"当作没破线"悄悄过去 —— 要留下痕迹。"""
    result = plan_closes(
        positions=[_pos("BTC", 0.001, 100000.0, 100.0)],
        highs={"BTC": 100000.0},
        drawdown_pct=None,
        settings=_s(),
    )
    assert result.should_close is False
    assert any("算不出权益回撤" in n for n in result.notes)


# ---------------------------------------------------------------- 止损清仓


def test_stop_breach_closes_only_that_asset():
    result = plan_closes(
        positions=[
            _pos("BTC", 0.001, 50000.0, 50.0),  # 从 100000 跌到 50000，破线
            _pos("ETH", 0.05, 3000.0, 150.0),   # 没跌
        ],
        highs={"BTC": 100000.0, "ETH": 3000.0},
        drawdown_pct=0.0,
        settings=_s(),
    )
    assert result.should_close is True
    assert len(result.instructions) == 1
    ins = result.instructions[0]
    assert ins.asset == "BTC"
    assert ins.rule == "stop_loss"
    assert ins.quantity == pytest.approx(0.001)  # 基础币数量
    assert ins.symbol == "BTCUSDT"


def test_stop_breach_without_a_high_is_skipped_silently():
    """拿不到高点 → 判不了，不出现在结果里。这不是"没破线"。"""
    result = plan_closes(
        positions=[_pos("BTC", 0.001, 50000.0, 50.0)],
        highs={},
        drawdown_pct=0.0,
        settings=_s(),
    )
    assert result.should_close is False


def test_drawdown_wins_over_stop_when_both_fire():
    """两条都成立时按回撤处理 —— 它的集合更大，原因也是账户级的。"""
    result = plan_closes(
        positions=[
            _pos("BTC", 0.001, 50000.0, 50.0),
            _pos("ETH", 0.05, 3000.0, 150.0),
        ],
        highs={"BTC": 100000.0, "ETH": 3000.0},
        drawdown_pct=0.06,
        settings=_s(),
    )
    assert {i.asset for i in result.instructions} == {"BTC", "ETH"}
    assert all(i.rule == "drawdown" for i in result.instructions)


def test_zero_and_negative_quantities_are_never_closed():
    """数量为 0 平不了；为负不是持仓。都不该生成卖单。"""
    result = plan_closes(
        positions=[
            _pos("BTC", 0.0, 50000.0, 0.0),
            _pos("ETH", -1.0, 3000.0, -3000.0),
        ],
        highs={"BTC": 100000.0, "ETH": 9000.0},
        drawdown_pct=0.0,
        settings=_s(),
    )
    assert result.should_close is False


def test_result_is_serialisable_for_the_event_log():
    result = plan_closes(
        positions=[_pos("BTC", 0.001, 50000.0, 50.0)],
        highs={"BTC": 100000.0},
        drawdown_pct=0.0,
        settings=_s(),
    )
    payload = result.as_dict()
    assert payload["instructions"][0]["rule"] == "stop_loss"
    assert isinstance(payload["notes"], list)
