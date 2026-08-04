import { Link } from "react-router-dom";
import { useCallback, useMemo } from "react";
import { api, fetchDashboardSummary, fetchTickersForSymbols } from "../api/client";
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
  const statusPoll = usePolling(() => api.agentStatus(), 5000);
  const status = statusPoll.data;
  const symbols = status?.symbols ?? [];

  const summaryFetcher = useCallback(async () => {
    return fetchDashboardSummary(status?.running ?? false);
  }, [status?.running]);
  const summaryPoll = usePolling(summaryFetcher, 15000, statusPoll.data !== null);

  const usagePoll = usePolling(() => api.usage(), 60000);
  const healthPoll = usePolling(() => api.health(), 30000);
  const tradingModePoll = usePolling(() => api.tradingMode(), 30000);
  const validationPoll = usePolling(() => api.validationStatus(), 60000);

  const balancePoll = usePolling(() => api.balance(), 20000);
  const tickersFetcher = useCallback(
    () => fetchTickersForSymbols(symbols),
    [symbols.join(",")],
  );
  const tickersPoll = usePolling(tickersFetcher, 10000, symbols.length > 0);

  const allTradesPoll = usePolling(() => api.trades({ limit: 100 }), 15000);
  const submittedPoll = usePolling(() => api.trades({ status: "submitted", limit: 20 }), 15000);
  const filledPoll = usePolling(() => api.trades({ status: "filled", limit: 10 }), 15000);
  const riskPoll = usePolling(() => api.riskEvents(10), 15000);

  const showConfirm =
    status?.execution_mode === "semi_auto" || status?.degraded;

  const chartTrades = useMemo(
    () => allTradesPoll.data?.items ?? [],
    [allTradesPoll.data],
  );

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
            {tradingModePoll.data?.mode ?? "demo"} · {status?.trade_market ?? "crypto"}
          </span>
          {symbols.length > 0 ? (
            <span className="rounded bg-zinc-900 px-2 py-1 ring-1 ring-zinc-800">
              {symbols.join(", ")}
            </span>
          ) : null}
        </div>
      </header>

      <ExecutionModeBanner status={status ?? null} />

      {/* A | B | C */}
      <div className="grid gap-4 lg:grid-cols-3">
        <AgentControl
          status={status ?? null}
          onChange={() => {
            void statusPoll.refresh();
            void summaryPoll.refresh();
          }}
        />
        <TradingModePanel
          tradingMode={tradingModePoll.data}
          validation={validationPoll.data}
          health={healthPoll.data}
          tradeMarket={status?.trade_market}
        />
        <TokenUsagePanel
          usage={usagePoll.data}
          session={summaryPoll.data}
          error={usagePoll.error}
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
          balance={balancePoll.data}
          tickers={tickersPoll.data ?? []}
          symbols={symbols}
          session={summaryPoll.data}
          error={balancePoll.error ?? tickersPoll.error}
        />
        <PnLPanel
          summary={summaryPoll.data}
          agentStatus={status ?? null}
          tradesForChart={chartTrades}
        />
      </div>

      {/* G */}
      <ActiveTradesSection
        submitted={submittedPoll.data?.items ?? []}
        recentFilled={filledPoll.data?.items ?? []}
        showConfirmQueue={showConfirm ?? false}
        riskEvents={riskPoll.data?.items ?? []}
      />

      {/* H */}
      <TickerPanel tickers={tickersPoll.data ?? []} loading={tickersPoll.loading} />

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
