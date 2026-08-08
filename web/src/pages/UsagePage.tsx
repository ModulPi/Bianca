import { api } from "../api/client";
import StatCard from "../components/StatCard";
import { usePolling } from "../hooks/usePolling";

export default function UsagePage() {
  const usagePoll = usePolling(() => api.usage(), 30000);
  const summaryPoll = usePolling(() => api.summaryLatest().catch(() => null), 30000);

  const usage = usagePoll.data;
  const session = summaryPoll.data;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Token 用量</h1>
        <p className="text-sm text-zinc-500">LLM 调用与成本统计</p>
      </header>

      {usagePoll.error ? <p className="text-sm text-rose-400">{usagePoll.error}</p> : null}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="今日 calls"
          value={String(usage?.today.calls ?? 0)}
          hint={`${(usage?.today.total_tokens ?? 0).toLocaleString()} tokens`}
        />
        <StatCard
          label="累计 calls"
          value={String(usage?.total.calls ?? 0)}
          hint={`${(usage?.total.total_tokens ?? 0).toLocaleString()} tokens`}
        />
        <StatCard
          label="最近会话 calls"
          value={String(session?.usage.llm_calls ?? 0)}
          hint={session ? `${session.usage.total_tokens.toLocaleString()} tokens` : undefined}
        />
        <StatCard
          label="最近会话成本"
          value={session ? `$${session.usage.estimated_cost_usd.toFixed(4)}` : "—"}
          hint="估算 USD"
        />
      </div>
    </div>
  );
}
