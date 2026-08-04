import { Link } from "react-router-dom";
import { api } from "../api/client";
import AgentControl from "../components/AgentControl";
import ConfirmQueue from "../components/ConfirmQueue";
import ExecutionModeBanner from "../components/ExecutionModeBanner";
import SessionSummaryPanel from "../components/SessionSummaryPanel";
import StatCard from "../components/StatCard";
import TradesTable from "../components/TradesTable";
import WorkerStatusPanel from "../components/WorkerStatusPanel";
import { usePolling } from "../hooks/usePolling";
import { fetchDashboardSummary } from "../api/client";
import { useCallback } from "react";

export default function DashboardPage() {
  const statusPoll = usePolling(() => api.agentStatus(), 5000);
  const summaryFetcher = useCallback(async () => {
    return fetchDashboardSummary(statusPoll.data?.running ?? false);
  }, [statusPoll.data?.running]);
  const summaryPoll = usePolling(summaryFetcher, 10000, statusPoll.data !== null);
  const tradesPoll = usePolling(() => api.trades({ limit: 20 }), 15000);
  const healthPoll = usePolling(() => api.health(), 30000);

  const status = statusPoll.data;
  const showConfirm =
    status?.execution_mode === "semi_auto" || status?.degraded;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Agent 运维</h1>
        <p className="text-sm text-zinc-500">24×7 自主交易 · 多 Worker 并行 · 异常降级人工介入</p>
      </header>

      <ExecutionModeBanner status={status ?? null} />

      <AgentControl
        status={status ?? null}
        onChange={() => {
          void statusPoll.refresh();
          void summaryPoll.refresh();
        }}
      />

      <WorkerStatusPanel
        workers={status?.workers ?? []}
        tradeMarket={status?.trade_market}
        symbols={status?.symbols}
      />

      {showConfirm ? (
        <section>
          <h2 className="mb-3 text-lg font-medium">降级 · 待确认信号</h2>
          <ConfirmQueue />
        </section>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-3">
        <StatCard label="LLM" value={healthPoll.data?.llm ?? "—"} hint={healthPoll.data?.llm_provider} />
        <StatCard
          label="市场"
          value={status?.trade_market ?? "crypto"}
          hint={(status?.symbols ?? []).join(", ") || undefined}
        />
        <StatCard label="Ticks" value={String(status?.tick_count ?? 0)} hint={`间隔 ${status?.tick_interval ?? "—"}s`} />
      </div>

      {summaryPoll.data ? (
        <SessionSummaryPanel summary={summaryPoll.data} title="会话快照" />
      ) : null}

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-medium">最近成交</h2>
          <Link to="/trades" className="text-sm text-amber-400 hover:underline">
            全部
          </Link>
        </div>
        <TradesTable items={tradesPoll.data?.items.slice(0, 8) ?? []} />
      </section>

      <p className="text-xs text-zinc-600">
        审计：<Link to="/checkpoints" className="text-amber-500 hover:underline">决策回放</Link>
        {" · "}
        <Link to="/sessions" className="text-amber-500 hover:underline">会话</Link>
      </p>
    </div>
  );
}
