import type { AgentStatus, SessionSummary, TradeLogItem } from "../types/api";
import LoopClosedBadge from "./LoopClosedBadge";
import PnLChart from "./PnLChart";
import StatCard from "./StatCard";

function fmtUsdt(v: number) {
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(4)} USDT`;
}

function tone(v: number): "positive" | "negative" | "default" {
  if (v > 0) return "positive";
  if (v < 0) return "negative";
  return "default";
}

interface PnLPanelProps {
  summary: SessionSummary | null;
  agentStatus: AgentStatus | null;
  tradesForChart: TradeLogItem[];
}

export default function PnLPanel({ summary, agentStatus, tradesForChart }: PnLPanelProps) {
  const pnl = summary?.pnl;
  const dailyLegacy = pnl?.daily_pnl_legacy ?? agentStatus?.daily_pnl ?? 0;

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-sm font-medium text-zinc-300">收益（PnL）</h2>
        {summary ? (
          <>
            <LoopClosedBadge closed={summary.trades.loop_closed} />
            <span className="text-xs text-zinc-500">
              {summary.ended_at ? "已关闭会话" : "当前会话"}
            </span>
          </>
        ) : (
          <span className="text-xs text-zinc-500">暂无会话汇总</span>
        )}
      </div>

      {pnl ? (
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
          <StatCard label="已实现" value={fmtUsdt(pnl.realized_usdt)} tone={tone(pnl.realized_usdt)} />
          <StatCard
            label="未实现"
            value={fmtUsdt(pnl.unrealized_usdt)}
            tone={tone(pnl.unrealized_usdt)}
          />
          <StatCard
            label="现金净流入"
            value={fmtUsdt(pnl.cash_flow_usdt)}
            tone={tone(pnl.cash_flow_usdt)}
          />
          <StatCard label="合计" value={fmtUsdt(pnl.total_usdt)} tone={tone(pnl.total_usdt)} />
        </div>
      ) : (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 px-4 py-6 text-sm text-zinc-500">
          启动 Agent 后将展示会话级 PnL
        </div>
      )}

      <StatCard
        label="当日 legacy PnL"
        value={fmtUsdt(dailyLegacy)}
        tone={tone(dailyLegacy)}
        hint="agent/status · 24×7 模式下仅供参考"
      />

      <PnLChart trades={tradesForChart} />
    </section>
  );
}
