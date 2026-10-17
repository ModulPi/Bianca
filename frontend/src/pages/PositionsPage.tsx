import { useCallback, useState } from "react";
import { api } from "../api/client";
import EquityChart from "../components/EquityChart";
import PositionsTable from "../components/PositionsTable";
import { usePolling } from "../hooks/usePolling";

export default function PositionsPage() {
  const portfolio = usePolling(api.portfolio, 10_000);
  // 稳定引用，理由见 DashboardPage：内联 fetcher 会让轮询变成每渲染一次。
  const fetchHistory = useCallback(() => api.equityHistory({ limit: 500 }), []);
  const history = usePolling(fetchHistory, 15_000);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const capture = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const snap = await api.captureSnapshot();
      setMsg(`已采集：权益 ${snap.equity.toFixed(2)} USDT`);
      portfolio.refresh();
      history.refresh();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">持仓 · 权益</h1>
          <p className="mt-1 text-sm text-zinc-500">
            最近一次交易所快照。采集会真的读交易所，并写一条快照。
          </p>
        </div>
        <button
          type="button"
          onClick={() => void capture()}
          disabled={busy}
          className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-zinc-950 disabled:opacity-50"
        >
          {busy ? "采集中…" : "采集快照"}
        </button>
      </header>

      {msg ? <p className="text-sm text-zinc-400">{msg}</p> : null}
      {portfolio.error ? <p className="text-sm text-rose-400">{portfolio.error}</p> : null}

      <PositionsTable portfolio={portfolio.data} />

      <section className="space-y-2">
        <h2 className="text-sm font-medium text-zinc-300">权益历史</h2>
        {history.error ? (
          <p className="rounded-xl border border-zinc-800 bg-zinc-900/40 px-4 py-3 text-sm text-zinc-400">
            {history.error}
          </p>
        ) : (
          <EquityChart points={history.data?.items ?? []} />
        )}
      </section>
    </div>
  );
}
