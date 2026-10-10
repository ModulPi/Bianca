"""阶段 B-1：工具注册表 + prompt 渲染 + 解析器。

全部离线：不碰 LLM、不碰网、不碰真库。

这里最该看的是**解析器那几条「不该被当成调用」的用例** —— 它们挡住的失败比
「解析出错」严重得多：把模型随口一段计划执行掉，等于让它的话变成真实动作。
"""

from __future__ import annotations

import json

import pytest

from agent.manager.parser import (
    DEEPSEEK_CALLS_BEGIN,
    DEEPSEEK_CALLS_END,
    DEEPSEEK_CALL_BEGIN,
    DEEPSEEK_CALL_END,
    DEEPSEEK_SEP,
    ToolCallParseError,
    looks_like_attempt,
    parse_reply,
)
from agent.manager.prompt import build_system_prompt, render_tool_catalog
from agent.manager.tools import (
    ToolArgumentError,
    ToolRegistrationError,
    ToolRegistry,
    ToolSpec,
    UnknownToolError,
)

TOOLS = ("get_position", "get_market", "get_risk_limits")


def _registry(**kwargs) -> ToolRegistry:
    reg = ToolRegistry(**kwargs)

    async def _echo(args: dict):
        return {"echo": args}

    for name in TOOLS:
        reg.register(
            ToolSpec(
                name=name,
                description=f"{name} 的说明",
                parameters={
                    "type": "object",
                    "properties": {"asset": {"type": "string"}},
                    "required": ["asset"],
                },
                handler=_echo,
            )
        )
    return reg


# --- 注册表 ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_refuses_state_changing_tool_by_default():
    """阶段 B 的验收是「不产生任何订单」。拦截点是注册口，不是调用方的自觉。"""
    reg = ToolRegistry()

    async def _order(args: dict):
        return {"ok": True}

    with pytest.raises(ToolRegistrationError) as exc:
        reg.register(
            ToolSpec(
                name="place_order",
                description="下单",
                parameters={"type": "object", "properties": {}},
                handler=_order,
                read_only=False,
            )
        )
    assert "place_order" in str(exc.value)
    assert len(reg) == 0, "被拒的工具不能留下任何痕迹"


def test_registry_allows_writes_only_when_explicitly_opened():
    reg = ToolRegistry(allow_writes=True)

    async def _order(args: dict):
        return {"ok": True}

    reg.register(
        ToolSpec(
            name="place_order",
            description="下单",
            parameters={"type": "object", "properties": {}},
            handler=_order,
            read_only=False,
        )
    )
    assert "place_order" in reg


def test_registry_rejects_duplicate_names():
    reg = _registry()
    with pytest.raises(ToolRegistrationError):

        async def _h(args: dict):
            return None

        reg.register(
            ToolSpec(
                name="get_position",
                description="重复的",
                parameters={"type": "object", "properties": {}},
                handler=_h,
            )
        )


@pytest.mark.asyncio
async def test_call_validates_required_args_before_handler_runs():
    """缺参数要在这里断掉 —— 让 handler 自己面对缺参数，等于让它替模型猜默认值。"""
    reg = _registry()
    seen: list[dict] = []

    async def _spy(args: dict):
        seen.append(args)
        return {"ok": True}

    reg.register(
        ToolSpec(
            name="spy",
            description="记录调用",
            parameters={
                "type": "object",
                "properties": {"asset": {"type": "string"}},
                "required": ["asset"],
            },
            handler=_spy,
        )
    )
    with pytest.raises(ToolArgumentError) as exc:
        await reg.call("spy", {})
    assert "asset" in str(exc.value)
    assert seen == [], "handler 压根不该被调到"

    assert await reg.call("spy", {"asset": "BTC"}) == {"ok": True}
    assert seen == [{"asset": "BTC"}]


@pytest.mark.asyncio
async def test_call_unknown_tool_raises_rather_than_returning_nothing():
    reg = _registry()
    with pytest.raises(UnknownToolError) as exc:
        await reg.call("get_balance", {"asset": "BTC"})
    # 报错要把已注册的名单带出来，模型才有机会改
    assert "get_position" in str(exc.value)


# --- 解析器：认得出的形状 ---------------------------------------------------


