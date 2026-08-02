import type { SessionSummary } from "../types/api";
import LoopClosedBadge from "./LoopClosedBadge";
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

interface SessionSummaryPanelProps {
  summary: SessionSummary;
  title?: string;
}

export default function SessionSummaryPanel({ summary, title }: SessionSummaryPanelProps) {
  const { pnl, usage, trades, positions, agent, highlights } = summary;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        {title ? <h2 className="text-lg font-semibold">{title}</h2> : null}
        <LoopClosedBadge closed={trades.loop_closed} />
        <span className="text-xs text-zinc-500">
          {summary.ended_at ? "已关闭" : "进行中"} · {agent.trading_style}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="已实现盈亏" value={fmtUsdt(pnl.realized_usdt)} tone={tone(pnl.realized_usdt)} />
        <StatCard label="未实现盈亏" value={fmtUsdt(pnl.unrealized_usdt)} tone={tone(pnl.unrealized_usdt)} />
        <StatCard label="现金净流入" value={fmtUsdt(pnl.cash_flow_usdt)} tone={tone(pnl.cash_flow_usdt)} />
        <StatCard label="总盈亏" value={fmtUsdt(pnl.total_usdt)} tone={tone(pnl.total_usdt)} />
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="LLM 调用" value={String(usage.llm_calls)} />
        <StatCard label="Token 总计" value={usage.total_tokens.toLocaleString()} />
        <StatCard
          label="成交 BUY/SELL"
          value={`${trades.buy_filled} / ${trades.sell_filled}`}
          hint={`失败 ${trades.failed} · 仅信号 ${trades.signal_only}`}
        />
        <StatCard
          label="持仓"
          value={`${positions.base_free} ${positions.base_asset}`}
          hint={`USDT ${positions.usdt_free.toFixed(2)}`}
        />
      </div>

      {highlights.length > 0 ? (
        <ul className="rounded-xl border border-zinc-800 bg-zinc-900/40 px-4 py-3 text-sm text-zinc-400 space-y-1">
          {highlights.map((h) => (
            <li key={h}>· {h}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
