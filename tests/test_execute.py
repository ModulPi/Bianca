"""执行路径测试：订单账本、成交对账、幂等重放。全程不出网（假的 ccxt）。

**这条路径此前零覆盖**，而它是唯一真的会花钱的路径。旧实现
`order.get("filled") or amount` 会把 0 成交记成 filled —— 那类谎报不会有任何测试变红，
所以这里的重点是"交易所没说的，我们不编"。
"""

from __future__ import annotations

import ccxt
import pytest
from httpx import ASGITransport, AsyncClient

from agent.config import Settings
from agent.graph import execute_agent
from agent.storage import database
from agent.storage.repository import AgentConfigRepository, OrderRepository, TradeRepository
from agent.trading.orders import new_client_order_id


@pytest.fixture
async def db(tmp_path, monkeypatch):
    """临时库文件。engine/session 是模块级单例，先清掉上一轮可能残留的。"""
    await database.close_db()
    url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/bianca.db"
    monkeypatch.setattr(database, "get_settings", lambda: Settings(database_url=url))
    # 对账轮询不为测试真实等待
    monkeypatch.setattr(execute_agent, "_RECONCILE_DELAY_S", 0.0)
    await database.init_db()
    yield
    await database.close_db()


class FakeCCXT:
    """假 ccxt：只实现执行路径用到的两个方法。

    `fetch_order` 有两种问法，语义完全不同，必须分开：
      · `origClientOrderId=...` —— 提交前回查"这个幂等键你见过吗"
      · 按交易所单号 —— 提交后对账
    """

    def __init__(
        self,
        *,
        order=None,
        order_error=None,
        fetch=None,
        fetch_error=None,
        by_cid=None,
        probe_error=None,
    ):
        self.created: list[dict] = []
        self.fetched: list[str] = []  # 按单号查的
        self.probed: list[str] = []  # 按 cid 查的
        self._order = order
        self._order_error = order_error
        self._fetch = fetch
        self._fetch_error = fetch_error
        self._by_cid = dict(by_cid or {})
        self._probe_error = probe_error

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.created.append(
            {"symbol": symbol, "type": type_, "side": side, "amount": amount, "params": params or {}}
        )
        if self._order_error is not None:
            raise self._order_error
        return self._order

    async def fetch_order(self, order_id, symbol=None, params=None):
        cid = (params or {}).get("origClientOrderId")
        if cid is not None:
            self.probed.append(cid)
            if self._probe_error is not None:
                raise self._probe_error
            if cid not in self._by_cid:
                # 交易所不认得这个 cid —— 正常结果，不是错误
                raise ccxt.OrderNotFound("order does not exist")
            return self._by_cid[cid]

        self.fetched.append(order_id)
        if self._fetch_error is not None:
            raise self._fetch_error
        return self._fetch if self._fetch is not None else self._order

    async def close(self):
        return None


def _install(monkeypatch, fake: FakeCCXT) -> None:
    class FakeDemo:
        def __init__(self, settings=None):
            self._exchange = fake

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        @property
        def exchange(self):
            return self._exchange

        async def fetch_order(self, order_id, symbol=None):
            return await fake.fetch_order(order_id, symbol)

    monkeypatch.setattr(execute_agent, "SpotDemoExchange", FakeDemo)


def _state(*, action="BUY", amount=25.0, trade_id="t-1", decision_id="d-1"):
    return {
        "llm_signal": {
            "action": action,
            "symbol": "BTCUSDT",
            "amount": amount,
            "confidence": 0.8,
            "reason": "测试信号",
        },
        "market_data": {"symbol": "BTCUSDT", "last": 65000.0},
        "trade_log_id": trade_id,
        "decision_id": decision_id,
        "llm_auto_execute": True,
    }


async def _seed_trade(trade_id: str, *, side="BUY") -> None:
    await TradeRepository().save_signal(
        trade_id=trade_id,
        signal={"action": side, "symbol": "BTCUSDT", "confidence": 0.8, "reason": "测试信号"},
        market_data={"symbol": "BTCUSDT", "last": 65000.0},
        status="submitted",
        risk_decision="approved",
        decision_id="d-1",
    )


_FILLED_ORDER = {
    "id": 9911,
    "status": "closed",
    "filled": 0.000384,
    "average": 65000.0,
    "cost": 25.0,
}


# ---------------------------------------------------------------- 正常成交


@pytest.mark.asyncio
async def test_market_buy_records_real_fill(db, monkeypatch):
    fake = FakeCCXT(order=_FILLED_ORDER)
    _install(monkeypatch, fake)
    await _seed_trade("t-1")

    result = await execute_agent.run_execute_agent(_state())

    assert result["status"] == "filled"
    row = await OrderRepository().find_by_client_id(new_client_order_id("t-1"))
    assert row is not None
    assert row.status == "filled"
    assert row.exchange_order_id == "9911"
    assert row.filled_qty == 0.000384
    assert row.avg_price == 65000.0
    assert row.cost_quote == 25.0
    assert fake.fetched == []  # 回执已是终态，不必再问