def test_parse_deepseek_native_format_from_the_real_model():
    """这段是 §5.1 实测里本地模型**真实吐出来**的原文，别改。"""
    content = (
        "\n"
        f"{DEEPSEEK_CALLS_BEGIN}{DEEPSEEK_CALL_BEGIN}function{DEEPSEEK_SEP}get_position\n"
        "```json\n"
        '{"asset":"BTC"}\n'
        "```\n"
        f"{DEEPSEEK_CALL_END}{DEEPSEEK_CALLS_END}\n"
    )
    reply = parse_reply(content, known_tools=TOOLS)
    assert reply.shape == "deepseek"
    assert len(reply.calls) == 1
    assert reply.calls[0].name == "get_position"
    assert reply.calls[0].arguments == {"asset": "BTC"}
    assert reply.text == ""


def test_parse_deepseek_format_with_several_calls_and_leftover_prose():
    content = (
        f"{DEEPSEEK_CALLS_BEGIN}"
        f"{DEEPSEEK_CALL_BEGIN}function{DEEPSEEK_SEP}get_position\n"
        '```json\n{"asset":"BTC"}\n```\n'
        f"{DEEPSEEK_CALL_END}"
        f"{DEEPSEEK_CALL_BEGIN}function{DEEPSEEK_SEP}get_market\n"
        '```json\n{"asset":"BTC"}\n```\n'
        f"{DEEPSEEK_CALL_END}"
        f"{DEEPSEEK_CALLS_END}"
        "\n先查这两样，再定计划。"
    )
    reply = parse_reply(content, known_tools=TOOLS)
    assert [c.name for c in reply.calls] == ["get_position", "get_market"]
    assert "先查这两样" in reply.text, "调用之外的话要留下来，不能吞掉"
    assert DEEPSEEK_CALLS_BEGIN not in reply.text


def test_parse_fenced_json_call():
    reply = parse_reply(
        '```json\n{"name": "get_position", "arguments": {"asset": "BTC"}}\n```',
        known_tools=TOOLS,
    )
    assert reply.shape == "fenced"
    assert reply.calls[0].name == "get_position"
    assert reply.calls[0].arguments == {"asset": "BTC"}


def test_parse_structured_tool_calls():
    reply = parse_reply(
        "",
        known_tools=TOOLS,
        message={
            "tool_calls": [
                {"function": {"name": "get_market", "arguments": '{"asset": "BTC"}'}}
            ]
        },
    )
    assert reply.shape == "structured"
    assert reply.calls[0].arguments == {"asset": "BTC"}, "arguments 是 JSON 字符串也要认"


def test_parse_nested_arguments_survives_brace_balancing():
    """正则数不清嵌套，而参数就是嵌套的 —— 用配平扫。"""
    args = {"asset": "BTC", "opts": {"deep": {"n": 1}}, "note": "括号 } 在字符串里"}
    reply = parse_reply(
        "```json\n" + json.dumps({"name": "get_position", "arguments": args}) + "\n```",
        known_tools=TOOLS,
    )
    assert reply.calls[0].arguments == args


# --- 解析器：**不该**被当成调用的东西 ----------------------------------------


def test_plain_plan_is_not_a_call():
    """最要紧的一条：计划里也可能有 name，但不会有 arguments。"""
    plan = {
        "summary": "先观察再动手",
        "steps": [
            {"name": "查持仓", "why": "不知道手里有什么就没法定仓位"},
            {"name": "查风控上限", "why": "上限决定单笔规模"},
        ],
    }
    reply = parse_reply("```json\n" + json.dumps(plan, ensure_ascii=False) + "\n```", known_tools=TOOLS)
    assert reply.calls == ()
    assert reply.shape == "text"


def test_plan_with_name_key_but_no_arguments_is_not_a_call():
    reply = parse_reply(
        '```json\n{"name": "我的计划", "steps": ["查持仓", "定仓位"]}\n```',
        known_tools=TOOLS,
    )
    assert reply.calls == ()


def test_plan_step_naming_a_real_tool_without_arguments_is_not_a_call():
    """计划里提到真实工具名（没给参数）—— 归纳出的形状，不是调用。

    这条挡不住 `arguments` 那道闸（`name` 嵌在 `steps` 里，`_iter_json_objects`
    只吐最外层），它钉的是「name 必须命中已注册工具」那一层。
    """
    plan = {
        "summary": "先摸清底细",
        "steps": [
            {"name": "get_position", "why": "先看手里有什么", "then": "再决定加不加仓"},
            {"name": "get_risk_limits", "why": "上限决定单笔规模"},
        ],
    }
    reply = parse_reply(
        "```json\n" + json.dumps(plan, ensure_ascii=False) + "\n```",
        known_tools=TOOLS,
    )
    assert reply.calls == (), "计划里提到工具名，不等于它在调工具"
    assert reply.shape == "text"


