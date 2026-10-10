"""可归因记录 R1–R3（ADR-019 / ADR-031）。全程临时库，不出网。

这一组测的不是"代码里有几个字段"，而是那三句话能不能被答出来：

    R1 为什么         -> 完整 prompt（被怎么问的）+ 决策链路
    R2 当时看到了什么  -> 整份行情上下文，**不是摘要**
    R3 当时手里有什么  -> 决策时刻的权益与持仓

旧的失败方式很安静：`save_signal` 收了 `market_data` 却只用它取 `symbol` 与 `last`，
整份快照丢掉，而没有任何测试会因此变红。所以每条断言都对着"整份 / 那一刻"，
能靠摘要和事后重读蒙混过关的写法在这里应当失败。
"""

from __future__ import annotations

import json
import sqlite3

import httpx
import pytest

from agent.config import Settings
from agent.graph import analysis_agent, execute_agent
from agent.llm.analyzer import AnalysisRun, MarketAnalyzer
from agent.llm.schemas import TradeSignal
from agent.storage import database
from agent.storage.repository import (
    DecisionRepository,
    EquityRepository,
    TradeRepository,
    decision_context,
)

# 一份带 K 线的行情上下文。**刻意做得比 "symbol + last" 大得多** ——
# 断言"整份都存下了"才有意义。
MARKET = {
    "symbol": "BTCUSDT",
    "last": 83070.0,
    "bid": 83069.5,
    "ask": 83070.5,
    "high_24h": 84100.0,
    "low_24h": 81500.0,
    "candles_1h": [{"t": 1760200000000 - i * 3600000, "c": 83000.0 + i} for i in range(25)],
    "indicators": {"rsi14": 55.2, "ema20": 82800.0},
}


@pytest.fixture
async def db(tmp_path, monkeypatch):
    await database.close_db()
    url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/bianca.db"
    monkeypatch.setattr(database, "get_settings", lambda: Settings(database_url=url))
    # 对账轮询的等待纯属浪费时间：这里要验的是"记了什么"，不是"等多久"
    monkeypatch.setattr(execute_agent, "_RECONCILE_DELAY_S", 0.0)
    await database.init_db()
    yield
    await database.close_db()


async def _snapshot(*, equity=1000.0, positions=None, captured_at="2026-10-11T09:00:00+00:00"):
    rows = positions if positions is not None else [
        {"asset": "BTC", "quantity": 0.001, "price": 83070.0, "value_quote": 83.07}
    ]
    held = sum((p.get("value_quote") or 0.0) for p in rows)
    await EquityRepository().save(
        cash_quote=equity - held, holdings_value=held, equity=equity,
        positions=rows, unpriced=[], gaps=[], notes=[], captured_at=captured_at,
    )


def _signal(action="BUY", reason="测试信号"):
    return {"action": action, "symbol": "BTCUSDT", "amount": 10.0,
            "confidence": 0.8, "reason": reason}


# ---------------------------------------------------------------- R2：整份上下文


@pytest.mark.asyncio
async def test_save_signal_keeps_the_whole_market_context(db):
    """**A1 的回归**：以前只留 symbol 与 last，其余静默丢弃。"""
    await TradeRepository().save_signal(
        trade_id="t1", signal=_signal(), market_data=MARKET,
        status="signal_only", risk_decision="skipped",
    )

    stored = json.loads((await TradeRepository().get_by_id("t1")).context)["market_data"]
    assert stored == MARKET, "整份行情上下文必须与传入的一字不差"
    assert len(stored["candles_1h"]) == 25, "K 线一根都不能少"


@pytest.mark.asyncio
async def test_context_is_not_double_wrapped_when_also_passed_in(db):
    """`decision_context()` 的产物再交给 `save_signal` 时，`market_data` 只有一份。

    两条路都能给 market_data（参数一份、context 里一份）—— 合并若写成嵌套，
    事后取 `context["market_data"]["market_data"]` 才拿得到数据，没人会想到。
    """
    await TradeRepository().save_signal(
        trade_id="t1", signal=_signal(), market_data=MARKET, status="submitted",
        risk_decision="approved",
        context=decision_context(MARKET, equity=1000.0,
                                 positions=[{"asset": "BTC", "quantity": 0.001}]),
        equity=1000.0,
    )

    context = json.loads((await TradeRepository().get_by_id("t1")).context)
    assert set(context) == {"market_data", "portfolio"}
    assert context["market_data"] == MARKET
    assert context["portfolio"]["equity"] == 1000.0


