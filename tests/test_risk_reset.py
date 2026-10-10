"""回撤重置的两个入口（ADR-030）。全程用临时库，不碰 data/bianca.db。

重置是**唯一能让回撤线松开**的操作，所以这里盯的不是"能跑通"，而是三件事：
  ① 没有理由就拒（不留痕的重置 = 静默解除风控）；
  ② 一定留痕，且痕里记的是**重置前**的账（只有事后状态看不出削掉了多少保护）；
  ③ 两个入口给的是同一笔账 —— 不一致比少一个入口更糟。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from agent.api.routes import router
from agent.config import Settings
from agent.portfolio.pnl import drawdown, reset_drawdown
from agent.storage import database
from agent.storage.repository import (
    AgentConfigRepository,
    EquityRepository,
    RiskEventRepository,
)

PEAK_AT = "2026-10-11T00:00:00+00:00"
NOW_AT = "2026-10-11T09:00:00+00:00"


@pytest.fixture
async def db(tmp_path, monkeypatch):
    """临时库。engine/session 是模块级单例，所以必须先关再换配置。"""
    await database.close_db()
    url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/bianca.db"
    monkeypatch.setattr(database, "get_settings", lambda: Settings(database_url=url))
    await database.init_db()
    yield
    await database.close_db()


@pytest.fixture
async def client(db):
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _snap(equity: float, captured_at: str) -> None:
    await EquityRepository().save(
        cash_quote=equity, holdings_value=0.0, equity=equity,
        positions=[], unpriced=[], gaps=[], notes=[], captured_at=captured_at,
    )


async def _two_snaps() -> None:
    """一个高权益峰值 + 一个当前低权益 —— 回撤由此可见。"""
    await _snap(11000.0, PEAK_AT)
    await _snap(9800.0, NOW_AT)


# ---------------------------------------------------------------- 没有理由就拒


@pytest.mark.asyncio
async def test_reset_without_a_reason_is_refused(db):
    await _two_snaps()

    with pytest.raises(ValueError):
        await reset_drawdown(reason="")

    with pytest.raises(ValueError):
        await reset_drawdown(reason="   ")


@pytest.mark.asyncio
async def test_a_refused_reset_leaves_no_trace_and_changes_nothing(db):
    """**关键**：拒了就得是全拒 —— 不能写入 config 键、也不能留下事件。"""
    await _two_snaps()
    before = await drawdown()

    with pytest.raises(ValueError):
        await reset_drawdown(reason="  ")

    assert await AgentConfigRepository().get("drawdown_reset_at") is None
    assert await RiskEventRepository().list_recent(limit=10) == []
    after = await drawdown()
    assert after.peak_equity == before.peak_equity
    assert after.since is None


# ---------------------------------------------------------------- 一定留痕


@pytest.mark.asyncio
async def test_reset_records_the_state_before_it(db):
    """**关键**：事件里必须是**重置前**的账，否则事后看不出这次削掉了多少保护。"""
    await _two_snaps()
    expected = await drawdown()
    assert expected.peak_equity == pytest.approx(11000.0)

    await reset_drawdown(reason="已人工复盘", now=datetime(2026, 10, 11, 10, 0, tzinfo=UTC))

    events = await RiskEventRepository().list_recent(limit=5)
    assert len(events) == 1
    assert events[0].event_type == "drawdown_reset"
    detail = json.loads(events[0].detail)
    assert detail["reason"] == "已人工复盘"
    assert detail["peak_before"] == pytest.approx(11000.0)
    assert detail["current_equity"] == pytest.approx(9800.0)
    assert detail["drawdown_before"] == pytest.approx((11000.0 - 9800.0) / 11000.0)
    assert detail["previous_reset_at"] is None


@pytest.mark.asyncio
async def test_the_second_reset_records_the_first_one(db):
    """连着重置两次时，第二条事件要记得**上一条**，否则重置链断了。"""
    await _two_snaps()
    first = await reset_drawdown(reason="第一次", now=datetime(2026, 10, 11, 10, 0, tzinfo=UTC))
    second = await reset_drawdown(reason="第二次", now=datetime(2026, 10, 11, 11, 0, tzinfo=UTC))

    assert second.previous_reset_at == first.reset_at
    events = await RiskEventRepository().list_recent(limit=5)
    assert len(events) == 2  # 两条都在，没有被覆盖


@pytest.mark.asyncio
async def test_the_event_and_the_return_value_are_the_same_account(db):
    """**关键**：返回值和落库内容必须逐字一致 —— 否则接口说的和库里记的是两笔账。"""
    await _two_snaps()

    result = await reset_drawdown(reason="核对用", now=datetime(2026, 10, 11, 10, 0, tzinfo=UTC))

    detail = json.loads((await RiskEventRepository().list_recent(limit=1))[0].detail)
    returned = asdict(result)
    for key, value in returned.items():
        if key in detail:
            assert detail[key] == value, f"{key} 对不上：库 {detail[key]!r} vs 返回 {value!r}"


# ---------------------------------------------------------------- HTTP 入口


@pytest.mark.asyncio
async def test_http_reset_reports_before_and_after(client):
    await _two_snaps()

    resp = await client.post("/api/v1/risk/reset-drawdown", json={"reason": "网页确认"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["reason"] == "网页确认"
    assert body["peak_before"] == pytest.approx(11000.0)
    assert body["drawdown_before"] == pytest.approx((11000.0 - 9800.0) / 11000.0)
    assert body["reset_at"]
    assert len(await RiskEventRepository().list_recent(limit=5)) == 1


@pytest.mark.asyncio
async def test_http_reset_without_a_reason_is_a_422_not_a_500(client):
    """缺理由和空白理由都得是参数错误（422），不能长得像服务端故障。"""
    await _two_snaps()

    missing = await client.post("/api/v1/risk/reset-drawdown", json={})
    blank = await client.post("/api/v1/risk/reset-drawdown", json={"reason": "   "})

    assert missing.status_code == 422
    assert blank.status_code == 422
    assert await RiskEventRepository().list_recent(limit=5) == []


@pytest.mark.asyncio
async def test_http_reset_trims_the_reason(client):
    resp = await client.post("/api/v1/risk/reset-drawdown", json={"reason": "  前后有空格  "})

    assert resp.status_code == 200
    assert resp.json()["reason"] == "前后有空格"


# ---------------------------------------------------------------- CLI 入口


@pytest.mark.asyncio
async def test_cli_reset_does_the_same_thing(db):
    """两个入口调同一个函数 —— 这里证明 CLI 那条路真的接到了它。

    测的是 `_parse_args` + `_run` 而不是 `main()`：`main` 内部 `asyncio.run()`，
    在测试的事件循环里调不了。两者拼起来就是 `main` 除去样板的那部分。
    真正的端到端（真的敲一遍命令）在交付时手工跑过。
    """
    from agent.risk.__main__ import _parse_args, _run

    await _two_snaps()

    args = _parse_args(["reset-drawdown", "--reason", "命令行确认"])

    assert await _run(args) == 0
    events = await RiskEventRepository().list_recent(limit=5)
    assert len(events) == 1
    assert json.loads(events[0].detail)["reason"] == "命令行确认"


def test_cli_reset_without_a_reason_is_a_usage_error():
    """argparse 自己拦下 —— 连 `_run` 都到不了。这一步不需要库。"""
    from agent.risk.__main__ import _parse_args

    with pytest.raises(SystemExit) as exc:
        _parse_args(["reset-drawdown"])

    assert exc.value.code == 2


def test_cli_requires_a_subcommand():
    from agent.risk.__main__ import _parse_args

    with pytest.raises(SystemExit) as exc:
        _parse_args([])

    assert exc.value.code == 2


@pytest.mark.asyncio
async def test_cli_drawdown_is_read_only(db, capsys):
    from agent.risk.__main__ import _parse_args, _run

    await _two_snaps()

    assert await _run(_parse_args(["drawdown", "--json"])) == 0

    body = json.loads(capsys.readouterr().out)
    assert body["known"] is True
    assert body["peak_equity"] == pytest.approx(11000.0)
    assert await AgentConfigRepository().get("drawdown_reset_at") is None
    assert await RiskEventRepository().list_recent(limit=5) == []


@pytest.mark.asyncio
async def test_cli_drawdown_unknown_is_not_an_error(db, capsys):
    """没有快照时读数是"未知"，但命令本身成功 —— 未知不是故障。"""
    from agent.risk.__main__ import _parse_args, _run

    assert await _run(_parse_args(["drawdown", "--json"])) == 0

    body = json.loads(capsys.readouterr().out)
    assert body["known"] is False
    assert body["pct"] is None
    assert body["reason"]
