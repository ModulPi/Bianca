import { Link } from "react-router-dom";
import { api } from "../api/client";
import ActiveTradesSection from "../components/ActiveTradesSection";
import AgentControl from "../components/AgentControl";
import ExecutionModeBanner from "../components/ExecutionModeBanner";
import PnLPanel from "../components/PnLPanel";
import PositionsPanel from "../components/PositionsPanel";
import TickerPanel from "../components/TickerPanel";
import TokenUsagePanel from "../components/TokenUsagePanel";
import TradingModePanel from "../components/TradingModePanel";
import WorkerStatusPanel from "../components/WorkerStatusPanel";
import { usePolling } from "../hooks/usePolling";

export default function DashboardPage() {
  const snapshotPoll = usePolling(() => api.dashboardSnapshot(), 5000);
  const snap = snapshotPoll.data;
  const status = snap?.agent ?? null;
  const symbols = status?.symbols ?? [];

  const showConfirm =
    status?.execution_mode === "semi_auto" || status?.degraded;

  const balanceError = snap?.balance_error ?? snapshotPoll.error;
  const tickersError = snap?.tickers_error ?? null;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Agent 运维看板</h1>
          <p className="text-sm text-zinc-500">
            24×7 自主交易 · 多 Worker 并行 · 异常降级人工介入
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs text-zinc-500">
          <span className="rounded bg-zinc-900 px-2 py-1 ring-1 ring-zinc-800">
            {snap?.trading_mode.mode ?? "demo"} · {status?.trade_market ?? "crypto"}
          </span>
          {symbols.length > 0 ? (
            <span className="rounded bg-zinc-900 px-2 py-1 ring-1 ring-zinc-800">
              {symbols.join(", ")}
            </span>
          ) : null}
          <span className="rounded bg-zinc-900 px-2 py-1 ring-1 ring-zinc-800">
            snapshot 5s
          </span>
        </div>
      </header>

      {snapshotPoll.error && !snap ? (
        <div className="rounded-xl border border-rose-900/50 bg-rose-950/20 px-4 py-3 text-sm text-rose-300">
          看板加载失败：{snapshotPoll.error}
        </div>
      ) : null}

      <ExecutionModeBanner status={status} />

      {/* A | B | C */}
      <div className="grid gap-4 lg:grid-cols-3">
        <AgentControl
          status={status}
          onChange={() => {
            void snapshotPoll.refresh();
          }}
        />
        <TradingModePanel
          tradingMode={snap?.trading_mode ?? null}
          validation={snap?.validation ?? null}
          health={snap?.health ?? null}
          tradeMarket={status?.trade_market}
        />
        <TokenUsagePanel
          usage={snap?.usage ?? null}
          session={snap?.session ?? null}
          workerUsage={snap?.worker_token_usage}
          error={snapshotPoll.error}
        />
      </div>

      {/* D */}
      <WorkerStatusPanel
        workers={status?.workers ?? []}
        tradeMarket={status?.trade_market}
        symbols={symbols}
        sessionId={status?.session_id}
      />

      {/* E | F */}
      <div className="grid gap-4 xl:grid-cols-2">
        <PositionsPanel
          balance={snap?.balance ?? null}
          tickers={snap?.tickers ?? []}
          symbols={symbols}
          session={snap?.session ?? null}
          snapshotPositions={snap?.positions}
          error={balanceError ?? tickersError}
        />
        <PnLPanel
          summary={snap?.session ?? null}
          agentStatus={status}
          tradesForChart={snap?.chart_trades ?? []}
        />
      </div>

      {/* G */}
      <ActiveTradesSection
        submitted={snap?.open_trades ?? []}
        recentFilled={snap?.recent_filled ?? []}
        showConfirmQueue={showConfirm ?? false}
        riskEvents={snap?.risk_events ?? []}
      />

      {/* H */}
      <TickerPanel
        tickers={snap?.tickers ?? []}
        loading={snapshotPoll.loading && !snap}
        error={tickersError}
      />

      <p className="text-xs text-zinc-600">
        审计：
        <Link to="/checkpoints" className="text-amber-500 hover:underline">
          决策回放
        </Link>
        {" · "}
        <Link to="/sessions" className="text-amber-500 hover:underline">
          会话汇总
        </Link>
        {" · "}
        <Link to="/trades" className="text-amber-500 hover:underline">
          成交明细
        </Link>
      </p>
    </div>
  );
}
