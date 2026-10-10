"""解析模型回的工具调用 —— **方案 B 的核心，也是它的全部代价所在**。

§5.1 的四次实测定下了这里必须认的形状。它们不是猜的，是那个本地模型真实吐出来的：

1. **结构化 `message.tool_calls`** —— 本地这条路走不通（带 `tools` 字段不是 0 条就是
   HTTP 500），但换云端模型时会出现，所以先认。
2. **DeepSeek 原生块** —— 不给 `tools` 字段时模型稳定产出这个，选得还对：

       <｜tool▁calls▁begin｜><｜tool▁call▁begin｜>function<｜tool▁sep｜>get_position
       ```json
       {"asset":"BTC"}
       ```
       <｜tool▁call▁end｜><｜tool▁calls▁end｜>

3. **裸的 ```json 围栏** `{"name": …, "arguments": …}` —— 给 `tools` 字段时它这么回。

**判定「这是调用而不是计划」靠什么。** 计划也是 JSON，形状还可能很像。这里用的判据是
**`name` 命中已注册的工具名，且带 `arguments`** —— 两个都满足才算调用。
不满足的一律当正文，绝不执行。这条有专门的用例钉住：把一段计划误当成命令执行，
后果是「模型随口说的一句话变成了真实动作」。

**解析不出来就拒，不糊过去。** 看得见调用标记（分隔符、`<tool_call>`）却解不出合法
载荷，抛 `ToolCallParseError`，由经理循环把错误原样回给模型重试，
**而不是当成「模型这次没调工具」继续往下走**。
"""

from __future__ import annotations

import json
import re
from collections.abc import Container, Iterable
from dataclasses import dataclass
from typing import Any

from agent.manager.tools import ToolError

# DeepSeek 的分隔符。`｜` 是全角竖线 U+FF5C，`▁` 是 U+2581 —— 别手打成 ASCII 竖线。
DEEPSEEK_CALLS_BEGIN = "<｜tool▁calls▁begin｜>"
DEEPSEEK_CALLS_END = "<｜tool▁calls▁end｜>"
DEEPSEEK_CALL_BEGIN = "<｜tool▁call▁begin｜>"
DEEPSEEK_CALL_END = "<｜tool▁call▁end｜>"
DEEPSEEK_SEP = "<｜tool▁sep｜>"

# Qwen / Hermes 风格的另一种外壳（换模型时会遇到）
QWEN_CALL_BEGIN = "<tool_call>"
QWEN_CALL_END = "</tool_call>"

ATTEMPT_MARKERS = (
    DEEPSEEK_CALLS_BEGIN,
    DEEPSEEK_CALL_BEGIN,
    QWEN_CALL_BEGIN,
)

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]*")


class ToolCallParseError(ToolError):
    """看得见模型在调工具，却解不出合法载荷。**不降级、不猜。**"""


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    # 服务端给的调用号。原生 `tools` 通道下必须有 —— 回 `role: "tool"` 时按它配对。
    # 走自解析的回退路径（deepseek / fenced 形状）时是 None，此时**不伪造一个号**。
    id: str | None = None


@dataclass(frozen=True)
class ParsedReply:
    """一次回执的解读结果。`shape` 要落库 —— 事后得看出模型是用哪种格式回的。"""

    calls: tuple[ToolCall, ...]
    text: str
    shape: str  # "structured" | "deepseek" | "fenced" | "text"

    @property
    def wants_tools(self) -> bool:
        return bool(self.calls)


def _iter_json_objects(text: str) -> Iterable[tuple[int, int, Any]]:
    """扫出 `text` 里所有能解析的顶层 JSON 对象，给出 (起, 止, 值)。

    用花括号配平而不是正则 —— 正则数不清嵌套，而模型的参数就是嵌套的。
    """
    depth = 0
    start = -1
    in_str = False
    escaped = False
    for i, ch in enumerate(text):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    chunk = text[start : i + 1]
                    try:
                        value = json.loads(chunk)
                    except json.JSONDecodeError:
                        start = -1
                        continue
                    yield start, i + 1, value
                    start = -1


