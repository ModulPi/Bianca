"""经理循环：目标 → 调只读工具 → 计划（阶段 B-3）。

**这一层最要紧的规矩：绝不假装工具跑过。**

§5.1 里那个本地模型编了一段调用不存在的 `get_balance` 的 Python，然后「执行」它，
报出「你的当前BTC余额是：0.00 BTC」。下游看到的是一个数字，不是一句猜测 ——
这正是本项目最难发现的一类失败。所以在这里：

  · 调不通（工具不存在 / 参数不合 schema / handler 抛错）→ **把错误原样回给模型**，
    它有机会改；**绝不塞一个编出来的结果**进去。
  · 解析不出它在调什么 → 也回一条明确的错误，同样不猜。
  · 轮次用尽还没拿到计划 → **如实返回没有计划**，不硬凑一个。

对外只暴露 `Manager.plan()`，返回的 `ManagerRun` 里带着**实际发出去的每一条 message**，
落库/核对时有据可查（ADR-019 R1：完整 prompt，不是摘要）。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from agent.config import Settings, get_settings
from agent.llm.provider import LLMEndpoint, resolve_llm_endpoint
from agent.manager.parser import ParsedReply, ToolCallParseError, parse_reply
from agent.manager.prompt import (
    build_decision_prompt,
    build_system_prompt,
    render_decision_context,
)
from agent.manager.readonly import build_readonly_registry
from agent.manager.schemas import ManagerDecision, Plan, parse_decision, parse_plan
from agent.manager.tools import ToolError, ToolRegistry
from agent.storage.repository import DecisionRepository

logger = logging.getLogger(__name__)

MAX_ROUNDS = 6

# 工具结果用哪个 role 回给模型。本地模型的 DeepSeek 模板专门渲染 `role='tool'`，
# 所以默认走它；`tool_call_id` 是对不上任何真实调用时给一个占位，只为满足
# OpenAI 兼容层的形状要求。
_TOOL_ROLE = "tool"

_RETRY_PLAN = (
    "你这轮没有调用工具，也没有给出计划。请二选一：\n"
    "  1. 需要更多信息 —— 按约定的格式调用一个工具；\n"
    "  2. 信息够了 —— 只输出下面这个 JSON，不要有别的内容：\n"
    '{"objective": "…", "steps": [{"what": "…", "why": "…"}], '
    '"stop_conditions": ["…"], "assumptions": ["…"], "risks": ["…"]}'
)


@dataclass
class Reply:
    content: str
    message: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] | None = None


@dataclass
class ToolInvocation:
    """一次工具调用与它的结局。`ok=False` 的那条**必须**留着 —— 它是「没跑成」的证据。"""

    name: str
    arguments: dict[str, Any]
    ok: bool
    result: Any = None
    error: str | None = None
    round: int = 0


@dataclass
class ManagerRun:
    goal: str
    plan: Plan | None
    plan_shape: str  # "json" | "text" | "none"
    final_text: str
    transcript: list[dict[str, Any]] = field(default_factory=list)
    invocations: list[ToolInvocation] = field(default_factory=list)
    rounds: int = 0
    usage: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    # 每一轮模型是用哪种形状回的。**别省这一栏**：实测里第 1 轮它没用 `tools`
    # 字段而是在正文里写原生格式，是这一栏才让人看出来的（§6.4.3）。
    shapes: list[str] = field(default_factory=list)

    @property
    def used_tools(self) -> list[str]:
        return [i.name for i in self.invocations]


@dataclass
class DecisionRun:
    """一次执行决策的完整痕迹。

    `decision` **永远不是 None**：解不出来时它是兜底的 HOLD，而理由写在
    `errors` / `decision.reason` 里。下游不该出现"这一轮没有决策"这种状态 ——
    tick 必须做点什么，哪怕就是不动。
    """

    goal: str
    decision: ManagerDecision | None
    final_text: str = ""
    parse_error: str | None = None
    transcript: list[dict[str, Any]] = field(default_factory=list)
    invocations: list[ToolInvocation] = field(default_factory=list)
    rounds: int = 0
    usage: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    shapes: list[str] = field(default_factory=list)
    decision_id: str | None = None

    @property
    def used_tools(self) -> list[str]:
        return [i.name for i in self.invocations]


ChatFn = Callable[[list[dict[str, Any]]], Awaitable[Reply]]


class Manager:
    def __init__(
        self,
        registry: ToolRegistry | None = None,
        *,
        settings: Settings | None = None,
        chat: ChatFn | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._registry = registry or build_readonly_registry(self._settings)
        self._endpoint = resolve_llm_endpoint(self._settings)
        self._chat = chat or self._http_chat

    @property
    def endpoint(self) -> LLMEndpoint:
        return self._endpoint

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    # -- 与模型说话 ---------------------------------------------------------

    async def _http_chat(self, messages: list[dict[str, Any]]) -> Reply:
        """带上 `tools` 字段 —— **但别以为它管用。**

        §6.4.1 曾以为「换官方 API 之后就能走原生通道」，**§6.4.3 的实测推翻了它**：
        对 `deepseek-flash` 连发 `tools` + `tool_choice=auto`，它照样把调用写在
        `content` 正文里（`shape='deepseek'`），从没填过 `tool_calls`。
        真正扛事的一直是 `parser.py` 那层客户端解析 —— 从 B-1 到现在都是。

        **那为什么还留着？** 因为它几乎不要钱（这部分 prompt 命中缓存），
        而一旦换到真会填 `tool_calls` 的模型，`_parse_structured` 那条通道会自动生效，
        不用改代码。留着的是**可能性**，不是**能力**。
        谁要摘掉它，先去翻 §6.4.3，别照着 §6.4.1 的旧说法做决定。
        """
        payload = {
            "model": self._endpoint.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 2048,
            "tools": self._registry.schemas(),
            "tool_choice": "auto",
        }
        headers = {"Content-Type": "application/json"}
        if self._endpoint.api_key:
            headers["Authorization"] = f"Bearer {self._endpoint.api_key}"

        async with httpx.AsyncClient(timeout=self._endpoint.timeout) as client:
            resp = await client.post(
                self._endpoint.chat_completions_url, headers=headers, json=payload
            )
            resp.raise_for_status()
            data = resp.json()
        message = (data.get("choices") or [{}])[0].get("message") or {}
        return Reply(
            content=message.get("content") or "",
            message=message,
            usage=data.get("usage"),
        )

    # -- 主循环 -------------------------------------------------------------

    async def plan(self, goal: str, *, max_rounds: int = MAX_ROUNDS) -> ManagerRun:
        if not goal or not goal.strip():
            raise ValueError("目标不能是空的 —— 一个没有目标的目标没法拆解")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": build_system_prompt(self._registry)},
            {"role": "user", "content": goal.strip()},
        ]
        run = ManagerRun(goal=goal.strip(), plan=None, plan_shape="none", final_text="")
        usage: dict[str, Any] | None = None
        transport_failed: str | None = None

        for round_no in range(1, max_rounds + 1):
            run.rounds = round_no
            try:
                reply = await self._chat(list(messages))
            except httpx.HTTPError as exc:
                # 连不上 / 超时 / 非 2xx。本地模型 8 t/s，超时是常态，不是意外。
                # **这跟「模型没给计划」是两回事，不能混成一句。**
                transport_failed = f"跟模型说话失败（{type(exc).__name__}）：{exc}"
                logger.warning(transport_failed)
                run.errors.append(transport_failed)
                break
            if reply.usage:
                usage = reply.usage

            parsed, parse_error = self._interpret(reply)
            run.shapes.append(parsed.shape if parsed is not None else "unparsed")
            if parse_error is not None:
                run.errors.append(parse_error)
                messages.append({"role": "assistant", "content": reply.content})
                messages.append({"role": "user", "content": parse_error})
                continue

            assert parsed is not None
            if parsed.calls:
                self._append_assistant(messages, reply, parsed)
                for call in parsed.calls:
                    observation = await self._invoke(call, round_no, run)
                    messages.append(self._observation_message(call, round_no, observation))
                continue

            # 没有调用 —— 该给计划了
            plan = parse_plan(parsed.text)
            if plan is not None:
                run.plan = plan
                run.plan_shape = "json"
                run.final_text = parsed.text
                run.transcript = messages + [{"role": "assistant", "content": reply.content}]
                run.usage = usage
                return run

            run.final_text = parsed.text
            messages.append({"role": "assistant", "content": reply.content})
            if round_no == max_rounds:
                break
            messages.append({"role": "user", "content": _RETRY_PLAN})

        # 轮次用尽仍没有计划：**如实返回**，不拿最后一段散文冒充
        run.plan_shape = "text" if run.final_text else "none"
        if transport_failed is None:
            run.errors.append(f"{max_rounds} 轮之内没有拿到结构化计划")
        run.transcript = messages
        run.usage = usage
        return run

    async def decide(
        self,
        brief: str,
        plan: Plan | None,
        state: Mapping[str, Any],
        *,
        max_rounds: int = MAX_ROUNDS,
        goal_id: str | None = None,
        plan_id: str | None = None,
        decisions: DecisionRepository | None = None,
    ) -> DecisionRun:
        """执行阶段的一轮决策：调只读工具，最后给一个信号（§6.6 D-2）。

        与 `plan()` 的三处不同，都是有意的：

        1. **解不出来就是 HOLD，不再追问。** 计划可以来回改；执行动作不行 ——
           为了让模型"再想想"而多花一轮，等于在没有决策的情况下继续持有敞口。
           失败原因原样记录，下一次 tick 它还有机会。
        2. **信号必须完整。** 说 BUY 却没给 amount 的，不拿上限替它补 —— 补了
           就是系统替模型下单。这种回复同样落成 HOLD 并记原因。
        3. **决策落 `decision_logs`**（R1）。HOLD 与解析失败也落：复盘最常问的
           恰恰是"为什么没动手"。
        """
        if not brief or not brief.strip():
            raise ValueError("目标不能是空的 —— 没有目标的执行不是执行")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": build_decision_prompt(self._registry)},
            {"role": "user", "content": render_decision_context(brief, plan, state)},
        ]
        run = DecisionRun(goal=brief.strip(), decision=None)
        usage: dict[str, Any] | None = None
        raw_text = ""
        transport_failed: str | None = None

        for round_no in range(1, max_rounds + 1):
            run.rounds = round_no
            try:
                reply = await self._chat(list(messages))
            except httpx.HTTPError as exc:
                transport_failed = f"跟模型说话失败（{type(exc).__name__}）：{exc}"
                logger.warning(transport_failed)
                run.errors.append(transport_failed)
                break
            if reply.usage:
                usage = reply.usage
            raw_text = reply.content

            parsed, parse_error = self._interpret(reply)
            run.shapes.append(parsed.shape if parsed is not None else "unparsed")
            if parse_error is not None:
                run.errors.append(parse_error)
                messages.append({"role": "assistant", "content": reply.content})
                messages.append({"role": "user", "content": parse_error})
                continue

            assert parsed is not None
            if parsed.calls:
                self._append_assistant(messages, reply, parsed)
                for call in parsed.calls:
                    observation = await self._invoke(call, round_no, run)
                    messages.append(self._observation_message(call, round_no, observation))
                continue

            # 不再追问：这一轮的正文就是最终答复，解不出来按 HOLD 落。
            decision = parse_decision(parsed.text)
            if decision is None:
                run.parse_error = (
                    "这一轮的回复不是可用的信号 JSON，按 HOLD 处理："
                    f"{parsed.text[:200]!r}"
                )
            else:
                unusable = decision.unusable_reason()
                if unusable:
                    run.parse_error = f"信号不完整，按 HOLD 处理：{unusable}"
                else:
                    run.decision = decision
                    run.final_text = parsed.text
                    run.transcript = messages + [{"role": "assistant", "content": reply.content}]
                    break
            run.errors.append(run.parse_error)
            messages.append({"role": "assistant", "content": reply.content})
            break
        else:
            run.errors.append(f"{max_rounds} 轮之内没有拿到信号")

        if run.decision is None:
            why = run.parse_error or transport_failed or f"{max_rounds} 轮之内没有拿到信号"
            run.decision = ManagerDecision(
                action="HOLD", reason=f"经理没有给出可用信号（{why}），按 HOLD 处理"
            )
            run.transcript = messages
            run.final_text = raw_text

        run.usage = usage
        await self._save_decision(
            run, state=state, goal_id=goal_id, plan_id=plan_id, decisions=decisions
        )
        return run

    async def _save_decision(
        self,
        run: DecisionRun,
        *,
        state: Mapping[str, Any],
        goal_id: str | None,
        plan_id: str | None,
        decisions: DecisionRepository | None,
    ) -> None:
        """落决策。**这一条写不进去，就不该继续交易** —— 没有记录的自动化没法复盘。"""
        assert run.decision is not None
        run.decision_id = uuid.uuid4().hex
        usage = run.usage or {}
        repo = decisions or DecisionRepository()
        await repo.save(
            decision_id=run.decision_id,
            model_used=self._endpoint.model,
            prompt_summary=f"{run.decision.action} {run.decision.symbol or '-'}：{run.decision.reason}",
            raw_output=run.final_text or "",
            parsed_signal=run.decision.model_dump(),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            prompt=run.transcript,
            market_data=state.get("market_data") if state else None,
            goal_id=goal_id,
            plan_id=plan_id,
            plan_step=run.decision.plan_step,
        )

    # -- 内部 ---------------------------------------------------------------

    def _interpret(self, reply: Reply) -> tuple[ParsedReply | None, str | None]:
        """把回执解读成调用；解不出来时返回一段**给模型看的**错误说明。"""
        try:
            return (
                parse_reply(
                    reply.content,
                    known_tools=set(self._registry.names()),
                    message=reply.message,
                ),
                None,
            )
        except ToolCallParseError as exc:
            logger.warning("经理的工具调用解析失败：%s", exc)
            return None, (
                f"你上一轮的回复里像是在调用工具，但按约定解析不出来：{exc}。"
                "请严格按约定的格式重新调用，或者直接给出计划。"
            )

    def _append_assistant(
        self, messages: list[dict[str, Any]], reply: Reply, parsed: ParsedReply
    ) -> None:
        """把这一轮的 assistant 消息挂上去。

        走原生 `tools` 通道时，**服务端会拿它按 `tool_calls[].id` 跟后面的
        `role: "tool"` 配对** —— 所以得原样带上，不能只留 content。
        """
        message: dict[str, Any] = {"role": "assistant", "content": reply.content or ""}
        if parsed.shape == "structured" and reply.message.get("tool_calls"):
            message["tool_calls"] = reply.message["tool_calls"]
        messages.append(message)

    def _observation_message(
        self, call: Any, round_no: int, observation: dict[str, Any]
    ) -> dict[str, Any]:
        """工具观测结果怎么回给模型。

        有服务端发的 `id` 就按 `role: "tool"` 回；**没有就退回 `role: "user"` 加前缀** ——
        绝不自己编一个 `tool_call_id` 去冒充。编出来的号在服务端对不上任何一条
        `tool_calls`，轻则 400，重则被当成真发生过的调用。
        """
        body = json.dumps(observation, ensure_ascii=False)
        if call.id:
            return {"role": _TOOL_ROLE, "tool_call_id": call.id, "content": body}
        return {"role": "user", "content": f"工具 {call.name} 的结果：{body}"}

    async def _invoke(self, call: Any, round_no: int, run: ManagerRun) -> dict[str, Any]:
        """执行一次调用。**失败也要返回一条明确的 observation，不返回编造的成功。**"""
        try:
            result = await self._registry.call(call.name, call.arguments)
        except ToolError as exc:
            # 工具不存在 / 参数不合 schema —— 原样告诉模型，让它改
            run.invocations.append(
                ToolInvocation(
                    name=call.name, arguments=call.arguments, ok=False,
                    error=str(exc), round=round_no,
                )
            )
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 — handler 内部炸了也不能假装有结果
            logger.exception("工具 %s 执行失败", call.name)
            run.invocations.append(
                ToolInvocation(
                    name=call.name, arguments=call.arguments, ok=False,
                    error=f"{type(exc).__name__}: {exc}", round=round_no,
                )
            )
            return {"ok": False, "error": f"工具执行失败：{exc}"}

        run.invocations.append(
            ToolInvocation(name=call.name, arguments=call.arguments, ok=True,
                           result=result, round=round_no)
        )
        return {"ok": True, "tool": call.name, "result": result}