@pytest.mark.asyncio
async def test_trade_log_follows_order_status(db, monkeypatch):
    _install(monkeypatch, FakeCCXT(order=_FILLED_ORDER))
    await _seed_trade("t-1")

    await execute_agent.run_execute_agent(_state())

    trade = await TradeRepository().get_by_id("t-1")
    assert trade.status == "filled"
    assert trade.external_order_id == "9911"
    assert trade.quantity == 0.000384
    assert trade.price == 65000.0


@pytest.mark.asyncio
async def test_buy_uses_quote_order_qty(db, monkeypatch):
    """BUY 的量纲是计价币名义，且这个口径要落库。"""
    fake = FakeCCXT(order=_FILLED_ORDER)
    _install(monkeypatch, fake)
    await _seed_trade("t-1")

    await execute_agent.run_execute_agent(_state(action="BUY", amount=25.0))

    sent = fake.created[0]
    assert sent["side"] == "buy"
    assert sent["amount"] is None
    assert sent["params"]["quoteOrderQty"] == 25.0
    row = await OrderRepository().find_by_client_id(new_client_order_id("t-1"))
    assert row.amount_kind == "quote"
    assert row.requested_amount == 25.0


@pytest.mark.asyncio
async def test_sell_uses_base_quantity(db, monkeypatch):
    fake = FakeCCXT(
        order={"id": 7, "status": "closed", "filled": 0.0005, "average": 65000.0, "cost": 32.5}
    )
    _install(monkeypatch, fake)
    await _seed_trade("t-1", side="SELL")

    await execute_agent.run_execute_agent(_state(action="SELL", amount=0.0005))

    sent = fake.created[0]
    assert sent["side"] == "sell"
    assert sent["amount"] == 0.0005
    assert "quoteOrderQty" not in sent["params"]
    row = await OrderRepository().find_by_client_id(new_client_order_id("t-1"))
    assert row.amount_kind == "base"


@pytest.mark.asyncio
async def test_client_order_id_is_sent_to_exchange(db, monkeypatch):
    """幂等键必须真的发给交易所，否则交易所那边的去重不存在。"""
    fake = FakeCCXT(order=_FILLED_ORDER)
    _install(monkeypatch, fake)
    await _seed_trade("t-1")

    await execute_agent.run_execute_agent(_state())

    assert fake.created[0]["params"]["newClientOrderId"] == new_client_order_id("t-1")


# ---------------------------------------------------------------- 谎报回归


@pytest.mark.asyncio
async def test_zero_fill_order_is_unknown_never_filled(db, monkeypatch):
    """**回归**：0 成交的单不许记成 filled。

    交易所持续回 `open / filled=0`，对账问到耗尽 —— 结论只能是 unknown。
    旧实现会在这里写下一笔 `filled`（拿请求量兜底）。
    """
    fake = FakeCCXT(
        order={"id": 5, "status": "open", "filled": 0.0},
    )
    _install(monkeypatch, fake)
    await _seed_trade("t-1")

    result = await execute_agent.run_execute_agent(_state())

    assert result["status"] == "unknown"
    row = await OrderRepository().find_by_client_id(new_client_order_id("t-1"))
    assert row.status == "unknown"
    assert row.filled_qty == 0.0
    trade = await TradeRepository().get_by_id("t-1")
    assert trade.status != "filled"


@pytest.mark.asyncio
async def test_partial_fill_is_recorded_as_partial(db, monkeypatch):
    fake = FakeCCXT(order={"id": 5, "status": "open", "filled": 0.0001, "average": 65000.0})
    _install(monkeypatch, fake)
    await _seed_trade("t-1")

    result = await execute_agent.run_execute_agent(_state())

    assert result["status"] == "partial"
    row = await OrderRepository().find_by_client_id(new_client_order_id("t-1"))
    assert row.status == "partial"
    assert row.filled_qty == 0.0001


@pytest.mark.asyncio
async def test_reconcile_polls_until_the_exchange_says_closed(db, monkeypatch):
    responses = [
        {"id": 5, "status": "open", "filled": 0.0},
        {"id": 5, "status": "closed", "filled": 0.0004, "average": 65000.0, "cost": 26.0},
    ]
    fake = FakeCCXT(order=responses[0])
    _install(monkeypatch, fake)
    fake._fetch = responses[1]
    await _seed_trade("t-1")

    result = await execute_agent.run_execute_agent(_state())

    assert result["status"] == "filled"
    assert fake.fetched == ["5"]


# ---------------------------------------------------------------- 失败的两条路