def _as_arguments(raw: Any) -> dict[str, Any]:
    """`arguments` 有时是对象，有时是被塞成字符串的 JSON（OpenAI 那边就这样）。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ToolCallParseError(f"arguments 不是合法 JSON：{raw[:120]!r}（{exc}）") from exc
        if isinstance(parsed, dict):
            return parsed
    raise ToolCallParseError(f"arguments 既不是对象也不是 JSON 字符串：{raw!r}")


def _call_from_mapping(value: Any, known_tools: Container[str]) -> ToolCall | None:
    """`{"name": …, "arguments": …}` → `ToolCall`；不是这个形状就返回 None。

    **`arguments` 是必须的。** 计划里的步骤也可能带 `name`，但不会带 `arguments` ——
    少了这一条，一段计划就会被当成命令执行。
    """
    if not isinstance(value, dict):
        return None
    if "arguments" not in value:
        return None
    name = value.get("name")
    if not isinstance(name, str) or not name:
        return None
    # OpenAI 的结构化形态外面还套一层 {"type": "function", "function": {...}}
    if name not in known_tools:
        return None
    return ToolCall(name=name, arguments=_as_arguments(value["arguments"]))


def _parse_deepseek(content: str, known_tools: Container[str]) -> tuple[list[ToolCall], str]:
    calls: list[ToolCall] = []
    leftover = content
    attrs = re.finditer(
        re.escape(DEEPSEEK_CALLS_BEGIN) + r"(.*?)" + re.escape(DEEPSEEK_CALLS_END),
        content,
        flags=re.DOTALL,
    )
    for block in attrs:
        leftover = leftover.replace(block.group(0), " ")
        body = block.group(1)
        for part in body.split(DEEPSEEK_CALL_BEGIN)[1:]:
            part = part.split(DEEPSEEK_CALL_END)[0]
            # `<type><sep><name>` 之后才是参数
            if DEEPSEEK_SEP not in part:
                raise ToolCallParseError(
                    f"调用块里没有 {DEEPSEEK_SEP}，认不出工具名：{part[:120]!r}"
                )
            tail = part.split(DEEPSEEK_SEP, 1)[1]
            match = _NAME_RE.search(tail)
            if match is None:
                raise ToolCallParseError(f"调用块里找不到工具名：{tail[:120]!r}")
            name = match.group(0)
            objects = [v for _, _, v in _iter_json_objects(tail)]
            if not objects:
                raise ToolCallParseError(f"工具 {name!r} 的调用块里没有 JSON 参数：{tail[:120]!r}")
            arguments = objects[0]
            if not isinstance(arguments, dict):
                raise ToolCallParseError(f"工具 {name!r} 的参数不是 JSON 对象：{arguments!r}")
            calls.append(ToolCall(name=name, arguments=arguments))
    return calls, leftover


def _parse_structured(message: dict[str, Any], known_tools: Container[str]) -> list[ToolCall]:
    raw = message.get("tool_calls")
    if not raw:
        return []
    calls: list[ToolCall] = []
    for item in raw:
        fn = item.get("function") if isinstance(item, dict) else None
        if not isinstance(fn, dict):
            raise ToolCallParseError(f"tool_calls 里有一项没有 function：{item!r}")
        name = fn.get("name")
        if not isinstance(name, str) or not name:
            raise ToolCallParseError(f"tool_calls 里有一项没有 name：{item!r}")
        if name not in known_tools:
            raise ToolCallParseError(f"模型点名了未注册的工具 {name!r}，不假装它存在")
        call_id = item.get("id")
        calls.append(
            ToolCall(
                name=name,
                arguments=_as_arguments(fn.get("arguments", {})),
                id=call_id if isinstance(call_id, str) and call_id else None,
            )
        )
    return calls


def parse_reply(
    content: str,
    *,
    known_tools: Container[str],
    message: dict[str, Any] | None = None,
) -> ParsedReply:
    """把一次回执解读成「要调哪些工具」+「剩下的话」。

    `known_tools` 是判据的一部分：**`name` 不认识就不是调用**。
    这挡住的是最危险的一种失败 —— 模型编一个不存在的工具，我们照单执行。
    """
    if message:
        structured = _parse_structured(message, known_tools)
        if structured:
            return ParsedReply(calls=tuple(structured), text="", shape="structured")

    text = content or ""

    if DEEPSEEK_CALLS_BEGIN in text or DEEPSEEK_CALL_BEGIN in text:
        calls, leftover = _parse_deepseek(text, known_tools)
        if calls:
            return ParsedReply(calls=tuple(calls), text=leftover.strip(), shape="deepseek")
        raise ToolCallParseError(
            f"出现了 {DEEPSEEK_CALLS_BEGIN} 标记，却没能解出任何调用。原文：{text[:200]!r}"
        )

    # 裸 JSON / ```json 围栏：只有「name 命中已注册工具 + 带 arguments」才算调用
    hits: list[tuple[int, int, ToolCall]] = []
    for start, end, value in _iter_json_objects(text):
        inner = value.get("function") if isinstance(value, dict) else None
        call = _call_from_mapping(value, known_tools) or _call_from_mapping(inner, known_tools)
        if call is not None:
            hits.append((start, end, call))

    if hits:
        leftover = text
        for start, end, _ in reversed(hits):
            leftover = leftover[:start] + " " + leftover[end:]
        return ParsedReply(
            calls=tuple(c for _, _, c in hits),
            text=leftover.strip(),
            shape="fenced",
        )

    if any(marker in text for marker in (QWEN_CALL_BEGIN,)):
        raise ToolCallParseError(f"出现了 {QWEN_CALL_BEGIN} 标记，却没解出合法调用。原文：{text[:200]!r}")

    return ParsedReply(calls=(), text=text.strip(), shape="text")


def looks_like_attempt(content: str) -> bool:
    """回执里有没有「想调工具」的痕迹 —— 给循环判断要不要重试用。"""
    return any(marker in (content or "") for marker in ATTEMPT_MARKERS)
