"""订单状态机与成交对账（纯逻辑，不出网、不碰数据库）。

这一层存在的唯一理由是**不再猜**。旧实现用 `order.get("filled") or amount` 兜底
（`agent/graph/execute_agent.py`），把"交易所没给成交量"当成"按请求量成交" ——
一笔 0 成交的单会被记成 `filled`，而下游的日亏熔断正吃这个数。

原则：交易所没说的，我们不编。拿不到可信回执时状态是 `unknown`，而 `unknown`
**不是成交**，它会阻断重放（见 `is_terminal`）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

import ccxt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 本地状态

PENDING = "pending"  # 已登记，尚未提交
SUBMITTED = "submitted"  # 已提交，未确认终态
FILLED = "filled"  # 全部成交
PARTIAL = "partial"  # 有成交但未能确认收尾（对账耗尽）
CANCELED = "canceled"
REJECTED = "rejected"  # 交易所明确拒绝，或提交前就失败
UNKNOWN = "unknown"  # 提交了，但拿不到可信回执 —— 绝不等于成交

# 终态一旦落定就不再变。`unknown` 是终态是**刻意的**：它意味着"这笔单的下落
# 我们不知道"，此时重放会真的下第二单，所以必须停下来等人处理。
TERMINAL_STATUSES = frozenset({FILLED, PARTIAL, CANCELED, REJECTED, UNKNOWN})

# ccxt 统一订单状态 → 本地状态
_CCXT_STATUS_MAP = {
    "open": SUBMITTED,
    "closed": FILLED,
    "canceled": CANCELED,
    "cancelled": CANCELED,
    "rejected": REJECTED,
    "expired": CANCELED,
}


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


def new_client_order_id(seed: str) -> str:
    """由决策派生幂等键。**必须确定性**，不能用随机数。

    它是我们**自己账本的索引**，不是交易所给的保证。2026-10-11 在 Demo 实测过：
    币安的 clientOrderId 唯一性只约束**当前挂单** —— 挂单重复提交会被
    `-2010 Duplicate order sent.` 拒绝，但市价单成交后 id 立刻释放，同一个 cid
    再提交**会被接受**（实测两笔同 cid 的市价单先后成交）。市价单正是我们唯一
    使用的类型，所以交易所侧根本没有去重。

    挡住重放的是本地账本，加上提交前对交易所的一次回查
    （`agent/graph/execute_agent.py::_find_existing_order`）。

    确定性仍是必需的：随机 id 会让这套机制认不出自己人 —— 崩在"提交"与"落库"
    之间时，本地找不到原单、交易所也不拦，重放就真的开出第二笔。

    币安 `newClientOrderId` 限 `^[.A-Z:/a-z0-9_-]{1,36}$`，这里固定 35 字符。
    """
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:28]
    return f"bianca-{digest}"


# ---------------------------------------------------------------- 归一化


@dataclass(frozen=True)
class OrderUpdate:
    status: str
    exchange_order_id: str | None = None
    filled_qty: float | None = None
    avg_price: float | None = None
    cost_quote: float | None = None
    fee: str | None = None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_order(raw: dict[str, Any]) -> OrderUpdate:
    """把 ccxt 订单回执归一化。缺的字段留 None，不兜底、不估算。"""
    mapped = _CCXT_STATUS_MAP.get(str(raw.get("status") or "").lower(), SUBMITTED)
    filled = _as_float(raw.get("filled"))
    fee = raw.get("fee")
    return OrderUpdate(
        status=resolve_status(mapped, filled),
        exchange_order_id=str(raw["id"]) if raw.get("id") is not None else None,
        filled_qty=filled,
        avg_price=_as_float(raw.get("average")) or _as_float(raw.get("price")),
        cost_quote=_as_float(raw.get("cost")),
        fee=json.dumps(fee, ensure_ascii=False) if fee else None,
    )


def resolve_status(mapped: str, filled: float | None) -> str:
    """终态判定要带证据。

    交易所说 `closed` 却一个成交量都没给时，不能记 `filled` —— 那正是旧实现
    谎报的入口。退回 `submitted`，交给对账去问清楚。
    """
    if mapped == FILLED and (filled is None or filled <= 0):
        return SUBMITTED
    return mapped


def classify_submit_error(exc: BaseException) -> str:
    """提交失败的性质，决定我们敢不敢重放。

    明确拒绝（余额不足、参数非法、下单被拒）→ `rejected`：重放没有意义。
    网络类异常（超时、连接中断）→ `unknown`：**交易所可能已经收下了**，
    重放会下第二单，所以停下来等人处理。

    这两者用同一个错误处理路径是危险的默认值，必须分开。
    """
    if isinstance(exc, (ccxt.NetworkError, ccxt.RequestTimeout)):
        return UNKNOWN
    if isinstance(exc, ccxt.ExchangeError):
        return REJECTED
    return UNKNOWN


# ---------------------------------------------------------------- 对账


async def reconcile(
    fetch_order: Callable[[str], Awaitable[dict[str, Any]]],
    *,
    exchange_order_id: str,
    attempts: int = 5,
    delay: float = 1.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> OrderUpdate:
    """反复问交易所"这笔到底成没成"，直到终态或问不动为止。

    **不自算"成交了多少才算满"** —— 用交易所自己的终态（ADR-006 的同一思路：
    把判定权留在权威方）。对账耗尽仍有持仓时记 `partial`，完全问不出来记 `unknown`。

    最后一轮不再 sleep：省掉一次无意义的等待，也避免测试里多一次虚拟时钟推进。
    """
    last: OrderUpdate | None = None
    for attempt in range(attempts):
        try:
            last = normalize_order(await fetch_order(exchange_order_id))
        except Exception as exc:  # noqa: BLE001 — 对账失败不能带崩 tick
            logger.warning("fetch_order 失败（第 %s 次）: %s", attempt + 1, exc)
        else:
            if is_terminal(last.status):
                return last
        if attempt < attempts - 1:
            await sleep(delay)

    return _exhausted(last)


def _exhausted(last: OrderUpdate | None) -> OrderUpdate:
    """对账问不出结论时的收尾。有成交量 → partial，完全没有 → unknown。"""
    if last is None:
        return OrderUpdate(status=UNKNOWN)
    if last.filled_qty and last.filled_qty > 0:
        return replace(last, status=PARTIAL)
    return replace(last, status=UNKNOWN)
