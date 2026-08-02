import { api } from "../api/client";
import StatCard from "../components/StatCard";
import { usePolling } from "../hooks/usePolling";

export default function UsagePage() {
  const { data, error, loading } = usePolling(() => api.usage(), 15000);

  if (loading && !data) {
    return <p className="text-sm text-zinc-500">加载中…</p>;
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Token 消耗</h1>
        <p className="text-sm text-zinc-500">decision_logs 聚合</p>
      </header>

      {error ? <p className="text-sm text-rose-400">{error}</p> : null}

      {data ? (
        <div className="grid gap-6 lg:grid-cols-2">
          <section className="space-y-3">
            <h2 className="text-sm uppercase tracking-wide text-zinc-500">今日 (UTC)</h2>
            <div className="grid grid-cols-2 gap-3">
              <StatCard label="调用次数" value={String(data.today.calls)} />
              <StatCard label="总 Token" value={data.today.total_tokens.toLocaleString()} />
              <StatCard label="Prompt" value={data.today.prompt_tokens.toLocaleString()} />
              <StatCard label="Completion" value={data.today.completion_tokens.toLocaleString()} />
            </div>
          </section>
          <section className="space-y-3">
            <h2 className="text-sm uppercase tracking-wide text-zinc-500">累计</h2>
            <div className="grid grid-cols-2 gap-3">
              <StatCard label="调用次数" value={String(data.total.calls)} />
              <StatCard label="总 Token" value={data.total.total_tokens.toLocaleString()} />
              <StatCard label="Prompt" value={data.total.prompt_tokens.toLocaleString()} />
              <StatCard label="Completion" value={data.total.completion_tokens.toLocaleString()} />
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
