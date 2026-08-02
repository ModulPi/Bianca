from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8-sig",
        extra="ignore",
    )

    # Binance Demo spot
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_demo_base_url: str = "https://demo-api.binance.com"
    # 大陆等地区需代理；Docker 内常用 http://host.docker.internal:7890
    binance_proxy: str = ""

    # LLM
    llm_provider: Literal["deepseek", "ollama"] = "deepseek"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    llm_auto_execute: bool = True
    llm_timeout: float = Field(default=30.0, gt=0)
    # auto | semi_auto | signal_only（未设时由 LLM_AUTO_EXECUTE 推导）
    execution_mode: Literal["auto", "semi_auto", "signal_only"] | None = None

    # Risk
    max_trade_amount: float = Field(default=50.0, gt=0)
    daily_loss_limit: float = Field(default=100.0, gt=0)
    min_confidence: float = Field(default=0.6, ge=0, le=1)
    max_position_pct: float = Field(default=0.8, gt=0, le=1)
    max_drawdown_usdt: float = Field(default=50.0, gt=0)
    circuit_breaker_failures: int = Field(default=3, ge=1)
    pending_signal_ttl_minutes: int = Field(default=30, ge=1)

    # Agent
    agent_tick_interval: int = Field(default=300, ge=10)
    trade_symbol: str = "BTCUSDT"
    # K 线采集（仅 PostgreSQL schema_mode=mvp 时写入）
    kline_collector_enabled: bool = True
    kline_interval: str = "1m"
    kline_symbols: str = ""
    kline_fetch_limit: int = Field(default=100, ge=1, le=1000)
    # conservative | aggressive — PoC 激进模式优先完成买卖闭环（模拟盘）
    trading_style: Literal["conservative", "aggressive"] = "conservative"
    poc_min_trade_usdt: float = Field(default=10.0, gt=0)

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/bianca.db"
    redis_url: str = ""

    # Server
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "INFO"

    # M8 — 通知与模拟门禁
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    notify_on_session_close: bool = True
    notify_on_risk_reject: bool = True
    trading_mode: Literal["demo", "live"] = "demo"
    paper_validation_min_hours: float = Field(default=24.0, gt=0)
    paper_validation_require_loop: bool = True
    futures_enabled: bool = False

    @field_validator("llm_auto_execute", mode="before")
    @classmethod
    def parse_bool(cls, value: object) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @field_validator("notify_on_session_close", "notify_on_risk_reject", "paper_validation_require_loop", "futures_enabled", "kline_collector_enabled", mode="before")
    @classmethod
    def parse_notify_bool(cls, value: object) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def resolved_execution_mode(self) -> Literal["auto", "semi_auto", "signal_only"]:
        if self.execution_mode:
            return self.execution_mode
        return "auto" if self.llm_auto_execute else "signal_only"

    @property
    def llm_auto_execute_effective(self) -> bool:
        return self.resolved_execution_mode != "signal_only"

    @property
    def data_dir(self) -> Path:
        return Path("data")

    @property
    def binance_configured(self) -> bool:
        return bool(self.binance_api_key.strip() and self.binance_api_secret.strip())

    @property
    def llm_configured(self) -> bool:
        if self.llm_provider == "ollama":
            return bool(self.llm_base_url.strip() and self.llm_model.strip())
        return bool(self.llm_api_key.strip())

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token.strip() and self.telegram_chat_id.strip())

    @property
    def redis_configured(self) -> bool:
        return bool(self.redis_url.strip())

    @property
    def database_backend(self) -> str:
        url = self.database_url.lower()
        if url.startswith("sqlite"):
            return "sqlite"
        if "postgresql" in url or url.startswith("postgres"):
            return "postgresql"
        return "unknown"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
