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
    llm_model: str = "deepseek-chat"
    llm_auto_execute: bool = True
    llm_timeout: float = Field(default=30.0, gt=0)

    # Risk
    max_trade_amount: float = Field(default=50.0, gt=0)
    daily_loss_limit: float = Field(default=100.0, gt=0)

    # Agent
    agent_tick_interval: int = Field(default=300, ge=10)
    trade_symbol: str = "BTCUSDT"

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/bianca.db"

    # Server
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "INFO"

    @field_validator("llm_auto_execute", mode="before")
    @classmethod
    def parse_bool(cls, value: object) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

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


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
