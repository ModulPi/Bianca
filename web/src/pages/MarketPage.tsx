import { useState } from "react";
import { api } from "../api/client";
import { usePolling } from "../hooks/usePolling";

export default function MarketPage() {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [interval, setInterval] = useState("1m");
  const { data, error, loading, refresh } = usePolling(
    () => api.marketKlines(symbol, interval, 60),
    30000,
  );

  const items = data?.items ?? [];
  const maxHigh = items.reduce((m, k) => Math.max(m, k.high), 0);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">K 线</h1>
        <p className="text-sm text-zinc-500">TimescaleDB 持久化数据（PostgreSQL 双栈 + KLINES_ENABLED）</p>
      </header>

      <div className="flex flex-wrap gap-3">
        <input
          value={symbol}
          onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm mono"
          placeholder="BTCUSDT"
        />
        <select
          value={interval}
          onChange={(e) => setInterval(e.target.value)}
          className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
        >
          <option value="1m">1m</option>
          <option value="5m">5m</option>
          <option value="15m">15m</option>
        </select>
        <button
          type="button"
          onClick={() => void refresh()}
          className="rounded-lg border border-zinc-600 px-3 py-2 text-sm hover:bg-zinc-800"
        >
          刷新
        </button>
      </div>

      {loading && !data ? <p className="text-sm text-zinc-500">加载中…</p> : null}
      {error ? <p className="text-sm text-rose-400">{error}</p> : null}

      {items.length === 0 && !loading ? (
        <p className="text-sm text-zinc-500">暂无 K 线数据。请启用 PostgreSQL 双栈并运行 Agent 写入。</p>
      ) : null}

      {items.length > 0 ? (
        <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
          <div className="mb-4 flex h-32 items-end gap-0.5">
            {items
              .slice()
              .reverse()
              .map((k) => {
                const h = maxHigh > 0 ? (k.close / maxHigh) * 100 : 0;
                const up = k.close >= k.open;
                return (
                  <div
                    key={k.time}
                    title={`${k.time} C:${k.close}`}
                    className={`flex-1 min-w-[2px] rounded-t ${up ? "bg-emerald-600/70" : "bg-rose-600/70"}`}
                    style={{ height: `${Math.max(h, 4)}%` }}
                  />
                );
              })}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-zinc-800 text-zinc-500">
                  <th className="py-2 pr-3">时间</th>
                  <th className="py-2 pr-3">开</th>
                  <th className="py-2 pr-3">高</th>
                  <th className="py-2 pr-3">低</th>
                  <th className="py-2 pr-3">收</th>
                  <th className="py-2">量</th>
                </tr>
              </thead>
              <tbody>
                {items.map((k) => (
                  <tr key={k.time} className="border-b border-zinc-800/50 mono">
                    <td className="py-1.5 pr-3 text-zinc-400">{k.time.slice(0, 19)}</td>
                    <td className="py-1.5 pr-3">{k.open.toFixed(2)}</td>
                    <td className="py-1.5 pr-3">{k.high.toFixed(2)}</td>
                    <td className="py-1.5 pr-3">{k.low.toFixed(2)}</td>
                    <td className="py-1.5 pr-3 text-amber-300">{k.close.toFixed(2)}</td>
                    <td className="py-1.5">{k.volume.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  );
}
