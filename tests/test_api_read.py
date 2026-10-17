"""阶段 F-1：只读端点的翻页、过滤与空值语义。

这些测试只读 API 的**契约**，不碰真实库：每一条都要求 `total` 与库里的
`COUNT(*)` 同源，且 `offset` 翻页不重不漏。前端控制台的"共 N 条"直接建立在
这个数上 —— 它一旦退化成"本页条数"，翻页就会骗人（ADR-F03）。
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from agent.config import Settings
from agent.main import app
from agent.market import storage as market_storage
from agent.market.repository import KlineRepository
from agent.storage import database
from agent.storage.database import close_db, get_session_factory, init_db
from agent.storage.models import (
    EquitySnapshotLog,
    MessageLog,
    OrderLog,
    PlanLog,
    RiskEvent,
    TradeLog,
)
from agent.storage.repository import GoalRepository, PlanRepository


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


async def _add(*rows: object) -> None:
    async with get_session_factory()() as db:
        db.add_all(list(rows))
        await db.commit()


@pytest.fixture(autouse=True)
async def _temp_dbs(tmp_path, monkeypatch):
    """业务库与行情库都用临时文件 —— 见 test_observability 的同一理由。"""
    await close_db()
    await market_storage.close_market_db()
    db_url = f"sqlite+aiosqlite:///{(tmp_path / 'bianca.db').as_posix()}"
    market_url = f"sqlite+aiosqlite:///{(tmp_path / 'market.db').as_posix()}"
    monkeypatch.setattr(database, "get_settings", lambda: Settings(database_url=db_url))
    monkeypatch.setattr(
        market_storage, "get_settings", lambda: Settings(market_database_url=market_url)
    )
    await init_db()
    await market_storage.init_market_db()
    yield
    await close_db()
    await market_storage.close_market_db()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _trade(symbol: str, created_at: str, *, side: str = "BUY", **extra) -> TradeLog:
    return TradeLog(
        id=extra.pop("id", _uid("trd")),
        symbol=symbol,
        side=side,
        decision_reason="test",
        status=extra.pop("status", "filled"),
        created_at=created_at,
        **extra,
    )


# ---------------------------------------------------------------- trades


@pytest.mark.asyncio
async def test_trades_total_is_db_count_and_offset_is_stable(client):
    sym = f"PG{uuid.uuid4().hex[:8].upper()}"
    rows = [_trade(sym, f"2026-10-01T00:0{i}:00+00:00") for i in range(5)]
    rows.append(_trade("OTHERUSDT", "2026-10-01T00:09:00+00:00"))
    await _add(*rows)

    seen: list[str] = []
    totals: set[int] = set()
    for offset in (0, 2, 4):
        resp = await client.get(
            "/api/v1/trades", params={"symbol": sym, "limit": 2, "offset": offset}
        )
        assert resp.status_code == 200
        body = resp.json()
        totals.add(body["total"])
        seen.extend(item["id"] for item in body["items"])

    # total 与筛选条件同源，翻到第几页都不变
    assert totals == {5}
    # 不重不漏：5 条各出现一次；干扰 symbol 不混进来
    assert len(seen) == len(set(seen)) == 5
    assert set(seen) == {row.id for row in rows[:5]}


@pytest.mark.asyncio
async def test_trades_filter_goal_and_until(client):
    goal_a, goal_b = _uid("goal"), _uid("goal")
    early = _trade("FLTUSDT", "2026-10-01T00:00:00+00:00", goal_id=goal_a)
    late = _trade("FLTUSDT", "2026-10-02T00:00:00+00:00", goal_id=goal_b)
    await _add(early, late)

    resp = await client.get("/api/v1/trades", params={"goal_id": goal_a})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == early.id

    resp = await client.get(
        "/api/v1/trades", params={"symbol": "FLTUSDT", "until": "2026-10-01T12:00:00+00:00"}
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == early.id


@pytest.mark.asyncio
async def test_trade_detail_keeps_attribution_fields(client):
    trade_id = _uid("trd")
    await _add(
        _trade(
            "DETAILUSDT",
            "2026-10-01T00:00:00+00:00",
            id=trade_id,
            goal_id="g-1",
            plan_id="p-1",
            context='{"market_data": {"last": 100.0}, "portfolio": {"equity": 1000.0}}',
            equity=1000.0,
            positions='[{"asset": "USDT", "quantity": 1000.0}]',
        )
    )

    resp = await client.get(f"/api/v1/trades/{trade_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["goal_id"] == "g-1"
    assert body["plan_id"] == "p-1"
    assert body["equity"] == 1000.0
    assert body["context"]["market_data"]["last"] == 100.0
    assert body["positions"][0]["asset"] == "USDT"

    missing = await client.get("/api/v1/trades/does-not-exist")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_page_bounds_are_rejected(client):
    assert (await client.get("/api/v1/trades", params={"limit": 0})).status_code == 422
    assert (await client.get("/api/v1/trades", params={"offset": -1})).status_code == 422
    assert (await client.get("/api/v1/equity/history", params={"limit": 0})).status_code == 422
    assert (await client.get("/api/v1/market/klines", params={"symbol": "BTCUSDT", "limit": 0})).status_code == 422


# ---------------------------------------------------------------- orders


@pytest.mark.asyncio
async def test_orders_list_and_detail(client):
    ids = []
    for i in range(3):
        order_id = _uid("ord")
        ids.append(order_id)
        await _add(
            OrderLog(
                id=order_id,
                client_order_id=_uid("cli"),
                symbol="ORDUSDT",
                side="SELL" if i == 1 else "BUY",
                order_type="MARKET",
                amount_kind="quote",
                requested_amount=10.0 + i,
                status="filled",
                fee="0.00000123 BTC" if i == 0 else None,
                raw_response='{"orderId": 1}' if i == 0 else None,
                created_at=f"2026-10-01T00:0{i}:00+00:00",
                updated_at=f"2026-10-01T00:0{i}:00+00:00",
            )
        )

    resp = await client.get("/api/v1/orders", params={"symbol": "ORDUSDT", "limit": 2})
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["items"][0]["id"] == ids[2]  # 最新在前

    resp = await client.get("/api/v1/orders", params={"side": "sell"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["side"] == "SELL"

    resp = await client.get(f"/api/v1/orders/{ids[0]}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["fee"] == "0.00000123 BTC"
    assert body["raw_response"] == '{"orderId": 1}'

    assert (await client.get("/api/v1/orders/nope")).status_code == 404


# ---------------------------------------------------------------- risk / equity


@pytest.mark.asyncio
async def test_risk_events_filter_and_total(client):
    for i, event_type in enumerate(["stop_loss", "drawdown", "stop_loss"]):
        await _add(
            RiskEvent(
                id=_uid("risk"),
                event_type=event_type,
                detail='{"why": "test"}',
                created_at=f"2026-10-01T00:0{i}:00+00:00",
            )
        )

    resp = await client.get("/api/v1/risk/events", params={"event_type": "stop_loss"})
    body = resp.json()
    assert body["total"] == 2
    assert all(item["event_type"] == "stop_loss" for item in body["items"])
    assert body["items"][0]["detail"] == {"why": "test"}


@pytest.mark.asyncio
async def test_equity_history_is_ascending_and_counts_all(client):
    for i, equity in enumerate([900.0, 950.0, 1000.0]):
        await _add(
            EquitySnapshotLog(
                id=_uid("eq"),
                captured_at=f"2026-10-01T00:0{i}:00+00:00",
                cash_quote=equity,
                holdings_value=0.0,
                equity=equity,
                unpriced="[]",
                positions="[]",
                gaps="[]",
                notes="[]",
            )
        )

    resp = await client.get("/api/v1/equity/history", params={"limit": 2})
    body = resp.json()
    # 取最近 2 条，仍然按时间正序 —— 曲线从左到右
    assert [p["equity"] for p in body["items"]] == [950.0, 1000.0]
    assert body["total"] == 3

    resp = await client.get(
        "/api/v1/equity/history", params={"since": "2026-10-01T00:01:00+00:00"}
    )
    body = resp.json()
    assert body["total"] == 2
    assert [p["equity"] for p in body["items"]] == [950.0, 1000.0]

    # 空库之外的空条件：没有任何快照时是空列表，不是编一个 0
    resp = await client.get(
        "/api/v1/equity/history", params={"until": "2025-01-01T00:00:00+00:00"}
    )
    assert resp.json() == {"items": [], "total": 0}


# ---------------------------------------------------------------- market klines


@pytest.mark.asyncio
async def test_market_klines_ascending_end_and_total(client):
    t0 = 1_790_000_000_000
    rows = [
        {
            "time": t0 + i * 60_000,
            "symbol": "BTCUSDT",
            "interval": "1m",
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.0 + i,
            "close": 100.5 + i,
            "volume": 1.0,
            "quote_volume": 100.0,
            "trades": 10,
            "taker_buy_base": 0.5,
            "taker_buy_quote": 50.0,
        }
        for i in range(5)
    ]
    assert await KlineRepository().insert_rows(rows) == 5

    resp = await client.get(
        "/api/v1/market/klines", params={"symbol": "btcusdt", "interval": "1m", "limit": 3}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "BTCUSDT"
    assert body["source"] == "market.db"
    assert body["total"] == 5
    assert [bar["time"] for bar in body["items"]] == [t0 + 2 * 60_000, t0 + 3 * 60_000, t0 + 4 * 60_000]

    resp = await client.get(
        "/api/v1/market/klines",
        params={"symbol": "BTCUSDT", "interval": "1m", "limit": 10, "end": t0 + 60_000},
    )
    body = resp.json()
    assert [bar["time"] for bar in body["items"]] == [t0, t0 + 60_000]
    assert body["total"] == 5  # total 不受 end 影响：它是整个序列的行数


# ---------------------------------------------------------------- goals / messages / plans


@pytest.mark.asyncio
async def test_goals_messages_and_plans(client):
    goal_id = await _goal()

    for i, (role, content) in enumerate(
        [("user", "开始"), ("assistant", "好的"), ("user", "确认")]
    ):
        await _add(
            MessageLog(
                id=_uid("msg"),
                goal_id=goal_id,
                role=role,
                content=content,
                created_at=f"2026-10-01T00:0{i}:00+00:00",
            )
        )

    ok_plan = _uid("plan")
    await _add(
        PlanLog(
            id=ok_plan,
            goal_id=goal_id,
            plan='{"steps": []}',
            plan_shape="single",
            final_text="解出来了",
            transcript='[{"role": "assistant", "content": "x"}]',
            invocations="[]",
            shapes='["single"]',
            usage='{"total_tokens": 10}',
            errors="[]",
            rounds=1,
            created_at="2026-10-01T00:00:30+00:00",
        ),
        PlanLog(
            id=_uid("plan"),
            goal_id=goal_id,
            plan=None,  # 解不出来也是事实，必须原样返回
            plan_shape="none",
            final_text="没解出来",
            transcript="[]",
            invocations="[]",
            shapes='["none"]',
            usage=None,
            errors='["bad shape"]',
            rounds=2,
            created_at="2026-10-01T00:01:30+00:00",
        ),
    )

    resp = await client.get("/api/v1/goals")
    body = resp.json()
    assert body["total"] == 1
    goal = body["items"][0]
    assert goal["id"] == goal_id
    # 「用户没说」如实是 null，不是 0
    assert goal["target_return_pct"] is None
    assert goal["max_drawdown_pct"] is None

    assert (await client.get(f"/api/v1/goals/{goal_id}")).json()["raw_text"] == "目标：测试"
    assert (await client.get("/api/v1/goals/nope")).status_code == 404

    resp = await client.get(f"/api/v1/goals/{goal_id}/messages")
    body = resp.json()
    assert body["total"] == 3
    assert [m["content"] for m in body["items"]] == ["开始", "好的", "确认"]

    resp = await client.get(f"/api/v1/goals/{goal_id}/plans")
    body = resp.json()
    assert body["total"] == 2
    assert body["items"][0]["plan"] is None  # 最新的那次失败
    assert body["items"][0]["errors"] == ["bad shape"]
    assert body["items"][1]["plan"] == {"steps": []}

    # 列表不返回 transcript；详情默认也不返回，显式要求才给
    resp = await client.get(f"/api/v1/plans/{ok_plan}")
    assert resp.json()["transcript"] is None
    resp = await client.get(f"/api/v1/plans/{ok_plan}", params={"include_transcript": "true"})
    assert resp.json()["transcript"] == [{"role": "assistant", "content": "x"}]
    assert (await client.get("/api/v1/plans/nope")).status_code == 404


async def _goal() -> str:
    row = await GoalRepository().create(raw_text="目标：测试", parsed={"objective": "测试"})
    return row.id
