"""阶段 C-5：HTTP 载体（ADR-032）。

盯的是**接线的形状**，不是再测一遍对话逻辑（那在 C-1~C-4 里）：

1. 动作一一对应，状态全在库里 —— 每条请求新建 `Dialogue` 也不丢上下文；
2. **业务拒绝是 200 + 人话，不是 HTTP 错误**（未确认就规划、已生效不让改）；
   只有「id 不对」「开第二个目标」这类调用方错误才是 4xx；
3. **确认的权益来自最近一次交易所快照**，不从请求体来；
   没有快照就 409，且 detail 指路去采一次。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from agent.config import Settings
from agent.main import app
from agent.manager.loop import ManagerRun
from agent.manager.schemas import Plan, PlanStep
from agent.storage import database
from agent.storage.repository import EquityRepository, GoalRepository

OPENING = "我想用 1000 USDT 试水，两周内做到 1100，最多亏 5% 就停"
COMPLETE = "用 1000 USDT 做 BTCUSDT，两周内赚 10%，最多亏 5% 就停"


@pytest.fixture
async def db(tmp_path, monkeypatch):
    await database.close_db()
    url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/bianca.db"
    monkeypatch.setattr(database, "get_settings", lambda: Settings(database_url=url))
    await database.init_db()
    yield
    await database.close_db()


@pytest.fixture
async def client(db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _snapshot(equity: float = 1000.0, captured_at: str = "2026-10-18T01:00:00+00:00"):
    return await EquityRepository().save(
        cash_quote=equity,
        holdings_value=0.0,
        equity=equity,
        positions=[],
        unpriced=[],
        gaps=[],
        notes=[],
        captured_at=captured_at,
    )


async def _start(client: AsyncClient, text: str = OPENING) -> str:
    resp = await client.post("/api/v1/dialogue/start", json={"text": text})
    assert resp.status_code == 200, resp.text
    return resp.json()["goal_id"]


# --- 状态全在库里：每条请求新建 Dialogue 也接得上 ------------------------------

async def test_the_conversation_survives_across_requests(client):
    goal_id = await _start(client)

    resp = await client.post(
        f"/api/v1/dialogue/{goal_id}/say", json={"text": "做 BTCUSDT，赚 10%，亏 5% 就停"}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["goal_id"] == goal_id
    assert "symbols" in body["changed"]
    assert body["status"] == "proposed"


async def test_current_returns_the_open_goal_and_404_when_there_is_none(client):
    resp = await client.get("/api/v1/dialogue/current")
    assert resp.status_code == 404

    goal_id = await _start(client)

    resp = await client.get("/api/v1/dialogue/current")
    assert resp.status_code == 200
    assert resp.json()["goal_id"] == goal_id


async def test_show_does_not_read_a_new_sentence(client):
    goal_id = await _start(client, COMPLETE)

    resp = await client.get(f"/api/v1/dialogue/{goal_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["changed"] == []
    assert "没有读出任何可以改的维度" not in body["text"]


# --- 确认：权益来自快照，不从请求体来（ADR-033）--------------------------------

async def test_confirm_without_a_snapshot_is_409_and_points_at_the_right_endpoint(client):
    goal_id = await _start(client, COMPLETE)

    resp = await client.post(f"/api/v1/dialogue/{goal_id}/confirm")

    assert resp.status_code == 409
    assert "portfolio/snapshot" in resp.json()["detail"]


async def test_confirm_uses_the_latest_exchange_snapshot(client):
    goal_id = await _start(client, COMPLETE)
    await _snapshot(equity=873.5, captured_at="2026-10-18T02:30:00+00:00")

    resp = await client.post(f"/api/v1/dialogue/{goal_id}/confirm")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["activated"] is True
    assert "873.5" in body["text"]
    # 快照多旧要看得见 —— 这比"激活这一刻读的"那句假话有用。
    assert "2026-10-18T02:30:00+00:00" in body["text"]

    row = await GoalRepository().get(goal_id)
    assert row.baseline_equity == 873.5


async def test_confirm_does_not_take_equity_from_the_body(client):
    """请求体里塞了 equity 也不该生效 —— 端点的签名根本不收它。"""
    goal_id = await _start(client, COMPLETE)
    await _snapshot(equity=900.0)

    resp = await client.post(
        f"/api/v1/dialogue/{goal_id}/confirm", json={"equity": 1.0}
    )

    assert resp.status_code == 200, resp.text
    assert (await GoalRepository().get(goal_id)).baseline_equity == 900.0


# --- 业务拒绝不是 HTTP 错误 ----------------------------------------------------

async def test_planning_before_confirm_is_a_200_with_an_explanation(client):
    """未确认就规划是**正常的工作结果**，不是 4xx —— 4xx 会被写进重试逻辑。"""
    goal_id = await _start(client, COMPLETE)

    resp = await client.post(f"/api/v1/dialogue/{goal_id}/plan")

    assert resp.status_code == 200
    assert "还不能规划" in resp.json()["text"]


async def test_calling_plan_twice_over_http_does_not_replace_the_first(client, monkeypatch):
    class _FakeManager:
        def __init__(self, **_kwargs) -> None:
            pass

        async def plan(self, goal: str, *, max_rounds: int = 4) -> ManagerRun:
            return ManagerRun(
                goal=goal,
                plan=Plan(objective="做到 1100", steps=[PlanStep(what="看 K 线")]),
                plan_shape="json",
                final_text="{}",
                transcript=[{"role": "user", "content": goal}],
            )

    monkeypatch.setattr("agent.manager.loop.Manager", _FakeManager)
    goal_id = await _start(client, COMPLETE)
    await _snapshot()
    await client.post(f"/api/v1/dialogue/{goal_id}/confirm")

    first = await client.post(f"/api/v1/dialogue/{goal_id}/plan")
    second = await client.post(f"/api/v1/dialogue/{goal_id}/plan")

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["plan_id"] is not None
    assert second.json()["plan_id"] is None
    assert "不重规划" in second.json()["text"]
    assert (await GoalRepository().get(goal_id)).plan_id == first.json()["plan_id"]


# --- 调用方错误才是 4xx --------------------------------------------------------

async def test_a_second_goal_is_409(client):
    await _start(client)

    resp = await client.post("/api/v1/dialogue/start", json={"text": COMPLETE})

    assert resp.status_code == 409


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/dialogue/nope"),
        ("post", "/api/v1/dialogue/nope/say"),
        ("post", "/api/v1/dialogue/nope/confirm"),
        ("post", "/api/v1/dialogue/nope/plan"),
    ],
)
async def test_an_unknown_goal_is_404(client, method, path):
    kwargs = {"json": {"text": "随便说一句"}} if path.endswith("/say") else {}
    resp = await getattr(client, method)(path, **kwargs)

    assert resp.status_code == 404


async def test_an_empty_sentence_is_422(client):
    resp = await client.post("/api/v1/dialogue/start", json={"text": "   "})

    assert resp.status_code == 422
