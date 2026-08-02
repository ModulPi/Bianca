import { useCallback } from "react";
import { api, fetchDashboardSummary } from "../api/client";
import AgentControl from "../components/AgentControl";
import SessionSummaryPanel from "../components/SessionSummaryPanel";
import StatCard from "../components/StatCard";
import TradesTable from "../components/TradesTable";
import { usePolling } from "../hooks/usePolling";

export default function DashboardPage() {
  const statusPoll = usePolling(() => api.agentStatus(), 5000);

  const summaryFetcher = useCallback(async () => {
    return fetchDashboardSummary(statusPoll.data?.running ?? false);
  }, [statusPoll.data?.running]);

  const summaryPoll = usePolling(summaryFetcher, 8000, statusPoll.data !== null);
  const tradesPoll = usePolling(() => api.trades({ limit: 10 }), 15000);
  const healthPoll = usePolling(() => api.health(), 30000);

  const health = healthPoll.data;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">仪表盘</h1>
        <p className="text-sm text-zinc-500">会话汇总 · Agent 状态 · 最近成交</p>
      </header>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-1">
          <AgentControl
            status={statusPoll.data}
            onChange={() => {
              void statusPoll.refresh();
              void summaryPoll.refresh();
            }}
          />
        </div>
        <div className="lg:col-span-2 grid grid-cols-3 gap-3">
          <StatCard
            label="系统"
            value={health?.status ?? "—"}
            tone={health?.status === "ok" ? "positive" : "warn"}
          />
          <StatCard label="LLM" value={health?.llm ?? "—"} hint={health?.llm_provider} />
          <StatCard label="Binance Demo" value={health?.binance_demo ?? "—"} />
        </div>
      </div>

      {summaryPoll.error ? (
        <p className="text-sm text-rose-400">{summaryPoll.error}</p>
      ) : summaryPoll.data ? (
        <SessionSummaryPanel
          summary={summaryPoll.data}
          title={statusPoll.data?.running ? "当前会话" : "最近会话"}
        />
      ) : (
        <p className="text-sm text-zinc-500">暂无会话汇总，启动 Agent 并完成一次闭环后可见。</p>
      )}

      <section>
        <h2 className="mb-3 text-lg font-medium">最近成交</h2>
        <TradesTable items={tradesPoll.data?.items ?? []} />
      </section>
    </div>
  );
}