@pytest.mark.asyncio
async def test_every_path_writes_the_same_context_shape(db):
    """HOLD、走完风控、强制平仓写出来的 `context` 必须是同一个形状 —— 否则事后要猜
    这一行是哪条路写的。"""
    await TradeRepository().save_signal(
        trade_id="hold", signal=_signal("HOLD"), market_data=MARKET,
        status="signal_only", risk_decision="skipped",
    )
    await TradeRepository().save_signal(
        trade_id="approved", signal=_signal(), market_data=MARKET, status="submitted",
        risk_decision="approved",
        context=decision_context(MARKET, equity=1000.0), equity=1000.0,
    )

    for trade_id in ("hold", "approved"):
        context = json.loads((await TradeRepository().get_by_id(trade_id)).context)
        assert set(context) <= {"market_data", "portfolio"}, trade_id
        assert context["market_data"]["symbol"] == "BTCUSDT", trade_id


def test_decision_context_omits_what_it_does_not_know():
    """不知道的**不写**，而不是写成 0 —— 0 与"不知道"在归因里是两件事。"""
    empty = decision_context(None)
    assert empty["market_data"] == {}
    assert "portfolio" not in empty, "权益未知时不该凭空长出 portfolio"

    partial = decision_context(None, equity=0.0)
    assert partial["portfolio"]["equity"] == 0.0, "权益真的是 0 时要写 0"
    assert "positions" not in partial["portfolio"]


# ---------------------------------------------------------------- R1：完整 prompt


class _StubAnalyzer:
    def __init__(self, run: AnalysisRun):
        self.run = run

    async def analyze(self, market_data):
        return self.run


def _analysis_run(variant="normal"):
    system = "你是交易分析器"
    if variant == "brevity":
        system += "\n\nThink very briefly, then output only the JSON."
    return AnalysisRun(
        signal=TradeSignal(action="BUY", symbol="BTCUSDT", amount=10.0,
                           confidence=0.8, reason="看多"),
        raw_output='{"action":"BUY"}',
        prompt_summary="BTCUSDT last=83070.0",
        usage={"prompt_tokens": 1200, "completion_tokens": 40, "total_tokens": 1240},
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": "这里是完整的行情与约束……"}],
        variant=variant,
    )


def _install_analyzer(monkeypatch, run):
    monkeypatch.setattr(analysis_agent, "MarketAnalyzer", lambda cfg: _StubAnalyzer(run))


@pytest.mark.asyncio
async def test_the_full_prompt_is_stored_not_just_the_summary(db, monkeypatch):
    """**A2/A6 的回归**：以前只有一行摘要，答不出"模型被怎么问的"。"""
    _install_analyzer(monkeypatch, _analysis_run())
    result = await analysis_agent.run_analysis_agent(MARKET)

    row = await DecisionRepository().get_by_id(result.decision_id)
    assert json.loads(row.prompt)[1]["content"] == "这里是完整的行情与约束……"
    assert row.prompt_summary == "BTCUSDT last=83070.0"  # 摘要还在，但它不是依据
    assert json.loads(row.market_data) == MARKET


@pytest.mark.asyncio
async def test_the_brevity_retry_is_recorded_as_such(db, monkeypatch):
    """重试那次是被催着简短作答的 —— 回答质量与首次不同，两条记录不能长得一样。"""
    _install_analyzer(monkeypatch, _analysis_run("brevity"))
    result = await analysis_agent.run_analysis_agent(MARKET)

    row = await DecisionRepository().get_by_id(result.decision_id)
    assert row.prompt_variant == "brevity"
    assert "Think very briefly" in json.loads(row.prompt)[0]["content"]


@pytest.mark.asyncio
async def test_a_timed_out_call_still_records_what_was_asked(db, monkeypatch):
    """**关键**：超时那一笔最需要事后看到"我们问了什么、等了多久"。

    这里打的是**真实的** `MarketAnalyzer.analyze`，不是替身 —— 失败路径要带回
    messages 这件事发生在真身里，用替身测等于什么都没测。
    """
    analyzer = MarketAnalyzer(Settings())

    async def _timeout(*args, **kwargs):
        raise httpx.TimeoutException("模拟超时")

    monkeypatch.setattr(analyzer, "_chat", _timeout)
    monkeypatch.setattr(analysis_agent, "MarketAnalyzer", lambda cfg: analyzer)

    result = await analysis_agent.run_analysis_agent(MARKET)

    assert result.signal.action == "HOLD"
    assert "超时" in result.signal.reason
    row = await DecisionRepository().get_by_id(result.decision_id)
    assert row.prompt is not None, "失败的调用同样要留下它问过什么"
    assert json.loads(row.prompt)[1]["content"], "问句本身不能是空的"
    assert row.prompt_variant == "normal"
    assert json.loads(row.market_data) == MARKET, "失败时行情上下文也要留"


# ---------------------------------------------------------------- R3：当时手里有什么


