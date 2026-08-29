import { api } from "../api/client";
import RiskEventsTable from "../components/RiskEventsTable";
import { usePolling } from "../hooks/usePolling";

export default function RiskPage() {
  const { data, error, loading } = usePolling(() => api.riskEvents(100), 15000);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">风控事件</h1>
        <p className="text-sm text-zinc-500">拒单与熔断记录 · risk_events</p>
      </header>
      {loading && !data ? <p className="text-sm text-zinc-500">加载中…</p> : null}
      {error ? <p className="text-sm text-rose-400">{error}</p> : null}
      <RiskEventsTable items={data?.items ?? []} />
    </div>
  );
}
