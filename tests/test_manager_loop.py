"""阶段 B-3：经理循环。

用**脚本化的 chat** 跑，不出网。这里钉的是「失败时它说了什么」——
一个会在工具没跑成时塞个编造结果进去的循环，比一个直接崩掉的循环危险得多。
"""

from __future__ import annotations

import json

import pytest

from agent.config import Settings
from agent.manager.loop import Manager, Reply
from agent.manager.parser import DEEPSEEK_CALL_BEGIN, DEEPSEEK_CALL_END, DEEPSEEK_CALLS_BEGIN
from agent.manager.parser import DEEPSEEK_CALLS_END, DEEPSEEK_SEP
from agent.manager.tools import ToolRegistry, ToolSpec

GOOD_PLAN = json.dumps(
    {
        "objective": "把回撤控制在 5% 以内",
        "steps": [
            {"what": "先看当前权益与回撤", "why": "不知道起点就没法定目标", "tool": "get_drawdown"},
            {"what": "把单笔规模压到上限的三分之一", "why": "留出连续亏损的余地"},
        ],
        "stop_conditions": ["回撤达到 5%"],
        "assumptions": ["行情库是新鲜的"],
        "risks": ["行情采集停了会看到过时数据"],
    },
    ensure_ascii=False,
)


def _deepseek_call(name: str, args: dict) -> str:
    return (
        f"{DEEPSEEK_CALLS_BEGIN}{DEEPSEEK_CALL_BEGIN}function{DEEPSEEK_SEP}{name}\n"
        "```json\n" + json.dumps(args, ensure_ascii=False) + "\n```\n"
        f"{DEEPSEEK_CALL_END}{DEEPSEEK_CALLS_END}"
    )


def _registry(*, boom: bool = False) -> ToolRegistry:
    reg = ToolRegistry()

    async def _drawdown(args: dict):
        if boom:
            raise RuntimeError("快照表锁住了")
        return {"available": True, "drawdown_pct": 0.02}

    async def _portfolio(args: dict):
        return {"available": False, "reason": "还没有权益快照"}

    reg.register(
        ToolSpec(
            name="get_drawdown",
            description="读当前回撤",
            parameters={"type": "object", "properties": {}},
            handler=_drawdown,
        )
    )
    reg.register(
        ToolSpec(
            name="get_portfolio",
            description="读权益快照",
            parameters={"type": "object", "properties": {}},
            handler=_portfolio,
        )
    )
    return reg


def _structured_reply(name: str, args: dict, call_id: str = "call_abc") -> Reply:
    """原生 `tools` 通道的回执形状：调用号在 `message.tool_calls[].id` 里。"""
    return Reply(
        content="",
        message={
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                }
            ],
        },
    )


def _scripted(replies: list):
    seen: list[list[dict]] = []

    async def chat(messages):
        seen.append([dict(m) for m in messages])
        item = replies[min(len(seen) - 1, len(replies) - 1)]
        return item if isinstance(item, Reply) else Reply(content=item)

    return chat, seen


def _manager(replies, *, registry=None, settings=None):
    chat, seen = _scripted(replies)
    mgr = Manager(
        registry or _registry(),
        settings=settings or Settings(),
        chat=chat,
    )
    return mgr, seen


@pytest.mark.asyncio
async def test_plan_straight_away_without_any_tool_call():
    mgr, seen = _manager([GOOD_PLAN])
    run = await mgr.plan("把回撤控制在 5% 以内")
    assert run.plan is not None
    assert run.plan_shape == "json"
    assert run.rounds == 1
    assert run.invocations == []
    assert run.plan.steps[1].why, "每一步都该带依据"
    assert seen[0][0]["role"] == "system"
    assert seen[0][1]["content"] == "把回撤控制在 5% 以内"