@pytest.mark.asyncio
async def test_exchange_rejection_is_rejected(db, monkeypatch):
    """明确拒绝 → rejected，且**不去对账**（没单可对）。"""
    fake = FakeCCXT(order_error=ccxt.InsufficientFunds("insufficient balance"))
    _install(monkeypatch, fake)
    await _seed_trade("t-1")

    result = await execute_agent.run_execute_agent(_state())

    assert result["status"] == "failed"
    assert fake.fetched == []
    row = await OrderRepository().find_by_client_id(new_client_order_id("t-1"))
    assert row.status == "rejected"
    assert "insufficient" in row.error


@pytest.mark.asyncio
async def test_network_error_is_unknown_not_rejected(db, monkeypatch):
    """**关键**：提交超时时交易所可能已经收单了。

    记成 rejected 会让人以为"没下出去"，从而放心重放 → 重复下单。
    这里必须是 unknown。
    """
    fake = FakeCCXT(order_error=ccxt.RequestTimeout("timed out"))
    _install(monkeypatch, fake)
    await _seed_trade("t-1")

    result = await execute_agent.run_execute_agent(_state())

    assert result["status"] == "unknown"
    row = await OrderRepository().find_by_client_id(new_client_order_id("t-1"))
    assert row.status == "unknown"


@pytest.mark.asyncio
async def test_no_market_data_does_not_affect_order_recording(db, monkeypatch):
    """市场快照缺失不应影响下单（旧实现拿它取兜底价）。"""
    _install(monkeypatch, FakeCCXT(order=_FILLED_ORDER))
    await _seed_trade("t-1")
    state = _state()
    state["market_data"] = {}

    result = await execute_agent.run_execute_agent(state)

    assert result["status"] == "filled"
    row = await OrderRepository().find_by_client_id(new_client_order_id("t-1"))
    assert row.avg_price == 65000.0  # 来自交易所回执，不是行情快照


# ---------------------------------------------------------------- 幂等


@pytest.mark.asyncio
async def test_replay_does_not_submit_a_second_order(db, monkeypatch):
    """同一决策跑两次，交易所只能看到一笔单。"""
    fake = FakeCCXT(order=_FILLED_ORDER)
    _install(monkeypatch, fake)
    await _seed_trade("t-1")

    first = await execute_agent.run_execute_agent(_state())
    second = await execute_agent.run_execute_agent(_state())

    assert len(fake.created) == 1
    assert first["status"] == "filled"
    assert second["status"] == "filled"
    assert second["order_result"]["replayed"] is True


async def _seed_orphan_pending(trade_id: str, *, order_id: str = "orphan") -> None:
    """模拟"崩在提交与落库之间"：本地有一行 pending、且没有交易所单号。"""
    await OrderRepository().create_pending(
        order_id=order_id,
        client_order_id=new_client_order_id(trade_id),
        symbol="BTCUSDT",
        side="buy",
        order_type="market",
        amount_kind="quote",
        requested_amount=25.0,
        trade_id=trade_id,
    )


@pytest.mark.asyncio
async def test_pending_order_unknown_to_exchange_is_safe_to_submit(db, monkeypatch):
    """本地停在 pending，但**交易所说没见过这个 cid** —— 那就确实没下出去。

    旧实现遇到这行会无条件标记 unknown 并永久拒绝重放（因为当时无从查证）。
    现在能查了：查不到就等于没提交，可以放心下单。
    """
    fake = FakeCCXT(order=_FILLED_ORDER)  # by_cid 为空 → 回查得到 OrderNotFound
    _install(monkeypatch, fake)
    await _seed_trade("t-1")
    await _seed_orphan_pending("t-1")

    result = await execute_agent.run_execute_agent(_state())

    assert result["status"] == "filled"
    assert len(fake.created) == 1
    assert fake.probed == [new_client_order_id("t-1")]


@pytest.mark.asyncio
async def test_pending_order_known_to_exchange_is_adopted_not_resubmitted(db, monkeypatch):
    """本地停在 pending，交易所**认得**这个 cid —— 上次其实下出去了。

    必须认领它，绝不能再下一笔。这修的是旧实现在这个窗口里"放弃一笔已成交仓位"。
    """
    cid = new_client_order_id("t-1")
    fake = FakeCCXT(
        order=_FILLED_ORDER,
        by_cid={cid: {"id": 9911, "status": "closed", "filled": 0.000384,
                      "average": 65000.0, "cost": 25.0}},
    )
    _install(monkeypatch, fake)
    await _seed_trade("t-1")
    await _seed_orphan_pending("t-1")

    result = await execute_agent.run_execute_agent(_state())

    assert fake.created == []  # 没有第二次提交
    assert result["status"] == "filled"
    row = await OrderRepository().find_by_client_id(cid)
    assert row.id == "orphan"  # 复用了原来那行，没有另起一笔
    assert row.exchange_order_id == "9911"
    assert row.filled_qty == 0.000384


