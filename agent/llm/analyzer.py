from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from agent.config import Settings, get_settings
from agent.llm.prompts import SYSTEM_PROMPT, build_user_prompt, summarize_market_for_log
from agent.llm.provider import LLMEndpoint, resolve_llm_endpoint
from agent.llm.schemas import TradeSignal

logger = logging.getLogger(__name__)


@dataclass
class ChatResult:
    """一次 LLM 调用的结果：正文 + 供应商返回的 usage（token 消耗）。"""

    content: str
    usage: dict[str, Any] | None = None


@dataclass
class AnalysisRun:
    """一次分析的**全部**输入与输出（ADR-019 / ADR-031）。

    比一个四元组多的那两样，正是归因要的：`messages` 是**实际发出**的完整
    prompt（重试过的话就是重试那份），`variant` 说明用的是哪一份 ——
    否则事后看到 raw_output 也判断不出模型是被怎么问的。
    """

    signal: TradeSignal
    raw_output: str
    prompt_summary: str
    usage: dict[str, int] | None = None
    messages: list[dict[str, str]] | None = None
    variant: str = "normal"


HOLD_ON_FAILURE = TradeSignal(
    action="HOLD",
    symbol="BTCUSDT",
    amount=None,
    confidence=0.0,
    reason="LLM 输出解析失败或调用异常，降级为 HOLD",
)


class MarketAnalyzer:
    """OpenAI-compatible chat client for market analysis."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._endpoint = resolve_llm_endpoint(self._settings)

    @property
    def endpoint(self) -> LLMEndpoint:
        return self._endpoint

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._endpoint.api_key:
            headers["Authorization"] = f"Bearer {self._endpoint.api_key}"
        return headers

    async def _chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 4096,
        require_content: bool = True,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": self._endpoint.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        # DeepSeek 要求启用 json_object 时 prompt 必须出现 "json" 字样，否则 400
        if self._endpoint.provider == "deepseek" and any(
            "json" in (m.get("content") or "").lower() for m in messages
        ):
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=self._endpoint.timeout) as client:
            response = await client.post(
                self._endpoint.chat_completions_url,
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            if require_content:
                raise ValueError("Empty LLM response content")
            return ChatResult("")
        return ChatResult(content=content.strip(), usage=data.get("usage"))

    async def ping(self) -> str:
        """Lightweight LLM reachability check.

        OK = 收到 HTTP 200。推理模型可能把 token 全耗在 reasoning_content，
        导致 content 为空，此时不应判定为不可达。
        """
        result = await self._chat(
            [
                {"role": "system", "content": "Reply with exactly: ok"},
                {"role": "user", "content": "ping"},
            ],
            max_tokens=64,
            require_content=False,
        )
        return result.content.strip()

    async def analyze(self, market_data: dict[str, Any]) -> AnalysisRun:
        """跑一次分析。返回 `AnalysisRun` —— 含**实际发出去**的 messages。

        失败路径也要带 messages：超时那一笔恰恰最需要事后看到"我们问了什么、
        多久没回"。没有 prompt 的失败记录等于什么都没记（ADR-019 / ADR-031）。
        """
        symbol = market_data.get("symbol") or self._settings.trade_symbol
        user_prompt = build_user_prompt(
            market_data,
            max_trade_amount=self._settings.max_trade_amount,
            trade_symbol=self._settings.trade_symbol,
        )
        prompt_summary = summarize_market_for_log(market_data)

        messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        variant = "normal"

        def _run(signal: TradeSignal, raw: str, usage: dict[str, int] | None) -> AnalysisRun:
            # messages 是**实际最后发出**的那一份：重试过的话，这里就是重试那份
            return AnalysisRun(
                signal=signal,
                raw_output=raw,
                prompt_summary=prompt_summary,
                usage=usage,
                messages=messages,
                variant=variant,
            )

        try:
            result = await self._chat(messages, require_content=False)
            if not result.content.strip():
                # 推理模型常把 token 全耗在思考上导致 content 为空：提示简短作答再试一次
                logger.warning("LLM reasoning exhausted tokens, retrying with brevity hint")
                brevity_system = SYSTEM_PROMPT + "\n\nThink very briefly, then output only the JSON."
                messages = [
                    {"role": "system", "content": brevity_system},
                    {"role": "user", "content": user_prompt},
                ]
                variant = "brevity"
                result = await self._chat(messages, require_content=False)
            if not result.content.strip():
                raise ValueError("Empty LLM response content")
            signal = parse_trade_signal(result.content, default_symbol=symbol)
            return _run(signal, result.content, result.usage)
        except httpx.TimeoutException:
            logger.warning("LLM request timed out, defaulting to HOLD")
            signal = HOLD_ON_FAILURE.model_copy(
                update={"symbol": symbol, "reason": "LLM 请求超时，降级为 HOLD"}
            )
            return _run(signal, "", None)
        except Exception as exc:  # noqa: BLE001
            logger.exception("LLM analysis failed: %s", exc)
            signal = HOLD_ON_FAILURE.model_copy(
                update={"symbol": symbol, "reason": f"LLM 调用失败: {exc}"}
            )
            return _run(signal, str(exc), None)


def parse_trade_signal(raw: str, *, default_symbol: str) -> TradeSignal:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("LLM output is not a JSON object")

    data.setdefault("symbol", default_symbol)
    return TradeSignal.model_validate(data)


async def check_llm(settings: Settings | None = None) -> dict[str, str]:
    cfg = settings or get_settings()
    if not cfg.llm_configured:
        return {
            "status": "not_configured",
            "detail": "LLM_API_KEY not set (DeepSeek) or base_url/model missing (Ollama)",
        }
    try:
        analyzer = MarketAnalyzer(cfg)
        reply = await analyzer.ping()
        return {
            "status": "ok",
            "detail": f"{cfg.llm_provider} @ {cfg.llm_base_url} ({cfg.llm_model}) ping={reply[:32]}",
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": str(exc)}
