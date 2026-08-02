import { api } from "../api/client";
import DecisionsTable from "../components/DecisionsTable";
import { usePolling } from "../hooks/usePolling";

export default function DecisionsPage() {
  const { data, error, loading } = usePolling(() => api.decisions(100), 15000);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">LLM 决策</h1>
        <p className="text-sm text-zinc-500">decision_logs · 结构化信号与 Token</p>
      </header>
      {loading && !data ? <p className="text-sm text-zinc-500">加载中…</p> : null}
      {error ? <p className="text-sm text-rose-400">{error}</p> : null}
      <DecisionsTable items={data?.items ?? []} />
    </div>
  );
}
