"""行情数据模块（数据面）。

采集 / 存储 / 加工 / 供数 / 观测五层，独立于 Agent 控制面。
设计文档：docs/outline-design/模块设计/行情数据模块设计-Bianca.md
"""

from agent.market.storage import close_market_db, init_market_db

__all__ = ["init_market_db", "close_market_db"]
