from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8-sig",
        extra="ignore",
    )

    # Binance Demo spot
    # 密钥一律用 SecretStr。`Settings` 是会被传来传去的对象，它的 `repr` 会出现在
    # 断言消息、日志和调试打印里 —— 裸 `str` 迟早会被某一条日志原样打出来，
    # 而这件事**已经发生过**（2026-10-11：一条 pytest 断言把 `.env` 里的真 key
    # 打进了输出）。要原文就显式调 `.get_secret_value()`：读密钥的地方在代码里看得见，
    # 顺手把它塞进日志就没那么容易。
    binance_api_key: SecretStr = SecretStr("")
    binance_api_secret: SecretStr = SecretStr("")
    binance_demo_base_url: str = "https://demo-api.binance.com"
    # 大陆等地区需代理；Docker 内常用 http://host.docker.internal:7890
    binance_proxy: str = ""

    # LLM
    llm_provider: Literal["deepseek", "ollama"] = "deepseek"
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"
    llm_auto_execute: bool = True
    llm_timeout: float = Field(default=30.0, gt=0)

    # Risk
    max_trade_amount: float = Field(default=50.0, gt=0)
    daily_loss_limit: float = Field(default=100.0, gt=0)
    # A-3：止损/回撤/仓位上限。见设计文档 §6.2 与 ADR-026~029。
    # 止损基准是"从观测高点回撤"，不是成本价 —— 我们没有可信成本价（ADR-026）。
    stop_loss_pct: float = Field(default=0.10, gt=0, lt=1)
    stop_lookback_hours: int = Field(default=24, ge=1, le=720)
    max_drawdown_pct: float = Field(default=0.05, gt=0, lt=1)
    max_position_pct: float = Field(default=0.50, gt=0, le=1)
    # 会真的下平仓单，留一个能关的总闸（ADR-027）
    force_close_enabled: bool = True

    # Agent
    agent_tick_interval: int = Field(default=300, ge=10)
    trade_symbol: str = "BTCUSDT"

    # Market data (行情数据模块，见 docs/outline-design/模块设计/行情数据模块设计-Bianca.md)
    # 行情一律取实盘（ADR-013），交易仍走上面的 Binance Demo
    market_rest_base_url: str = "https://api.binance.com"
    market_ws_base_url: str = "wss://stream.binance.com:9443/ws"
    market_database_url: str = "sqlite+aiosqlite:///./data/market.db"
    market_symbols: str = "BTCUSDT"
    market_interval: str = "1m"
    market_retention_days: int = Field(default=3650, ge=0)
    market_backfill_start: str = "2017-08-17"
    market_backfill_on_start: bool = True
    market_backfill_concurrency: int = Field(default=5, ge=1, le=20)
    market_collector_autostart: bool = True
    market_max_context_chars: int = Field(default=2000, gt=0)

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/bianca.db"

    # Server
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "INFO"

    @field_validator(
        "llm_auto_execute",
        "market_backfill_on_start",
        "market_collector_autostart",
        mode="before",
    )
    @classmethod
    def parse_bool(cls, value: object) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def data_dir(self) -> Path:
        return Path("data")

    @property
    def market_symbol_list(self) -> list[str]:
        """采集层按「订阅集合」建模（ADR-016），当前通常只有一个元素。"""
        return [s.strip().upper() for s in self.market_symbols.split(",") if s.strip()]

    @property
    def market_proxy(self) -> str:
        """行情链路统一走 BINANCE_PROXY —— 实测直连 REST 超时，不做双路径。"""
        return self.binance_proxy.strip()

    @property
    def binance_configured(self) -> bool:
        return bool(
            self.binance_api_key.get_secret_value().strip()
            and self.binance_api_secret.get_secret_value().strip()
        )

    @property
    def llm_configured(self) -> bool:
        if self.llm_provider == "ollama":
            return bool(self.llm_base_url.strip() and self.llm_model.strip())
        return bool(self.llm_api_key.get_secret_value().strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
