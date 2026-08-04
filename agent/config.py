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
    stop_loss_usdt: float = Field(default=25.0, gt=0)
    pending_signal_ttl_minutes: int = Field(default=30, ge=1)

    # Agent
    agent_tick_interval: int = Field(default=300, ge=10)
    trade_symbol: str = "BTCUSDT"
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
    api_token: str = ""
    encryption_key: str = ""

    # M8 — 通知与模拟门禁
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    notify_on_session_close: bool = True
    notify_on_risk_reject: bool = True
    trading_mode: Literal["demo", "live"] = "demo"
    paper_validation_min_hours: float = Field(default=24.0, gt=0)
    paper_validation_require_loop: bool = True
    futures_enabled: bool = False
    live_trading_confirmed: bool = False

    # 邮件通知（MVP）
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    notify_email_to: str = ""
    notify_email_from: str = ""

    # 会话快照与 K 线（MVP）
    session_snapshot_interval_minutes: int = Field(default=15, ge=1)
    klines_enabled: bool = True
    klines_interval: str = "1m"
    default_trade_market: Literal["spot", "futures_u", "futures_coin"] = "spot"
    metrics_enabled: bool = True

    @field_validator("llm_auto_execute", mode="before")
    @classmethod
    def parse_bool(cls, value: object) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @field_validator(
        "notify_on_session_close",
        "notify_on_risk_reject",
        "paper_validation_require_loop",
        "futures_enabled",
        "klines_enabled",
        "smtp_use_tls",
        "metrics_enabled",
        "live_trading_confirmed",
        mode="before",
    )
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
    def api_auth_enabled(self) -> bool:
        return bool(self.api_token.strip())

    @property
    def encryption_configured(self) -> bool:
        return bool(self.encryption_key.strip())

    @property
    def email_configured(self) -> bool:
        return bool(
            self.smtp_host.strip()
            and self.notify_email_to.strip()
            and (self.smtp_user.strip() or self.notify_email_from.strip())
        )

    @property
    def database_backend(self) -> str:
        url = self.database_url.lower()
        if url.startswith("sqlite"):
            return "sqlite"
        if "postgresql" in url or url.startswith("postgres"):
            return "postgresql"
        return "unknown"


_effective_settings: Settings | None = None


@lru_cache
def _load_base_settings() -> Settings:
    return Settings()


def get_settings() -> Settings:
    if _effective_settings is not None:
        return _effective_settings
    return _load_base_settings()


def set_effective_settings(settings: Settings) -> None:
    global _effective_settings
    _effective_settings = settings


def clear_settings_cache() -> None:
    global _effective_settings
    _effective_settings = None
    _load_base_settings.cache_clear()
