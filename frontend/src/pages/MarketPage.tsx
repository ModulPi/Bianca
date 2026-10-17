import { useCallback, useState } from "react";
import { api } from "../api/client";
import KlineChartPanel from "../components/KlineChartPanel";
import StatCard from "../components/StatCard";
import TickerPanel from "../components/TickerPanel";
import { usePolling } from "../hooks/usePolling";
import type { TickerResponse } from "../types/api";
import { fmtDuration, fmtMs, statusTone } from "../utils/format";

export default function MarketPage() {
  const status = usePolling(api.marketStatus, 30_000);
  // 稳定引用，理由见 DashboardPage：内联 fetcher 会让轮询变成每渲染一次。
  const fetchTrades = useCallback(() => api.trades({ limit: 200 }), []);
  const trades = usePolling(fetchTrades, 30_000);
  const [ticker, setTicker] = useState<TickerResponse | null>(null);
  const [tickerError, setTickerError] = useState<string | null>(null);
  const [tickerBusy, setTickerBusy] = useState(false);
  const [backfillMsg, setBackfillMsg] = useState<string | null>(null);
  const [backfillBusy, setBackfillBusy] = useState(false);

  const s = status.data;

  const loadTicker = async (symbol?: string) => {
    setTickerBusy(true);
    setTickerError(null);
    try {
      setTicker(await api.exchangeTicker(symbol));
    } catch (err) {
      setTickerError(err instanceof Error ? err.message : String(err));
    } finally {
      setTickerBusy(false);
    }
  };

  const backfill = async () => {
    setBackfillBusy(true);
    setBackfillMsg(null);
    try {
      const res = await api.marketBackfill();
      setBackfillMsg(
        `${res.status} · 请求 ${res.requests ?? "—"} · 收到 ${res.rows_received ?? "—"} · ` +
          `新增 ${res.rows_inserted ?? "—"} · 失败块 ${res.failed_chunks ?? "—"} · ${res.duration_s?.toFixed(1) ?? "—"}s`,
      );
      status.refresh();
    } catch (err) {
      setBackfillMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBackfillBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">行情</h1>
          <p className="mt-1 text-sm text-zinc-500">
            数据面状态与本地 K 线库。顶层字段跨进程可信，`session` 只是当前 API 进程的自述。
          </p>
        </div>
        <button
          type="button"
          onClick={() => void backfill()}
          disabled={backfillBusy}
          className="rounded-lg border border-zinc-700 px-3 py-1.5 text-sm text-zinc-300 hover:text-amber-300 disabled:opacity-50"
        >
          {backfillBusy ? "回补中…" : "触发回补"}
        </button>
      </header>

      {status.error ? (
        <p className="rounded-lg border border-rose-900/50 bg-rose-950/20 px-3 py-2 text-sm text-rose-300">
          {status.error}
        </p>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="数据在流"
          value={s ? (s.data_flowing ? "是" : "否") : "—"}
          hint={s ? `滞后 ${fmtDuration(s.lag_seconds)}` : undefined}
          tone={s?.data_flowing ? "positive" : "negative"}
        />
        <StatCard
          label="最新 bar"
          value={s ? fmtMs(s.last_bar_open_time) : "—"}
          hint={s ? `已收盘 ${fmtMs(s.last_closed_bar_open_time)}` : undefined}
        />
        <StatCard
          label="24h bar 数"
          value={s ? `${s.bars_count_24h}${s.bars_expected_24h ? ` / ${s.bars_expected_24h}` : ""}` : "—"}
          hint={s ? `缺 ${s.gap_count_24h ?? "—"} 根` : undefined}
        />
        <StatCard
          label="采集器"
          value={s?.collector_owner ?? "—"}
          hint={s ? `${s.symbols.join("、")} · ${s.interval}` : undefined}
          tone={s?.collector_owner === "self" || s?.collector_owner === "other" ? "positive" : "warn"}
        />
      </div>

      {s?.interval_error ? (
        <p className="rounded-lg border border-amber-900/60 bg-amber-950/20 px-3 py-2 text-sm text-amber-300">
          周期配置异常：{s.interval_error}
        </p>
      ) : null}
      {backfillMsg ? <p className="text-xs text-zinc-400">{backfillMsg}</p> : null}

      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium text-zinc-300">实时 Ticker</h2>
          <button
            type="button"
            onClick={() => void loadTicker(s?.symbols[0])}
            disabled={tickerBusy}
            className="rounded-lg border border-zinc-800 px-2 py-1 text-xs text-zinc-400 hover:text-amber-300 disabled:opacity-50"
          >
            {tickerBusy ? "读取中…" : "读取一次（打交易所）"}
          </button>
        </div>
        <TickerPanel tickers={ticker ? [ticker] : []} error={tickerError} />
      </section>

      <KlineChartPanel symbols={s?.symbols ?? []} trades={trades.data?.items ?? []} />

      {s?.session ? (
        <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
          <h2 className="text-sm font-medium text-zinc-300">
            本进程采集器 <span className={statusTone(s.session.connected ? "ok" : "error")}>
              {s.session.connected ? "connected" : "disconnected"}
            </span>
          </h2>
          <p className="mt-2 text-xs text-zinc-500">
            本 session 写入 {s.session.bars_written_session} 根 · 重连{" "}
            {s.session.reconnects_session} 次 · 补缺口 {s.session.gaps_filled} 次 · 启动于{" "}
            {s.session.started_at ? fmtMs(new Date(s.session.started_at).getTime()) : "—"}
          </p>
          {s.session.last_error ? (
            <p className="mt-1 text-xs text-rose-400">
              {s.session.last_error}
              {s.session.last_error_at
                ? ` · ${new Date(s.session.last_error_at).toLocaleString()}`
                : ""}
            </p>
          ) : null}
          {s.session.backfill_running ? (
            <p className="mt-1 text-xs text-amber-400">回补进行中…</p>
          ) : null}
        </section>
      ) : (
        <p className="text-xs text-zinc-600">
          本 API 进程不是采集器 —— 这是正常的（数据面独立运行）。判断数据流看上面的
          `数据在流` 与 `滞后`。
        </p>
      )}
    </div>
  );
}
