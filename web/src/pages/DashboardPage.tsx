import { useCallback } from "react";
import { Link } from "react-router-dom";
import ActiveTradesSection from "../components/ActiveTradesSection";
import AgentControl from "../components/AgentControl";
import CollapsibleSection from "../components/CollapsibleSection";
import ExecutionModeBanner from "../components/ExecutionModeBanner";
import KlineChartPanel from "../components/KlineChartPanel";
import PnLPanel from "../components/PnLPanel";
import PositionsPanel from "../components/PositionsPanel";
import SessionSummaryPanel from "../components/SessionSummaryPanel";
import SystemHealthPanel from "../components/SystemHealthPanel";
import TickerPanel from "../components/TickerPanel";
import TokenUsagePanel from "../components/TokenUsagePanel";
import TradingModePanel from "../components/TradingModePanel";
import WorkerStatusPanel from "../components/WorkerStatusPanel";
import { useSnapshotPolling } from "../hooks/useSnapshotPolling";
import { useSystemWebSocket } from "../hooks/useSystemWebSocket";

const QUICK_LINKS = [
  { to: "/trades", label: "成交明细" },
  { to: "/sessions", label: "会话汇总" },
  { to: "/decisions", label: "决策回放" },
  { to: "/checkpoints", label: "Checkpoints" },
  { to: "/risk", label: "风控事件" },
] as const;