@pytest.mark.asyncio
async def test_risk_agent_records_the_context_it_judged_on(db, monkeypatch):
    """**关键**：记的必须是**裁决那一刻**那份 —— 由 `evaluate_detailed` 交出来，
    而不是事后重读一次库（重读的可能已经是另一份，且没人会报出这个偏差）。"""
    from agent.graph import risk_agent
    from agent.risk import engine as engine_mod

    await _snapshot(equity=1234.56)

    async def _fake_highs(assets, settings=None):
        return {a: 90000.0 for a in assets}

    monkeypatch.setattr(engine_mod, "fetch_recent_highs", _fake_highs)

    state = {"llm_signal": _signal(), "market_data": MARKET, "trade_log_id": "t9"}
    out = await risk_agent.run_risk_agent(state)

    row = await TradeRepository().get_by_id(out["trade_log_id"])
    assert row.equity == pytest.approx(1234.56)
    assert json.loads(row.positions)[0]["asset"] == "BTC"

    context = json.loads(row.context)
    assert context["portfolio"]["equity"] == pytest.approx(1234.56)
    assert context["portfolio"]["highs"]["BTC"] == 90000.0


@pytest.mark.asyncio
async def test_hold_ticks_also_record_what_they_saw(db):
    """HOLD 轮次正是日后复盘"为什么没动手"的地方，最容易被漏记。"""
    from agent.graph.supervisor import log_signal_only_node

    await _snapshot(equity=777.0)

    out = await log_signal_only_node(
        {"llm_signal": _signal("HOLD"), "market_data": MARKET}
    )

    row = await TradeRepository().get_by_id(out["trade_log_id"])
    assert row.equity == pytest.approx(777.0)
    assert json.loads(row.positions)[0]["asset"] == "BTC"
    assert json.loads(row.context)["market_data"]["symbol"] == "BTCUSDT"


@pytest.mark.asyncio
async def test_no_snapshot_records_unknown_rather_than_zero(db):
    """**关键**：读不到快照时权益是 `None`，不是 `0`。

    写 0 的话事后会读成"当时账户里一分钱都没有"，而真相是"当时不知道" ——
    这正是 ADR-019 要防的那类沉默的假记录。
    """
    from agent.graph.supervisor import log_signal_only_node

    out = await log_signal_only_node(
        {"llm_signal": _signal("HOLD"), "market_data": MARKET}
    )

    row = await TradeRepository().get_by_id(out["trade_log_id"])
    assert row.equity is None
    assert row.positions is None
    assert "portfolio" not in json.loads(row.context), "不知道就别写"


@pytest.mark.asyncio
async def test_force_close_records_the_risk_context_not_candles(db, monkeypatch):
    """强制平仓不看 K 线，它的"当时依据"是**风控输入**。R2/R3 同样要答得出，
    只是答案的形状不同 —— 不能因为这条路没有行情就整段留白。"""
    from agent.portfolio.valuation import Position
    from agent.risk import close as close_module
    from agent.risk.engine import PortfolioInputs
    from tests.test_force_close import _FILLED_SELL, FakeCCXT, _install

    async def _loader(settings, *, highs_provider=None):
        return PortfolioInputs(
            equity=930.0,
            positions=(Position(asset="BTC", quantity=0.004, price=50000.0,
                                value_quote=200.0),),
            highs={"BTC": 100000.0},
            drawdown_pct=0.07,
            peak_equity=1000.0,
        )

    monkeypatch.setattr(close_module, "load_portfolio_inputs", _loader)
    fake = FakeCCXT(_FILLED_SELL)
    _install(monkeypatch, fake)

    results = await close_module.force_close(settings=Settings(max_trade_amount=5.0))

    assert len(fake.created) == 1, "前提：这一轮真的平了一笔"
    row = await TradeRepository().get_by_id(results[0]["trade_log_id"])
    assert row.equity == pytest.approx(930.0)
    assert json.loads(row.positions)[0]["quantity"] == 0.004

    context = json.loads(row.context)
    assert context["portfolio"]["drawdown_pct"] == pytest.approx(0.07)
    assert context["portfolio"]["highs"]["BTC"] == 100000.0
    assert context["market_data"] == {"symbol": "BTCUSDT"}, "没有 K 线就如实为空"


# ---------------------------------------------------------------- 留位与迁移


