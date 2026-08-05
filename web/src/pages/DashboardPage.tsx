import { useCallback } from "react";
import { Link } from "react-router-dom";
import { api, fetchDashboardSummary } from "../api/client";
import AgentControl from "../components/AgentControl";
import BalancePanel from "../components/BalancePanel";
import ExecutionModeBanner from "../components/ExecutionModeBanner";
import ConfirmQueue from "../components/ConfirmQueue";
import LoopClosedBadge from "../components/LoopClosedBadge";
import PnLChart from "../components/PnLChart";
import PositionsTable from "../components/PositionsTable";
import SessionSummaryPanel from "../components/SessionSummaryPanel";
import StatCard from "../components/StatCard";
import SystemHealthPanel from "../components/SystemHealthPanel";
import TradesTable from "../components/TradesTable";
import { usePolling } from "../hooks/usePolling";

async function fetchBalanceTicker() {
  try {
    const [balance, ticker] = await Promise.all([api.balance(), api.ticker()]);
    return { balance, ticker, error: null as string | null };
  } catch (err) {
    return {
      balance: null,
      ticker: null,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

export default function DashboardPage() {
  const statusPoll = usePolling(() => api.agentStatus(), 5000);

  const summaryFetcher = useCallback(async () => {
    return fetchDashboardSummary(statusPoll.data?.running ?? false);
  }, [statusPoll.data?.running]);

  const summaryPoll = usePolling(summaryFetcher, 8000, statusPoll.data !== null);
  const tradesPoll = usePolling(() => api.trades({ limit: 50 }), 15000);
  const healthPoll = usePolling(() => api.health(), 30000);
  const balancePoll = usePolling(fetchBalanceTicker, 20000);
  const dailyPoll = usePolling(() => api.summaryDaily(), 60000);
  const positionsPoll = usePolling(() => api.positions(undefined, 10), 20000);

  const health = healthPoll.data;
  const sessionId = statusPoll.data?.session_id;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">仪表盘</h1>
        <p className="text-sm text-zinc-500">持仓 · 盈亏曲线 · 会话汇总 · Agent 控制</p>
      </header>

      <ExecutionModeBanner status={statusPoll.data} />

      <SystemHealthPanel health={health} />

      {statusPoll.data?.execution_mode === "semi_auto" && (
        <section>
          <h2 className="mb-3 text-lg font-medium">待确认信号</h2>
          <ConfirmQueue />
        </section>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <AgentControl
          status={statusPoll.data}
          onChange={() => {
            void statusPoll.refresh();
            void summaryPoll.refresh();
          }}
        />
        <BalancePanel
          balance={balancePoll.data?.balance ?? null}
          ticker={balancePoll.data?.ticker ?? null}
          error={balancePoll.data?.error}
        />
        <div className="grid grid-cols-1 gap-3">
          <StatCard
            label="系统"
            value={health?.status ?? "—"}
            tone={health?.status === "ok" ? "positive" : "warn"}
          />
          <StatCard label="LLM" value={health?.llm ?? "—"} hint={health?.llm_provider} />
        </div>
      </div>

      <PnLChart trades={tradesPoll.data?.items ?? []} />

      {positionsPoll.data?.schema_mode === "mvp" && (positionsPoll.data.items.length ?? 0) > 0 ? (
        <section>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-lg font-medium">DB 持仓快照</h2>
            <Link to="/positions" className="text-xs text-amber-400 hover:underline">
              查看全部
            </Link>
          </div>
          <PositionsTable items={positionsPoll.data.items.slice(0, 5)} />
        </section>
      ) : null}

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

      {sessionId ? (
        <p className="text-sm text-zinc-500">
          当前 thread / session：{" "}
          <Link to={`/checkpoints?thread=${sessionId}`} className="text-amber-400 hover:underline mono">
            {sessionId}
          </Link>
        </p>
      ) : null}

      {(dailyPoll.data?.items.length ?? 0) > 0 ? (
        <section>
          <h2 className="mb-3 text-lg font-medium">今日会话</h2>
          <div className="flex flex-wrap gap-2">
            {dailyPoll.data!.items.map((s) => (
              <Link
                key={s.session_id}
                to={`/sessions/${s.session_id}`}
                className="inline-flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900/50 px-3 py-2 text-sm hover:border-zinc-700"
              >
                <span className="mono text-xs text-zinc-500">{s.session_id.slice(0, 8)}…</span>
                <LoopClosedBadge closed={s.trades.loop_closed} />
              </Link>
            ))}
          </div>
        </section>
      ) : null}

      <section>
        <h2 className="mb-3 text-lg font-medium">最近成交</h2>
        <TradesTable items={tradesPoll.data?.items.slice(0, 10) ?? []} />
      </section>
    </div>
  );
}
