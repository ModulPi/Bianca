import { buildCashFlowSeries, formatUsdt } from "../utils/pnl";
import type { TradeLogItem } from "../types/api";

interface PnLChartProps {
  trades: TradeLogItem[];
  height?: number;
}

export default function PnLChart({ trades, height = 160 }: PnLChartProps) {
  const series = buildCashFlowSeries(trades);
  if (series.length <= 1) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 px-4 py-8 text-center text-sm text-zinc-500">
        暂无 filled 成交，无法绘制盈亏曲线
      </div>
    );
  }

  const values = series.map((p) => p.value);
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 0);
  const span = max - min || 1;
  const w = 640;
  const pad = 24;
  const innerH = height - pad * 2;

  const coords = series.map((p, i) => {
    const x = pad + (i / Math.max(series.length - 1, 1)) * (w - pad * 2);
    const y = pad + innerH - ((p.value - min) / span) * innerH;
    return { x, y, ...p };
  });

  const line = coords.map((c) => `${c.x},${c.y}`).join(" ");
  const zeroY = pad + innerH - ((0 - min) / span) * innerH;
  const last = coords[coords.length - 1];

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-medium text-zinc-300">累计现金净流入</h3>
        <span
          className={`mono text-sm ${last.value >= 0 ? "text-emerald-400" : "text-rose-400"}`}
        >
          {formatUsdt(last.value)} USDT
        </span>
      </div>
      <svg viewBox={`0 0 ${w} ${height}`} className="w-full" role="img" aria-label="PnL chart">
        <line x1={pad} y1={zeroY} x2={w - pad} y2={zeroY} stroke="#3f3f46" strokeDasharray="4 4" />
        <polyline fill="none" stroke="#fbbf24" strokeWidth="2" points={line} />
        {coords.map((c, i) =>
          i === 0 ? null : (
            <circle
              key={c.at + i}
              cx={c.x}
              cy={c.y}
              r="4"
              fill={c.side === "SELL" ? "#34d399" : "#fb7185"}
            />
          ),
        )}
      </svg>
      <div className="mt-2 flex flex-wrap gap-3 text-xs text-zinc-500">
        {coords.slice(1).map((c) => (
          <span key={c.at} title={c.label}>
            {new Date(c.at).toLocaleTimeString()} · {c.side} · {formatUsdt(c.value)}
          </span>
        ))}
      </div>
    </div>
  );
}
