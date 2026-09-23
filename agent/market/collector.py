"""行情采集器：WS 主通道 + REST 回补。

与 AgentRunner 并列的独立 asyncio 任务，各自生命周期与快照（ADR-010）。

核心不变量：
- 只在 bar 关闭（k.x == true）后落库
- 重连后**先补缺口再恢复订阅**
- 断线是常态（经代理的长连接会频繁被重置），指数退避而非崩溃
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import websockets

from agent.config import Settings, get_settings
from agent.market.backfill import backfill_gaps, backfill_history
from agent.market.bars import (
    count_bars,
    interval_ms,
    last_closed_bar_open,
    parse_ws_kline,
)
from agent.market.lock import (
    CollectorLock,
    LockState,
    collector_lock_path,
    probe_lock_state,
)
from agent.market.repository import KlineRepository

logger = logging.getLogger(__name__)

# 日常巡检只看最近这么久的缺口：全量空白由 backfill_history 负责，
# 避免每次巡检都读取数百万行已存时间戳
_GAP_LOOKBACK_MS = 2 * 24 * 3_600_000

_BACKOFF_START_S = 1.0
_BACKOFF_MAX_S = 60.0
# 连续收到这么多条消息才算「连稳了」，据此重置退避
_STABLE_MESSAGES = 3


def build_ws_url(base: str, symbols: list[str], interval: str) -> str:
    """单标点对点流，多标的合并流（ADR-016：按订阅集合建模）。"""
    streams = "/".join(f"{s.lower()}@kline_{interval}" for s in symbols)
    if len(symbols) == 1:
        return f"{base.rstrip('/')}/{streams}"
    root = base.rstrip("/")
    if root.endswith("/ws"):
        root = root[: -len("/ws")]
    return f"{root}/stream?streams={streams}"


def install_ws_noise_filter() -> None:
    """抑制 websockets 16.0 的 teardown bug 噪声（设计文档 §6 库级问题 1）。

    连接被重置时，websockets 自身的 connection_lost 清理路径会抛
    AttributeError: 'ClientConnection' object has no attribute 'recv_messages'。
    经代理的长连接会频繁遇到重置，不处理会淹没日志。
    """

    def handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        if isinstance(exc, AttributeError) and "recv_messages" in str(exc):
            logger.debug("suppressed websockets teardown bug: %s", exc)
            return
        loop.default_exception_handler(context)

    try:
        asyncio.get_running_loop().set_exception_handler(handler)
    except RuntimeError:
        pass


@dataclass
class CollectorSnapshot:
    running: bool = False
    connected: bool = False
    symbols: list[str] = field(default_factory=list)
    interval: str = "1m"
    started_at: str | None = None
    connected_at: str | None = None
    last_write_at: str | None = None
    bars_written: int = 0  # 本次进程生命周期内写入
    reconnects: int = 0
    gaps_filled: int = 0
    last_gap_check_at: str | None = None
    backfill_running: bool = False
    backfill_last: dict[str, Any] | None = None
    backfill_error: str | None = None
    last_error: str | None = None
    last_error_at: str | None = None

    @property
    def has_session(self) -> bool:
        """这个采集器对象有没有"自述"可给：跑过（哪怕已经停了），或正在回补。

        用它决定 `market_status_detail()` 的 `session` 是对象还是 null。`running`
        不能担此任 —— 停机之后那些"这一程写了多少根/重连几次"正是最该看的时候。
        """
        return self.started_at is not None or self.backfill_running


def _now() -> str:
    return datetime.now(UTC).isoformat()


class MarketCollector:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._task: asyncio.Task[None] | None = None
        # 后台回补把 BackfillStats 转成 dict 返回（供 POST /market/backfill 复用）
        self._backfill_task: asyncio.Task[Any] | None = None
        self._stop_event = asyncio.Event()
        self._snapshot = CollectorSnapshot(
            symbols=self._settings.market_symbol_list,
            interval=self._settings.market_interval,
        )
        self._repo = KlineRepository()
        # 单实例锁（ADR-017）：采集器可以住在 API 进程里，也可以独立成进程，
        # 但同一个库同时只能有一个采集器在写。锁在这两条路径上统一生效。
        self._lock = CollectorLock(collector_lock_path(self._settings.market_database_url))

    @property
    def running(self) -> bool:
        return self._snapshot.running

    @property
    def lock(self) -> CollectorLock:
        return self._lock

    async def start(self) -> None:
        if self._snapshot.running:
            return
        cfg = self._settings
        if not cfg.market_symbol_list:
            raise RuntimeError("MARKET_SYMBOLS is empty")
        interval_ms(cfg.market_interval)  # 早失败：周期非法就别启动

        # 校验都过了再抢锁，抢不到就抛 CollectorLockError（调用方决定怎么退）
        self._lock.acquire()
        try:
            self._stop_event.clear()
            self._snapshot.running = True
            self._snapshot.started_at = _now()
            self._task = asyncio.create_task(self._loop(), name="bianca-market-collector")
        except BaseException:
            self._lock.release()  # 没起来就别占着锁
            self._snapshot.running = False
            raise
        logger.info(
            "Market collector started (symbols=%s interval=%s)",
            cfg.market_symbol_list,
            cfg.market_interval,
        )

    async def stop(self) -> None:
        self._stop_event.set()
        for task in (self._task, self._backfill_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001 — 停机不抛
                    pass
        self._task = None
        self._backfill_task = None
        self._snapshot.running = False
        self._snapshot.connected = False
        self._lock.release()  # 停了就交还，好让下一个采集器（API 内或独立进程）能起
        logger.info("Market collector stopped")

    async def get_snapshot(self) -> CollectorSnapshot:
        return self._snapshot

    # ------------------------------------------------------------------ 回补

    async def backfill_history_now(self) -> dict[str, Any]:
        """触发一次全量历史回补（幂等、可重入）。"""
        if self._snapshot.backfill_running:
            return {"status": "already_running"}
        self._snapshot.backfill_error = None
        self._snapshot.backfill_running = True
        try:
            stats = await backfill_history(
                symbol=self._settings.market_symbol_list[0],
                interval=self._settings.market_interval,
                settings=self._settings,
            )
            self._snapshot.backfill_last = stats.as_dict()
            return {"status": "ok", **stats.as_dict()}
        except Exception as exc:  # noqa: BLE001
            self._snapshot.backfill_error = str(exc)
            logger.exception("history backfill failed")
            return {"status": "error", "detail": str(exc)}
        finally:
            self._snapshot.backfill_running = False

    # ------------------------------------------------------------------ 主循环

    async def _loop(self) -> None:
        install_ws_noise_filter()
        if self._settings.market_backfill_on_start:
            # 全量回补要十几分钟，不能阻塞启动 —— 放后台跑，与实时采集并行
            self._backfill_task = asyncio.create_task(
                self.backfill_history_now(), name="bianca-market-backfill"
            )

        backoff = _BACKOFF_START_S
        while not self._stop_event.is_set():
            try:
                await self._stream_once()
                backoff = _BACKOFF_START_S
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — 采集器必须活着
                self._snapshot.connected = False
                self._snapshot.last_error = f"{type(exc).__name__}: {exc}"
                self._snapshot.last_error_at = _now()
                self._snapshot.reconnects += 1
                logger.warning("market stream dropped: %s (retry in %.1fs)", exc, backoff)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                    break
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, _BACKOFF_MAX_S)

    async def _stream_once(self) -> None:
        cfg = self._settings
        url = build_ws_url(cfg.market_ws_base_url, cfg.market_symbol_list, cfg.market_interval)
        proxy = cfg.market_proxy or None

        async with websockets.connect(url, proxy=proxy, open_timeout=20, ping_interval=20) as ws:
            self._snapshot.connected = True
            self._snapshot.connected_at = _now()
            logger.info("market stream connected: %s", url)

            # 重连后先补缺口，再恢复订阅 —— 顺序不能反（设计文档 §2.3）
            await self._fill_gaps()

            seen = 0
            async for raw in ws:
                if self._stop_event.is_set():
                    return
                row = self._parse(raw)
                if row is None:
                    continue
                written = await self._repo.insert_rows([row])
                if written:
                    self._snapshot.bars_written += written
                    self._snapshot.last_write_at = _now()
                seen += 1
                if seen == _STABLE_MESSAGES:
                    self._snapshot.last_error = None

    def _parse(self, raw: str | bytes) -> dict[str, Any] | None:
        try:
            event = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return parse_ws_kline(event)

    async def _fill_gaps(self) -> None:
        cfg = self._settings
        symbol = cfg.market_symbol_list[0]
        interval = cfg.market_interval
        try:
            stats = await backfill_gaps(
                symbol,
                interval,
                lookback_ms=_GAP_LOOKBACK_MS,
                settings=cfg,
            )
            self._snapshot.last_gap_check_at = _now()
            if stats.rows_inserted:
                self._snapshot.gaps_filled += stats.rows_inserted
                logger.info("gap check filled %s bars", stats.rows_inserted)
        except Exception as exc:  # noqa: BLE001 — 补洞失败不应中断订阅
            logger.warning("gap check failed: %s", exc)
            self._snapshot.last_gap_check_at = _now()


def _database_label(url: str) -> str:
    return url.rsplit("/", 1)[-1] if "/" in url else url


def _lock_file_label(database_url: str) -> str | None:
    """锁文件的**绝对**路径。

    值得单独暴露：`collector_lock_path` 解析相对 URL 时用的是进程 CWD，而计划任务
    带 `WorkingDirectory=repo_root`、API 未必 —— 两者 CWD 不同就会指着**两个不同的
    锁文件**，于是探针报"空闲"而采集器其实在跑。把绝对路径摆出来，这种错配自己
    就看得出来（顺带也是单实例锁本身的一个隐患）。
    """
    try:
        path = collector_lock_path(database_url)
    except Exception:  # noqa: BLE001 — 观测信息算不出来不该影响状态查询
        logger.debug("could not derive lock path", exc_info=True)
        return None
    return str(path.resolve()) if path is not None else None


# 采集器静默死亡是风险 #1：数据断流但无人知。超过这么多个周期没新 bar 即报 degraded。
_STALE_LAG_S = 180


def _stale_after_s(step_s: int) -> int:
    """把"落后几个周期算故障"换算成秒。

    180s 是按 1m 定的。周期越长这个常数越离谱：`MARKET_INTERVAL=1h` 时每根之间
    本来就隔 3600s，固定的 180s 会让它在每个小时里有约 94% 的时间报 error ——
    一个天天喊狼来了的判据等于没有判据。所以取 max(180s, 3 个周期)。
    """
    return max(_STALE_LAG_S, 3 * step_s)


def _flow_verdict(
    *,
    symbol: str,
    interval: str,
    step_s: int | None,
    last_bar: int | None,
    now_ms: int,
    lock_state: LockState,
) -> tuple[str, int | None, str]:
    """数据面健不健康 —— **只看数据流动，不看进程归属**。

    `/health` 与 `/market/status` 都由这里得出结论，否则两个面会互相矛盾。

    可用的信道只有两条，都是跨进程读得到的：库（新不新鲜）和内核锁（有没有进程在）。
    所以同一个判据在 API 进程里和采集器进程里得出同一个答案 —— 这正是它们以前
    做不到的事。
    """
    if not symbol:
        # 没有标的就没有订阅，采集器起不来，库永远是空的。但库空本身会被判成
        # "冷启动"从而一直 warming_up，所以这条必须显式判死。
        return "error", None, "MARKET_SYMBOLS 为空：没有订阅任何标的"
    if step_s is None:
        return "error", None, f"不支持的 K 线周期 {interval!r}"

    if last_bar is None:
        if lock_state == "free":
            # 有正面证据：没人在写这个库，而且一根 bar 都没有
            return "error", None, "库里没有任何 bar，且没有采集器在运行"
        # 持锁 → 有采集器，只是历史回补还没落第一根；unknown → 问不出答案，
        # 而"报故障"应当要求证据，所以按冷启动对待。
        return "warming_up", None, "no bars yet (initial backfill in progress)"

    lag_s = max((now_ms - last_bar) // 1000 - step_s, 0)
    threshold = _stale_after_s(step_s)
    if lag_s > threshold:
        return "error", lag_s, f"stale: lag {lag_s}s > {threshold}s"
    return "ok", lag_s, f"lag {lag_s}s"


async def market_health(settings: Settings | None = None) -> tuple[str, str | None]:
    """给 /health 用的轻量探活（不跑 count_since，避免把健康检查变重）。

    返回 (status, detail)。status ∈ ok / warming_up / error。

    **不再有 `disabled`**，也不再接受 collector 参数：那两样都是在问"本进程是否
    托管采集器"，而采集器现在住在独立进程里（ADR-017），这个问题的答案与数据面
    的健康无关。历史上 `disabled` 还恰好把风险 #1 的告警静默掉了 ——
    `MARKET_COLLECTOR_AUTOSTART=false` 之后它永远返回 disabled，而 overall 只在
    `error` 时降级，于是采集器真的死了也不会响。

    附带一句以免后人以为删掉的分支承重：原来的 `not snap.running` 从来没抓到过
    `_loop` 的静默死亡 —— `snapshot.running` 只在 `stop()` 里清，循环崩了它仍是
    True，真正兜住这种情况的一直是下面这个 lag 判据。
    """
    cfg = settings or get_settings()
    symbol = cfg.market_symbol_list[0] if cfg.market_symbol_list else ""
    interval = cfg.market_interval
    try:
        step_s: int | None = interval_ms(interval) // 1000
    except ValueError:
        step_s = None

    last_bar = None
    if symbol and step_s is not None:
        last_bar = await KlineRepository().last_bar_time(symbol, interval)

    lock_state: LockState = probe_lock_state(cfg.market_database_url)
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    verdict, _, detail = _flow_verdict(
        symbol=symbol,
        interval=interval,
        step_s=step_s,
        last_bar=last_bar,
        now_ms=now_ms,
        lock_state=lock_state,
    )
    return verdict, detail


def _collector_owner(collector: MarketCollector, lock_state: LockState) -> str:
    """谁在写这个库：self / other / none / unknown（最后一个 = 这台机器问不出来）。"""
    lock = getattr(collector, "lock", None)
    if lock is not None and getattr(lock, "held", False):
        return "self"
    return {"held": "other", "free": "none", "unknown": "unknown"}[lock_state]


async def market_status_detail(
    collector: MarketCollector, settings: Settings | None = None
) -> dict[str, Any]:
    """供 API / 独立入口用的状态聚合。

    **分区原则：顶层只放跨进程可信的事实，进程内快照一律收进 `session`。**
    这不是为了整齐。采集器独立成进程（ADR-017）之后，进程内字段在 API 进程里必然
    是 false/null —— 而它们和跨进程字段平铺在一起时，读的人会得出"采集器没在跑"
    这个错误结论（实测：数据在流、lag 9s，而 collector_running=false）。收进
    `session` 并且在没有视角时给 `null`，是让这类谎报在结构上说不出口：
    `null` 说的是"我没有这个视角"，而 `false` 说的是"它在跑但没连上"，两者不是
    一回事。所以顶层的 key 集合有测试钉着 —— 新字段要放对地方。
    """
    cfg = settings or get_settings()
    snap = await collector.get_snapshot()
    symbols = cfg.market_symbol_list
    symbol = symbols[0] if symbols else ""
    interval = cfg.market_interval
    now_ms = int(datetime.now(UTC).timestamp() * 1000)

    try:
        step_s: int | None = interval_ms(interval) // 1000
        interval_error: str | None = None
    except ValueError as exc:
        # 周期配错时宁可给出一个说得清的状态，也不要 500 —— 状态端点本身不该失联
        step_s, interval_error = None, str(exc)

    lock_state = probe_lock_state(cfg.market_database_url)
    owner = _collector_owner(collector, lock_state)
    own_lock = owner == "self"

    repo = KlineRepository()
    last_bar = (
        await repo.last_bar_time(symbol, interval)
        if (symbol and step_s is not None)
        else None
    )
    verdict, lag_seconds, _ = _flow_verdict(
        symbol=symbol,
        interval=interval,
        step_s=step_s,
        last_bar=last_bar,
        now_ms=now_ms,
        lock_state="held" if own_lock else lock_state,
    )

    # 24h 缺口：窗口右端对齐到"最后一根已收盘 bar"，否则当下这根还没收盘的会被
    # 算成缺口。expected 与 actual 用同一个窗口 —— 比较 two count 才有意义。
    day_ago = now_ms - 24 * 3_600_000
    bars_count_24h = 0
    if symbol and step_s is not None:
        bars_count_24h = await repo.count_since(symbol, interval, day_ago)
    expected_24h = (
        count_bars(day_ago, last_bar, interval)
        if (last_bar is not None and step_s is not None)
        else None
    )
    # 保留 max(...,0) 但对越窗回补不沉默：expected 一并给出，actual > expected
    # 时读的人看得出来（那说明窗口里被补进了别的来源的行）。
    gap_count_24h = (
        max(expected_24h - bars_count_24h, 0) if expected_24h is not None else None
    )

    # 本进程有过采集器会话时才有"自述"（跑过或正在跑），否则这个视角不存在
    session: dict[str, Any] | None = None
    if snap.has_session:
        session = {
            "connected": snap.connected,
            "bars_written_session": snap.bars_written,
            "reconnects_session": snap.reconnects,
            "gaps_filled": snap.gaps_filled,
            "last_gap_check_at": snap.last_gap_check_at,
            "backfill_running": snap.backfill_running,
            "backfill_last": snap.backfill_last,
            "backfill_error": snap.backfill_error,
            "last_error": snap.last_error,
            "last_error_at": snap.last_error_at,
            "last_write_at": snap.last_write_at,
            "started_at": snap.started_at,
        }

    return {
        "database": _database_label(cfg.market_database_url),
        "lock_file": _lock_file_label(cfg.market_database_url),
        "symbols": symbols,
        "interval": interval,
        "interval_error": interval_error,
        "last_bar_open_time": last_bar,
        "last_closed_bar_open_time": (
            last_closed_bar_open(now_ms, interval) if step_s is not None else None
        ),
        "lag_seconds": lag_seconds,
        "bars_count_24h": bars_count_24h,
        "bars_expected_24h": expected_24h,
        "gap_count_24h": gap_count_24h,
        "collector_owner": owner,
        # bool 表达不了 ok/warming_up/error 三态：warming_up 映射为 false，
        # 冷启动不该被消费方当成故障（/health 本来也不因 warming_up 转 degraded）。
        "data_flowing": verdict == "ok",
        "session": session,
    }


_collector: MarketCollector | None = None


def get_collector() -> MarketCollector:
    global _collector
    if _collector is None:
        _collector = MarketCollector()
    return _collector