@pytest.mark.parametrize(
    "payload",
    [
        # 顶层就带真实工具名，但没给参数 —— 模型只写了个壳
        '{"name": "get_position", "why": "先看手里有什么"}',
        # OpenAI 的嵌套形态也一样，这里是靠 inner 那一支走到闸门前
        '{"type": "function", "function": {"name": "get_position"}}',
    ],
)
def test_real_tool_name_without_arguments_is_not_a_call(payload: str):
    """**这条钉的才是 `arguments` 那道闸。**

    上面两条用例的 `name` 要么不认识、要么嵌得够深，在更早的一层就被刷掉了 ——
    把 `arguments` 判据拆掉，它们照样全绿（已实测）。只有这里 `name` 是真工具、
    且没有再套一层，才会真正走到那道闸。拆掉它，这条用例立刻挂（已实测）。
    """
    reply = parse_reply("```json\n" + payload + "\n```", known_tools=TOOLS)
    assert reply.calls == (), "认不出参数就不算调用 —— 不能猜一个空参数去执行"
    assert reply.shape == "text"


def test_unknown_tool_name_is_not_a_call():
    """模型编一个不存在的工具 —— 不执行它。§5.1 里那个 `get_balance` 就是这种。"""
    reply = parse_reply(
        '```json\n{"name": "get_balance", "arguments": {"asset": "BTC"}}\n```',
        known_tools=TOOLS,
    )
    assert reply.calls == ()
    assert reply.shape == "text"


def test_structured_call_to_unknown_tool_raises():
    """结构化通道里点名了不存在的工具要**报出来**，不能当成「这次没调工具」。"""
    with pytest.raises(ToolCallParseError) as exc:
        parse_reply(
            "",
            known_tools=TOOLS,
            message={"tool_calls": [{"function": {"name": "get_balance", "arguments": "{}"}}]},
        )
    assert "get_balance" in str(exc.value)


def test_deepseek_marker_without_payload_raises():
    """看得见调用标记却解不出来 —— 拒，不降级成「模型没调工具」。"""
    content = f"{DEEPSEEK_CALLS_BEGIN}{DEEPSEEK_CALL_BEGIN}function{DEEPSEEK_SEP}get_position"
    with pytest.raises(ToolCallParseError):
        parse_reply(content, known_tools=TOOLS)


def test_deepseek_call_without_json_raises():
    content = (
        f"{DEEPSEEK_CALLS_BEGIN}{DEEPSEEK_CALL_BEGIN}function{DEEPSEEK_SEP}get_position\n"
        "（这里本该有参数但没有）"
        f"{DEEPSEEK_CALL_END}{DEEPSEEK_CALLS_END}"
    )
    with pytest.raises(ToolCallParseError):
        parse_reply(content, known_tools=TOOLS)


def test_qwen_marker_without_payload_raises():
    with pytest.raises(ToolCallParseError):
        parse_reply("<tool_call>我打算调用它</tool_call>", known_tools=TOOLS)


def test_broken_arguments_string_raises():
    with pytest.raises(ToolCallParseError):
        parse_reply(
            "",
            known_tools=TOOLS,
            message={"tool_calls": [{"function": {"name": "get_position", "arguments": "{不是 json"}}]},
        )


def test_prose_alone_is_just_text():
    reply = parse_reply("我建议先观察，等回撤到位再进场。", known_tools=TOOLS)
    assert reply.calls == ()
    assert reply.shape == "text"
    assert reply.text == "我建议先观察，等回撤到位再进场。"
    assert not reply.wants_tools
    assert not looks_like_attempt(reply.text)


# --- prompt ---------------------------------------------------------------


def test_system_prompt_carries_the_catalog_in_the_body_not_a_tools_field():
    reg = _registry()
    prompt = build_system_prompt(reg)

    for name in TOOLS:
        assert name in prompt, f"{name} 的说明必须出现在 prompt 正文里"
    assert DEEPSEEK_CALLS_BEGIN in prompt and DEEPSEEK_SEP in prompt, "得把调用格式教给模型"
    assert "只读" in prompt
    assert "没有" in prompt and "下单权限" in prompt
    # 清单是正文的一部分，可以直接被解析回来
    catalog = json.loads(prompt.split("```json", 1)[1].split("```", 1)[0])
    assert {c["function"]["name"] for c in catalog} == set(TOOLS)
    assert all(c["function"]["parameters"]["type"] == "object" for c in catalog)


def test_render_tool_catalog_matches_registry_schemas():
    reg = _registry()
    assert json.loads(render_tool_catalog(reg)) == reg.schemas()


def test_empty_registry_prompt_still_renders():
    prompt = build_system_prompt(ToolRegistry())
    assert "可用工具" in prompt
    assert "[]" in prompt