export default function DashboardPage() {
  const { data: snap, error, loading, forceRefresh, notModifiedCount } = useSnapshotPolling(5000);

  const onWsEvent = useCallback(
    (event: { type: string }) => {
      if (
        event.type === "confirmation_required" ||
        event.type === "agent_status_changed" ||
        event.type === "trade_executed"
      ) {
        void forceRefresh();
      }
    },
    [forceRefresh],
  );

  const { connected: wsConnected } = useSystemWebSocket(onWsEvent);

  const agent = snap?.agent ?? null;
  const session = snap?.session ?? null;
  const mode = snap?.trading_mode?.mode ?? snap?.validation?.trading_mode ?? "demo";
  const showConfirmQueue =
    Boolean(agent?.degraded) ||
    agent?.execution_mode === "semi_auto" ||
    (snap?.pending_signals?.length ?? 0) > 0;

  const chartTrades = snap?.chart_trades ?? snap?.recent_filled ?? [];
  const symbols =
    agent?.symbols ??
    (snap?.tickers ?? []).map((t) => t.symbol).filter(Boolean) as string[];

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Agent 运维看板</h1>
          <p className="mt-1 text-sm text-zinc-500">
            24×7 运行态 · Worker · 仓位 · PnL · Token · 降级确认
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
          <span
            className={`rounded px-2 py-0.5 font-medium ${
              mode === "live"
                ? "bg-rose-950 text-rose-300 ring-1 ring-rose-800"
                : "bg-sky-950 text-sky-300 ring-1 ring-sky-800"
            }`}
          >
            {mode === "live" ? "Live" : "Demo"}
          </span>
          <span className="rounded bg-zinc-800 px-2 py-0.5">{agent?.trade_market ?? "crypto"}</span>
          <span className="flex items-center gap-1.5">
            <span
              className={`h-1.5 w-1.5 rounded-full ${wsConnected ? "bg-emerald-400" : "bg-zinc-600"}`}
            />
            WS {wsConnected ? "已连接" : "重连中"}
          </span>
          {snap?.generated_at ? (
            <span className="mono text-[10px] text-zinc-600">
              快照 {new Date(snap.generated_at).toLocaleTimeString()}
              {notModifiedCount > 0 ? ` · 304×${notModifiedCount}` : ""}
            </span>
          ) : null}
        </div>
      </header>

      {error ? (
        <div className="rounded-xl border border-rose-900/50 bg-rose-950/20 px-4 py-3 text-sm text-rose-300">
          看板加载失败：{error}
          <button
            type="button"
            onClick={() => void forceRefresh()}
            className="ml-3 text-rose-400 underline hover:text-rose-200"
          >
            重试
          </button>
        </div>
      ) : null}

      {loading && !snap ? (
        <p className="text-sm text-zinc-500">加载运维快照…</p>
      ) : null}

      <ExecutionModeBanner status={agent} />

      {/* [A][B][C] 运行态 · 门禁 · Token */}
      <div className="grid gap-4 lg:grid-cols-3">
        <AgentControl status={agent} onChange={() => void forceRefresh()} />
        <TradingModePanel
          tradingMode={snap?.trading_mode ?? null}
          validation={snap?.validation ?? null}
          health={snap?.health ?? null}
          tradeMarket={agent?.trade_market}
        />
        <TokenUsagePanel
          usage={snap?.usage ?? null}
          session={session}
          workerUsage={snap?.worker_token_usage}
        />
      </div>

      {/* [D] Worker 表 */}
      <WorkerStatusPanel
        workers={agent?.workers ?? []}
        tradeMarket={agent?.trade_market}
        symbols={agent?.symbols}
        sessionId={agent?.session_id}
      />

      {/* [E][F] 仓位 · PnL */}
      <div className="grid gap-4 xl:grid-cols-2">
        <PositionsPanel
          balance={snap?.balance ?? null}
          tickers={snap?.tickers ?? []}
          symbols={symbols}
          session={session}
          snapshotPositions={snap?.positions}
          tradeMarket={agent?.trade_market}
          error={snap?.balance_error ?? snap?.tickers_error ?? null}
        />
        <PnLPanel summary={session} agentStatus={agent} tradesForChart={chartTrades} />
      </div>

      {/* [G] 进行中交易 · 确认队列 */}
      <CollapsibleSection
        title="进行中交易 · 降级确认"
        badge={
          showConfirmQueue
            ? String(snap?.pending_signals?.length ?? 0)
            : undefined
        }
        defaultOpen={showConfirmQueue}
      >
        <ActiveTradesSection
          embedded
          submitted={snap?.open_trades ?? []}
          recentFilled={snap?.recent_filled ?? []}
          pendingSignals={snap?.pending_signals ?? []}
          showConfirmQueue={showConfirmQueue}
          riskEvents={snap?.risk_events ?? []}
          onRefresh={() => void forceRefresh()}
        />
      </CollapsibleSection>

      {/* [H] 实时行情 */}
      <TickerPanel
        tickers={snap?.tickers ?? []}
        loading={loading && !snap}
        error={snap?.tickers_error}
      />

      {/* 会话亮点 + 基础设施 */}
      {session && (session.highlights?.length ?? 0) > 0 ? (
        <CollapsibleSection title="会话亮点" defaultOpen={false}>
          <SessionSummaryPanel
            summary={session}
            title={agent?.running ? "当前会话" : "最近会话"}
          />
        </CollapsibleSection>
      ) : session ? (
        <CollapsibleSection title="会话汇总" defaultOpen={false}>
          <SessionSummaryPanel
            summary={session}
            title={agent?.running ? "当前会话" : "最近会话"}
          />
        </CollapsibleSection>
      ) : (
        <p className="text-sm text-zinc-500">暂无会话汇总，启动 Agent 后将自动生成。</p>
      )}

      {agent?.session_id ? (
        <p className="text-sm text-zinc-500">
          决策回放 thread：{" "}
          <Link
            to={`/checkpoints?thread=${agent.session_id}`}
            className="mono text-amber-400 hover:underline"
          >
            {agent.session_id}
          </Link>
        </p>
      ) : null}

      <CollapsibleSection title="基础设施健康" defaultOpen={false}>
        <SystemHealthPanel health={snap?.health ?? null} />
      </CollapsibleSection>

      {(symbols?.length ?? 0) > 0 ? (
        <CollapsibleSection title="K 线 · 买卖点标记" defaultOpen={false} badge="可选">
          <KlineChartPanel symbols={symbols} trades={chartTrades} />
        </CollapsibleSection>
      ) : null}

      <nav className="flex flex-wrap gap-3 border-t border-zinc-800 pt-4 text-sm">
        {QUICK_LINKS.map((link) => (
          <Link key={link.to} to={link.to} className="text-amber-400/90 hover:text-amber-300 hover:underline">
            {link.label}
          </Link>
        ))}
      </nav>
    </div>
  );
}
