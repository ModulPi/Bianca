import type { EquityPoint } from "../types/api";
import { fmtTime, fmtUsdt } from "../utils/format";

interface EquityChartProps {
  points: EquityPoint[];
  height?: number;
}

/**
 * 权益曲线。数据来自 `equity_snapshots`（每 tick 一张），不是从成交反推 ——
 * 反推只能得到现金流，不是权益。
 */
export default function EquityChart({ points, height = 200 }: EquityChartProps) {
  if (points.length < 2) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 px-4 py-8 text-center text-sm text-zinc-500">
        权益快照不足两条，画不出曲线
      </div>
    );
  }

  const values = points.map((p) => p.equity);
  const first = values[0];
  const last = values[values.length - 1];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || Math.max(max * 0.001, 1);
  const w = 720;
  const padL = 64;
  const padR = 12;
  const padT = 16;
  const padB = 24;
  const innerW = w - padL - padR;
  const innerH = height - padT - padB;

  const x = (i: number) => padL + (i / Math.max(points.length - 1, 1)) * innerW;
  const y = (v: number) => padT + innerH - ((v - min) / span) * innerH;
  const line = points.map((p, i) => `${x(i)},${y(p.equity)}`).join(" ");
  const baselineY = y(first);
  const change = last - first;
  const changePct = first !== 0 ? (change / first) * 100 : null;

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-zinc-300">权益曲线</h3>
        <span className={`mono text-sm ${change >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
          {fmtUsdt(last)} · {change >= 0 ? "+" : ""}
          {change.toFixed(2)}
          {changePct !== null ? ` (${changePct >= 0 ? "+" : ""}${changePct.toFixed(2)}%)` : ""}
        </span>
      </div>
      <svg viewBox={`0 0 ${w} ${height}`} className="w-full" role="img" aria-label="权益曲线">
        {[min, (min + max) / 2, max].map((v) => (
          <g key={v}>
            <line x1={padL} y1={y(v)} x2={w - padR} y2={y(v)} stroke="#27272a" strokeWidth="1" />
            <text x={padL - 6} y={y(v) + 4} textAnchor="end" fill="#71717a" fontSize="10">
              {v.toFixed(2)}
            </text>
          </g>
        ))}
        <line
          x1={padL}
          y1={baselineY}
          x2={w - padR}
          y2={baselineY}
          stroke="#52525b"
          strokeDasharray="4 4"
        />
        <polyline fill="none" stroke="#fbbf24" strokeWidth="2" points={line} />
      </svg>
      <div className="mt-2 flex flex-wrap justify-between gap-2 text-xs text-zinc-500">
        <span>{fmtTime(points[0].captured_at)}</span>
        <span>
          {points.length} 个快照 · 区间 {fmtUsdt(min)} ~ {fmtUsdt(max)}
        </span>
        <span>{fmtTime(points[points.length - 1].captured_at)}</span>
      </div>
    </div>
  );
}
