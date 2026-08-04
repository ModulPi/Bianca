import type { SessionSummary, UsageSummary, WorkerTokenUsage } from "../types/api";
import StatCard from "./StatCard";

interface TokenUsagePanelProps {
  usage: UsageSummary | null;
  session: SessionSummary | null;
  workerUsage?: WorkerTokenUsage[];
  error?: string | null;
}

export default function TokenUsagePanel({ usage, session, workerUsage, error }: TokenUsagePanelProps) {
  if (error) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4 text-sm text-rose-400 h-full">
        Token 加载失败：{error}
      </div>
    );
  }

  const sessionUsage = session?.usage;
  const sessionLabel = session?.ended_at ? "最近会话" : "当前会话";

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4 h-full">
      <p className="text-xs uppercase tracking-wide text-zinc-500">Token 用量</p>
      <div className="mt-3 grid grid-cols-2 gap-2">
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
      </div>
      {sessionUsage ? (
        <div className="mt-2 grid grid-cols-2 gap-2">
          <StatCard
            label={`${sessionLabel} calls`}
            value={String(sessionUsage.llm_calls)}
            hint={`${sessionUsage.total_tokens.toLocaleString()} tokens`}
          />
          <StatCard
            label="预估成本"
            value={`$${sessionUsage.estimated_cost_usd.toFixed(4)}`}
            hint="会话级估算"
          />
        </div>
      ) : (
        <p className="mt-3 text-xs text-zinc-500">暂无会话 Token 数据</p>
      )}
      {workerUsage && workerUsage.length > 0 ? (
        <div className="mt-3 overflow-x-auto">
          <p className="mb-2 text-[11px] text-zinc-500">按 Worker 分摊</p>
          <table className="w-full text-left text-[11px]">
            <thead>
              <tr className="border-b border-zinc-800 text-zinc-500">
                <th className="py-1 pr-2">Symbol</th>
                <th className="py-1 pr-2">calls</th>
                <th className="py-1">tokens</th>
              </tr>
            </thead>
            <tbody>
              {workerUsage.map((w) => (
                <tr key={w.symbol} className="border-b border-zinc-800/40">
                  <td className="py-1 pr-2 mono text-amber-300">{w.symbol}</td>
                  <td className="py-1 pr-2">{w.llm_calls}</td>
                  <td className="py-1">{w.total_tokens.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