@pytest.mark.asyncio
async def test_tool_call_then_plan_feeds_the_result_back():
    mgr, seen = _manager([_structured_reply("get_drawdown", {}, "call_xyz"), GOOD_PLAN])
    run = await mgr.plan("把回撤控制在 5% 以内")

    assert run.plan is not None
    assert run.used_tools == ["get_drawdown"]
    assert run.invocations[0].ok is True
    assert run.invocations[0].result == {"available": True, "drawdown_pct": 0.02}

    # 第二轮必须看得到第一轮的工具结果
    second = seen[1]
    tool_msgs = [m for m in second if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    payload = json.loads(tool_msgs[0]["content"])
    assert payload["ok"] is True
    assert payload["result"]["drawdown_pct"] == 0.02


@pytest.mark.asyncio
async def test_structured_call_pairs_by_the_server_issued_id():
    """原生 `tools` 通道：assistant 那条得原样带上 `tool_calls`，
    观测结果得按**服务端发的那个号**回 —— 序号对不上，服务端不认。"""
    mgr, seen = _manager([_structured_reply("get_drawdown", {}, "call_xyz"), GOOD_PLAN])
    await mgr.plan("把回撤控制在 5% 以内")

    second = seen[1]
    assistant = [m for m in second if m.get("role") == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["tool_calls"][0]["id"] == "call_xyz", "assistant 必须带着 tool_calls"

    tool_msgs = [m for m in second if m.get("role") == "tool"]
    assert tool_msgs[0]["tool_call_id"] == "call_xyz"


@pytest.mark.asyncio
async def test_fallback_shape_never_fabricates_a_tool_call_id():
    """走回退路径（DeepSeek 原生块，服务端没给号）时，**绝不自己编一个号**。
    编出来的号在服务端对不上任何一条 `tool_calls`，轻则 400，重则被当成真发生过的调用。"""
    mgr, seen = _manager([_deepseek_call("get_drawdown", {}), GOOD_PLAN])
    run = await mgr.plan("把回撤控制在 5% 以内")

    assert run.invocations[0].ok is True, "回退路径照样要把工具跑起来"
    second = seen[1]
    assert not [m for m in second if m.get("role") == "tool"], "没有真号就别用 tool 角色"
    carriers = [m for m in second if m["role"] == "user" and "get_drawdown 的结果" in m["content"]]
    assert len(carriers) == 1
    assert "tool_call_id" not in json.dumps(second)


@pytest.mark.asyncio
async def test_failing_tool_is_reported_never_faked():
    """**这条是整个阶段 B 最要紧的一条。** handler 炸了，回给模型的必须是错误本身 ——
    不能塞一个看起来正常的结果，否则模型会照着编下去（§5.1 的 get_balance 0.00 BTC）。"""
    mgr, seen = _manager(
        [_structured_reply("get_drawdown", {}, "call_xyz"), GOOD_PLAN], registry=_registry(boom=True)
    )
    run = await mgr.plan("把回撤控制在 5% 以内")

    inv = run.invocations[0]
    assert inv.ok is False
    assert "快照表锁住了" in (inv.error or "")

    tool_msgs = [m for m in seen[1] if m.get("role") == "tool"]
    payload = json.loads(tool_msgs[0]["content"])
    assert payload["ok"] is False
    assert "快照表锁住了" in payload["error"]
    assert "result" not in payload, "失败就不能带一个结果字段"


@pytest.mark.asyncio
async def test_unknown_tool_gets_an_error_and_a_chance_to_correct():
    """模型点名一个不存在的工具。走结构化通道时解析层会拒；这里走的是
    「围栏 JSON 里 name 不认识」那条 —— 它不算调用，于是整轮变成「没给计划」，被打回。"""
    mgr, seen = _manager(
        ['```json\n{"name": "get_balance", "arguments": {}}\n```', GOOD_PLAN]
    )
    run = await mgr.plan("把回撤控制在 5% 以内")
    assert run.invocations == [], "不存在的工具绝不能被调用"
    assert run.plan is not None, "被打回之后要能重来"
    assert run.rounds == 2
    # 打回时明确告诉它上一轮不算数
    assert any("工具" in (m.get("content") or "") for m in seen[1] if m["role"] == "user")


@pytest.mark.asyncio
async def test_malformed_call_is_rejected_and_explained():
    """看得见调用标记却解不出来 —— 回一条解释，而不是当成「没调工具」继续往前。"""
    broken = f"{DEEPSEEK_CALLS_BEGIN}{DEEPSEEK_CALL_BEGIN}function{DEEPSEEK_SEP}get_drawdown"
    mgr, _ = _manager([broken, GOOD_PLAN])
    run = await mgr.plan("把回撤控制在 5% 以内")
    assert run.plan is not None
    assert run.errors and "解析不出来" in run.errors[0]


@pytest.mark.asyncio
async def test_gives_up_honestly_after_max_rounds():
    mgr, _ = _manager(["我觉得可以先观察一下再说。"])
    run = await mgr.plan("随便赚点", max_rounds=3)
    assert run.plan is None
    assert run.plan_shape == "text"
    assert run.rounds == 3
    assert any("没有拿到结构化计划" in e for e in run.errors)
    assert run.final_text == "我觉得可以先观察一下再说。"


@pytest.mark.asyncio
async def test_empty_goal_is_refused():
    mgr, _ = _manager([GOOD_PLAN])
    with pytest.raises(ValueError):
        await mgr.plan("   ")


@pytest.mark.asyncio
async def test_transcript_carries_the_full_prompt_for_attribution():
    """ADR-019 R1：留的是**完整 prompt**，不是摘要。"""
    mgr, _ = _manager([GOOD_PLAN])
    run = await mgr.plan("把回撤控制在 5% 以内")
    assert run.transcript[0]["role"] == "system"
    for name in mgr.registry.names():
        assert name in run.transcript[0]["content"]
    assert run.transcript[-1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_plan_without_reasons_is_still_reported_as_such():
    """计划缺依据不算失败，但必须能被调用方看出来 —— 静默放过就失去意义了。"""
    plan = json.dumps({"objective": "测试", "steps": [{"what": "随便做点啥"}]}, ensure_ascii=False)
    mgr, _ = _manager([plan])
    run = await mgr.plan("测试")
    assert run.plan is not None
    assert run.plan.steps_without_reason == ["随便做点啥"]


@pytest.mark.asyncio
async def test_transport_failure_is_not_reported_as_a_missing_plan():
    """连不上模型**不等于**模型没给计划。这两句混成一句，等于替模型背锅 ——
    事后归因时会以为「它想过了但没想出来」，而事实是根本没问上。"""
    import httpx

    async def dead_chat(messages):
        raise httpx.ReadTimeout("timed out")

    mgr = Manager(_registry(), settings=Settings(), chat=dead_chat)
    run = await mgr.plan("把回撤控制在 5% 以内")

    assert run.plan is None
    assert run.plan_shape == "none"
    assert any("跟模型说话失败" in e for e in run.errors)
    assert not any("轮之内没有拿到结构化计划" in e for e in run.errors)


@pytest.mark.asyncio
async def test_transport_failure_stops_retrying():
    """连不上就该停。硬撑满 6 轮只会让用户多等 6 个超时。"""
    import httpx

    calls = []

    async def dead_chat(messages):
        calls.append(1)
        raise httpx.ConnectError("connection refused")

    mgr = Manager(_registry(), settings=Settings(), chat=dead_chat)
    run = await mgr.plan("把回撤控制在 5% 以内")
    assert len(calls) == 1
    assert run.rounds == 1


# --- 方案 B 的技术决定要被测试钉住 -------------------------------------------


@pytest.mark.asyncio
async def test_http_chat_sends_the_native_tools_field(monkeypatch):
    """**这条钉的是 §6.4.1 那个转向。** 换到 DeepSeek 官方 API 之后走原生通道 ——
    工具清单既进 `tools` 字段，也仍在 system message 里（回退时用得上）。
    谁要把 `tools` 摘掉，这条会红。"""
    captured: list[dict] = []

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": GOOD_PLAN}}], "usage": {"total_tokens": 7}}

    class _Client:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):  # noqa: A002 — httpx 的形参名
            captured.append({"url": url, "payload": json, "headers": headers})
            return _Resp()

    monkeypatch.setattr("agent.manager.loop.httpx.AsyncClient", _Client)

    mgr = Manager(_registry(), settings=Settings())
    run = await mgr.plan("把回撤控制在 5% 以内")

    assert run.plan is not None
    assert len(captured) == 1
    payload = captured[0]["payload"]
    assert "tools" in payload, "§6.4.1：改走原生 tools 字段"
    assert payload["tool_choice"] == "auto"
    sent = {t["function"]["name"] for t in payload["tools"]}
    assert sent == set(mgr.registry.names())
    # 清单仍在 system message 里 —— 回退到本地模型时靠它
    assert mgr.registry.names()[0] in payload["messages"][0]["content"]


@pytest.mark.asyncio
async def test_loop_never_places_an_order_even_if_the_model_asks():
    """**阶段 B 的验收条件本身。** 模型明确要求下单，循环也只能拒绝 ——
    因为注册表里根本没有下单工具。"""
    asked = '```json\n{"name": "place_order", "arguments": {"symbol": "BTCUSDT", "side": "BUY", "amount": 100}}\n```'
    mgr, _ = _manager([asked, GOOD_PLAN])
    run = await mgr.plan("买点 BTC")
    assert run.invocations == []
    assert "place_order" not in mgr.registry
