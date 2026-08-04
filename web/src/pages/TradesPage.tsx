import { useCallback, useState } from "react";
import { api } from "../api/client";
import PnLChart from "../components/PnLChart";
import TradesTable from "../components/TradesTable";
import { usePolling } from "../hooks/usePolling";

export default function TradesPage() {
  const [side, setSide] = useState("");
  const [status, setStatus] = useState("");

  const fetcher = useCallback(
    () => api.trades({ limit: 100, side: side || undefined, status: status || undefined }),
    [side, status],
  );

  const { data, error, loading } = usePolling(fetcher, 10000);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">交易记录</h1>
        <p className="text-sm text-zinc-500">trade_logs 明细</p>
      </header>

      <div className="flex flex-wrap gap-3">
        <select
          value={side}
          onChange={(e) => setSide(e.target.value)}
          className="rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm"
        >
          <option value="">全部方向</option>
          <option value="BUY">BUY</option>
          <option value="SELL">SELL</option>
        </select>
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm"
        >
          <option value="">全部状态</option>
          <option value="filled">filled</option>
          <option value="failed">failed</option>
          <option value="signal_only">signal_only</option>
        </select>
      </div>

      {loading && !data ? <p className="text-sm text-zinc-500">加载中…</p> : null}
      {error ? <p className="text-sm text-rose-400">{error}</p> : null}
      <PnLChart trades={data?.items ?? []} />
      <TradesTable items={data?.items ?? []} showSymbol />
    </div>
  );
}
