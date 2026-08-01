from __future__ import annotations

from dataclasses import dataclass

from agent.config import Settings


@dataclass(frozen=True)
class LLMEndpoint:
    provider: str
    model: str
    chat_completions_url: str
    api_key: str | None
    timeout: float


def chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def resolve_llm_endpoint(settings: Settings) -> LLMEndpoint:
    url = chat_completions_url(settings.llm_base_url)
    if settings.llm_provider == "ollama":
        return LLMEndpoint(
            provider="ollama",
            model=settings.llm_model,
            chat_completions_url=url,
            api_key=None,
            timeout=settings.llm_timeout,
        )
    return LLMEndpoint(
        provider="deepseek",
        model=settings.llm_model,
        chat_completions_url=url,
        api_key=settings.llm_api_key.strip() or None,
        timeout=settings.llm_timeout,
    )