@pytest.mark.asyncio
async def test_goal_and_plan_survive_a_real_tick(db, monkeypatch, tmp_path):
    """**留位不等于接通。** 端到端跑一轮真图，确认这两个 id 真的落到两张表里。

    为什么必须跑真图而不是直接调节点：LangGraph 只传递 Schema 里**声明过**的键，
    没声明的会被静默丢掉。直接调节点的话，`goal_id` 当然拿得到 —— 而实际运行中
    节点收到的是一个已经把该键扔掉的状态，`.get()` 恒为 None，列建好了却永远空着。
    这种"看着通了其实没通"只有真跑一遍才露出来。
    """
    from agent.graph import supervisor

    async def _snap():
        await _snapshot(equity=555.0)

    async def _no_patrol(settings):
        return []

    monkeypatch.setattr(supervisor, "_capture_equity_snapshot", _snap)
    monkeypatch.setattr(supervisor, "_force_close_if_needed", _no_patrol)
    # 检查点别写进仓库的 data/ —— 那一份是跑着的东西，不该被测试弄脏
    monkeypatch.setattr(Settings, "data_dir", property(lambda self: tmp_path))
    _install_analyzer(monkeypatch, AnalysisRun(
        signal=TradeSignal(action="HOLD", symbol="BTCUSDT", amount=None,
                           confidence=0.0, reason="观望"),
        raw_output='{"action":"HOLD"}', prompt_summary="summary",
        messages=[{"role": "user", "content": "行情"}], variant="normal",
    ))

    result = await supervisor.run_agent_tick(
        market_data=MARKET, settings=Settings(), goal_id="G-1", plan_id="P-1",
    )

    assert result["status"] == "signal_only", "前提：这一轮走的是 HOLD 那条路"
    trade = await TradeRepository().get_by_id(result["trade_log_id"])
    assert trade.goal_id == "G-1"
    assert trade.plan_id == "P-1"

    decision = await DecisionRepository().get_by_id(result["decision_id"])
    assert decision.goal_id == "G-1"
    assert decision.plan_id == "P-1"


@pytest.mark.asyncio
async def test_goal_and_plan_columns_exist_and_default_to_null(db):
    """R1 的链头要等阶段 C 有 goal 实体。列先留着，**可空**，不阻塞现在。"""
    await TradeRepository().save_signal(
        trade_id="t1", signal=_signal(), market_data=MARKET,
        status="signal_only", risk_decision="skipped",
    )

    row = await TradeRepository().get_by_id("t1")
    assert row.goal_id is None
    assert row.plan_id is None


_OLD_TRADE_LOGS = """
CREATE TABLE trade_logs (
    id VARCHAR PRIMARY KEY, symbol VARCHAR NOT NULL, side VARCHAR NOT NULL,
    quantity FLOAT, price FLOAT, order_type VARCHAR, llm_confidence FLOAT,
    decision_reason TEXT NOT NULL, risk_decision VARCHAR, risk_reason TEXT,
    external_order_id VARCHAR, decision_id VARCHAR, status VARCHAR NOT NULL,
    created_at VARCHAR NOT NULL
)
"""
_OLD_DECISION_LOGS = """
CREATE TABLE decision_logs (
    id VARCHAR PRIMARY KEY, model_used VARCHAR NOT NULL, prompt_summary TEXT,
    raw_output TEXT NOT NULL, parsed_signal TEXT NOT NULL,
    created_at VARCHAR NOT NULL
)
"""


@pytest.mark.asyncio
async def test_migration_adds_columns_to_a_db_with_existing_data(tmp_path, monkeypatch):
    """**线上库 `data/bianca.db` 已经有数据**，加列只能 ALTER TABLE。

    造一个"旧 schema + 有数据"的库，跑一次 init_db，验证列补上了、且数据没动。
    """
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute(_OLD_TRADE_LOGS)
    con.execute(_OLD_DECISION_LOGS)
    con.execute(
        "INSERT INTO trade_logs (id, symbol, side, decision_reason, status, created_at)"
        " VALUES ('old1','BTCUSDT','BUY','历史记录','filled','2026-01-01T00:00:00+00:00')"
    )
    con.commit()
    con.close()

    await database.close_db()
    monkeypatch.setattr(
        database, "get_settings",
        lambda: Settings(database_url=f"sqlite+aiosqlite:///{path.as_posix()}"),
    )
    await database.init_db()

    con = sqlite3.connect(path)
    trade_cols = {r[1] for r in con.execute("PRAGMA table_info(trade_logs)")}
    decision_cols = {r[1] for r in con.execute("PRAGMA table_info(decision_logs)")}
    rows = con.execute("SELECT id, symbol, decision_reason FROM trade_logs").fetchall()
    con.close()

    assert {"context", "equity", "positions", "goal_id", "plan_id"} <= trade_cols
    assert {"prompt", "prompt_variant", "market_data", "goal_id", "plan_id"} <= decision_cols
    assert rows == [("old1", "BTCUSDT", "历史记录")], "迁移绝不能动已有数据"


@pytest.mark.asyncio
async def test_init_db_is_idempotent(db):
    """应用每次启动都会跑它 —— 跑第二次不该炸，也不该重复加列。"""
    await database.init_db()
    await database.init_db()
