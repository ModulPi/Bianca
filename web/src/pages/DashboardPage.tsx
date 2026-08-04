import { Link } from "react-router-dom";
import { useCallback } from "react";
import ActiveTradesSection from "../components/ActiveTradesSection";
import AgentControl from "../components/AgentControl";
import CollapsibleSection from "../components/CollapsibleSection";
import ExecutionModeBanner from "../components/ExecutionModeBanner";
import PnLPanel from "../components/PnLPanel";
import PositionsPanel from "../components/PositionsPanel";
import TickerPanel from "../components/TickerPanel";
import TokenUsagePanel from "../components/TokenUsagePanel";
import TradingModePanel from "../components/TradingModePanel";
import WorkerStatusPanel from "../components/WorkerStatusPanel";
import { useSnapshotPolling } from "../hooks/useSnapshotPolling";
import { useSystemWebSocket } from "../hooks/useSystemWebSocket";

export default function DashboardPage() {
  const snapshotPoll = useSnapshotPolling(5000);
  const snap = snapshotPoll.data;
  const status = snap?.agent ?? null;
  const symbols = status?.symbols ?? [];

  const forceRefreshSnapshot = useCallback(() => {
    void snapshotPoll.forceRefresh();
  }, [snapshotPoll.forceRefresh]);

  const wsHandler = useCallback(
    (ev: { type: string }) => {
      if (
        ev.type === "confirmation_required" ||
        ev.type === "confirmation_executed" ||
        ev.type === "confirmation_rejected"
      ) {
        void snapshotPoll.forceRefresh();
      }
    },
    [snapshotPoll.forceRefresh],
  );

  const { connected: wsConnected } = useSystemWebSocket(wsHandler);

  const pendingCount = snap?.pending_signals.length ?? 0;
  const submittedCount = snap?.open_trades.length ?? 0;

  const showConfirm =
    status?.execution_mode === "semi_auto" ||
    status?.degraded ||
    pendingCount > 0;

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
          <span
            className={`rounded px-2 py-1 ring-1 ${
              wsConnected
                ? "bg-emerald-950/40 text-emerald-400 ring-emerald-900"
                : "bg-zinc-900 text-zinc-500 ring-zinc-800"
            }`}
          >
            WS {wsConnected ? "已连接" : "重连中"}
          </span>
          <span className="rounded bg-zinc-900 px-2 py-1 ring-1 ring-zinc-800">
            snapshot 5s
            {snap?.generated_at
              ? ` · ${new Date(snap.generated_at).toLocaleTimeString()}`
              : ""}
            {snapshotPoll.notModifiedCount > 0
              ? ` · 304×${snapshotPoll.notModifiedCount}`
              : ""}
          </span>
        </div>
      </header>

      {snapshotPoll.error && !snap ? (
        <div className="rounded-xl border border-rose-900/50 bg-rose-950/20 px-4 py-3 text-sm text-rose-300">
          看板加载失败：{snapshotPoll.error}
        </div>
      ) : null}

      <ExecutionModeBanner status={status} />

      {/* A | B | C — 始终可见 */}
      <div className="grid gap-4 lg:grid-cols-3">
        <AgentControl status={status} onChange={forceRefreshSnapshot} />
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

      {/* D — 始终可见 */}
      <WorkerStatusPanel
        workers={status?.workers ?? []}
        tradeMarket={status?.trade_market}
        symbols={symbols}
        sessionId={status?.session_id}
      />

      {/* E | F — 可折叠，默认展开 */}
      <CollapsibleSection title="仓位 · 收益" defaultOpen badge="E · F">
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
      </CollapsibleSection>

      {/* G — 有活动时默认展开 */}
      <CollapsibleSection
        title="进行中交易 · 降级确认"
        defaultOpen={showConfirm || submittedCount > 0 || pendingCount > 0}
        badge={`G · ${pendingCount + submittedCount}`}
      >
        <ActiveTradesSection
          submitted={snap?.open_trades ?? []}
          recentFilled={snap?.recent_filled ?? []}
          pendingSignals={snap?.pending_signals ?? []}
          showConfirmQueue={showConfirm}
          riskEvents={snap?.risk_events ?? []}
          onRefresh={forceRefreshSnapshot}
          embedded
        />
      </CollapsibleSection>

      {/* H — 默认收起 */}
      <CollapsibleSection title="实时行情" defaultOpen={false} badge="H">
        <TickerPanel
          tickers={snap?.tickers ?? []}
          loading={snapshotPoll.loading && !snap}
          error={tickersError}
        />
      </CollapsibleSection>

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
