from agent.strategy.dca import evaluate_dca
from agent.strategy.grid import evaluate_grid
from agent.strategy.trend import evaluate_trend


def test_grid_buy_on_level_drop():
    market = {"last": 64000.0, "balance": {"free": {"USDT": 1000, "BTC": 0}}}
    params = {"lower_price": 60000, "upper_price": 70000, "grid_count": 10, "amount_per_grid": 10}
    state = {"grid_level": 5}
    r = evaluate_grid(params=params, state=state, market_data=market, symbol="BTCUSDT")
    assert r.signal.action == "BUY"
    assert r.state["grid_level"] == 4


def test_dca_buy_after_interval():
    market = {"last": 65000.0, "balance": {"free": {"USDT": 100, "BTC": 0}}}
    params = {"interval_minutes": 60, "buy_amount_usdt": 10}
    state = {}
    r = evaluate_dca(params=params, state=state, market_data=market, symbol="BTCUSDT")
    assert r.signal.action == "BUY"
    assert "last_buy_at" in r.state


def test_trend_hold_insufficient_samples():
    market = {"last": 65000.0, "balance": {"free": {"USDT": 100, "BTC": 0.01}}}
    params = {"fast_period": 5, "slow_period": 20, "trade_amount_usdt": 10}
    state = {}
    r = evaluate_trend(params=params, state=state, market_data=market, symbol="BTCUSDT")
    assert r.signal.action == "HOLD"


def test_trend_uses_klines_5m_closes():
    closes = [float(64000 + i * 10) for i in range(25)]
    market = {
        "last": closes[-1],
        "klines_5m_closes": closes,
        "balance": {"free": {"USDT": 1000, "BTC": 0}},
    }
    params = {"fast_period": 5, "slow_period": 20, "trade_amount_usdt": 10}
    r = evaluate_trend(params=params, state={}, market_data=market, symbol="BTCUSDT")
    assert r.signal.action in {"BUY", "HOLD", "SELL"}
    assert len(r.state.get("prices") or []) >= 20