@pytest.mark.asyncio
async def test_probe_failure_refuses_to_submit(db, monkeypatch):
    """回查失败就不能下 —— 交易所侧没有去重，盲下的代价是重复建仓。

    但**不落终态**：这一轮不下不等于这笔单作废，下一轮还要再试。
    """
    fake = FakeCCXT(order=_FILLED_ORDER, probe_error=ccxt.RequestTimeout("probe timed out"))
    _install(monkeypatch, fake)
    await _seed_trade("t-1")

    result = await execute_agent.run_execute_agent(_state())

    assert fake.created == []
    assert result["status"] == "failed"
    row = await OrderRepository().find_by_client_id(new_client_order_id("t-1"))
    assert row.status == "pending"  # 仍可重试，不是终态
    assert "回查失败" in row.error


@pytest.mark.asyncio
async def test_order_known_only_to_exchange_is_recovered(db, monkeypatch):
    """本地**完全没有**这条记录，交易所却认得这个 cid（DB 丢过 / 被重建）。

    这也是本地账本单独挡不住的情形 —— 必须靠回查兜住。
    """
    cid = new_client_order_id("t-1")
    fake = FakeCCXT(
        order=_FILLED_ORDER,
        by_cid={cid: {"id": 9911, "status": "closed", "filled": 0.000384,
                      "average": 65000.0, "cost": 25.0}},
    )
    _install(monkeypatch, fake)
    await _seed_trade("t-1")

    result = await execute_agent.run_execute_agent(_state())

    assert fake.created == []
    assert result["status"] == "filled"
    row = await OrderRepository().find_by_client_id(cid)
    assert row is not None
    assert row.exchange_order_id == "9911"


@pytest.mark.asyncio
async def test_every_submission_is_preceded_by_a_probe(db, monkeypatch):
    """没有回查就等于没有幂等 —— 这条钉住"回查不是可选项"。"""
    fake = FakeCCXT(order=_FILLED_ORDER)
    _install(monkeypatch, fake)
    await _seed_trade("t-1")

    await execute_agent.run_execute_agent(_state())

    assert len(fake.probed) == 1
    assert len(fake.created) == 1
    assert fake.probed[0] == fake.created[0]["params"]["newClientOrderId"]


# ---------------------------------------------------------------- 不进执行路径


@pytest.mark.asyncio
async def test_hold_is_skipped_without_touching_the_exchange(db, monkeypatch):
    fake = FakeCCXT(order=_FILLED_ORDER)
    _install(monkeypatch, fake)

    result = await execute_agent.run_execute_agent(_state(action="HOLD", amount=None))

    assert result["status"] == "skipped"
    assert fake.created == []


@pytest.mark.asyncio
async def test_missing_amount_is_skipped(db, monkeypatch):
    fake = FakeCCXT(order=_FILLED_ORDER)
    _install(monkeypatch, fake)

    result = await execute_agent.run_execute_agent(_state(action="BUY", amount=None))

    assert result["status"] == "skipped"
    assert fake.created == []


# ---------------------------------------------------------------- 日亏不再在这里记


@pytest.mark.asyncio
async def test_a_zero_fill_does_not_move_any_money_counter(db, monkeypatch):
    """下单不再往任何"今天亏了多少"的计数器里写字。

    旧的现金流累加器（买入即记亏损）已删除，日盈亏改由权益快照算
    （`agent/portfolio/pnl.py`）。这里守住的是**这条路径不再碰钱**：
    成交与否只影响订单账本，账本的 `cost_quote` 才是事实来源。
    0 成交 → cost_quote 为 0，没有任何地方把请求名义当成花掉的钱。
    """
    fake = FakeCCXT(order={"id": 5, "status": "open", "filled": 0.0})
    _install(monkeypatch, fake)
    await _seed_trade("t-1")

    await execute_agent.run_execute_agent(_state(amount=25.0))

    row = await OrderRepository().list_recent(limit=1)
    assert row[0].cost_quote in (0.0, None)
    # 旧累加器不许复活：它在新的一天无条件返回 0.0，会被读成"今天没亏"，
    # 熔断静默失效 —— 那正是它被删掉的理由。
    assert not hasattr(AgentConfigRepository(), "get_daily_pnl")


# ---------------------------------------------------------------- 可观测


@pytest.mark.asyncio
async def test_orders_endpoint_exposes_the_ledger(db, monkeypatch):
    from agent.main import app

    _install(monkeypatch, FakeCCXT(order=_FILLED_ORDER))
    await _seed_trade("t-1")
    await execute_agent.run_execute_agent(_state())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/orders")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["status"] == "filled"
    assert item["amount_kind"] == "quote"
    assert item["client_order_id"] == new_client_order_id("t-1")
