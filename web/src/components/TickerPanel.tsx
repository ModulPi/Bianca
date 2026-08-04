import type { TickerResponse } from "../types/api";

interface TickerPanelProps {
  tickers: TickerResponse[];
  loading?: boolean;
  error?: string | null;
}

export default function TickerPanel({ tickers, loading, error }: TickerPanelProps) {
  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-medium text-zinc-300">实时行情</h2>
        <span className="text-[10px] text-zinc-600">轻量 ticker · 非 K 线终端 · 10s 刷新</span>
      </div>
      {error ? (
        <p className="mb-2 text-xs text-rose-400">{error}</p>
      ) : null}
      {loading && tickers.length === 0 ? (
        <p className="text-sm text-zinc-500">加载中…</p>
      ) : tickers.length === 0 ? (
        <p className="text-sm text-zinc-500">无行情数据</p>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {tickers.map((t) => (
            <div
              key={t.symbol ?? "default"}
              className="rounded-lg border border-zinc-800/80 bg-zinc-950/40 px-3 py-2"
            >
              <p className="mono text-sm font-medium text-amber-300">{t.symbol ?? "—"}</p>
              <p className="mono mt-1 text-xl font-semibold">{t.last?.toLocaleString() ?? "—"}</p>
              <div className="mt-1 flex gap-3 text-[11px] text-zinc-500">
                <span>bid {t.bid?.toLocaleString() ?? "—"}</span>
                <span>ask {t.ask?.toLocaleString() ?? "—"}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
