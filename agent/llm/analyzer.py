from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from agent.config import Settings, get_settings
from agent.llm.prompts import build_user_prompt, get_system_prompt, summarize_market_for_log
from agent.llm.provider import LLMEndpoint, resolve_llm_endpoint
from agent.llm.schemas import TradeSignal

logger = logging.getLogger(__name__)


@dataclass
class ChatResult:
    """一次 LLM 调用的结果：正文 + 供应商返回的 usage（token 消耗）。"""

    content: str
    usage: dict[str, Any] | None = None


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
        max_tokens: int = 512,
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

    async def analyze(
        self, market_data: dict[str, Any]
    ) -> tuple[TradeSignal, str, str, dict[str, int] | None]:
        """
        Run analysis on market snapshot.
        Returns (signal, raw_output, prompt_summary, usage). usage 在调用失败时为 None。
        """
        symbol = market_data.get("symbol") or self._settings.trade_symbol
        user_prompt = build_user_prompt(
            market_data,
            max_trade_amount=self._settings.max_trade_amount,
            trade_symbol=self._settings.trade_symbol,
            trading_style=self._settings.trading_style,
            min_trade_usdt=self._settings.poc_min_trade_usdt,
        )
        prompt_summary = summarize_market_for_log(market_data)

        try:
            result = await self._chat(
                [
                    {"role": "system", "content": get_system_prompt(self._settings)},
                    {"role": "user", "content": user_prompt},
                ]
            )
            signal = parse_trade_signal(result.content, default_symbol=symbol)
            return signal, result.content, prompt_summary, result.usage
        except httpx.TimeoutException:
            logger.warning("LLM request timed out, defaulting to HOLD")
            signal = HOLD_ON_FAILURE.model_copy(
                update={"symbol": symbol, "reason": "LLM 请求超时，降级为 HOLD"}
            )
            return signal, "", prompt_summary, None
        except Exception as exc:  # noqa: BLE001
            logger.exception("LLM analysis failed: %s", exc)
            signal = HOLD_ON_FAILURE.model_copy(
                update={"symbol": symbol, "reason": f"LLM 调用失败: {exc}"}
            )
            return signal, str(exc), prompt_summary, None


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
        hint = (
            "Ollama: 设置 LLM_PROVIDER=ollama、LLM_BASE_URL=http://host.docker.internal:11434、LLM_MODEL=..."
            if cfg.llm_provider == "ollama"
            else "DeepSeek: 设置 LLM_API_KEY，或通过 /api/v1/secrets/keys 写入 llm 类型密钥"
        )
        return {"status": "not_configured", "detail": hint}
    try:
        analyzer = MarketAnalyzer(cfg)
        reply = await analyzer.ping()
        return {
            "status": "ok",
            "detail": f"{cfg.llm_provider} @ {cfg.llm_base_url} ({cfg.llm_model}) ping={reply[:32] or 'empty-content-ok'}",
        }
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
        if cfg.llm_provider == "ollama" and (
            "Connect" in detail or "connection" in detail.lower() or "111" in detail
        ):
            detail += (
                " — Docker 内访问宿主机 Ollama 请用 LLM_BASE_URL=http://host.docker.internal:11434 "
                "并确认 ollama serve 已启动、模型已 pull"
            )
        return {"status": "error", "detail": detail}


async def check_ollama(settings: Settings | None = None) -> dict[str, str]:
    cfg = settings or get_settings()
    if cfg.llm_provider != "ollama":
        return {"status": "skipped", "detail": f"LLM_PROVIDER={cfg.llm_provider}"}
    return await check_llm(cfg)
