import type { TradeLogItem } from "../types/api";

export interface PnLPoint {
  at: string;
  value: number;
  side: string;
  label: string;
}

/** 按 filled 成交时间序列计算累计现金净流入（USDT） */
export function buildCashFlowSeries(trades: TradeLogItem[]): PnLPoint[] {
  const filled = trades
    .filter((t) => t.status === "filled" && t.price != null && t.quantity != null)
    .sort((a, b) => a.created_at.localeCompare(b.created_at));

  let cumulative = 0;
  const points: PnLPoint[] = [{ at: "", value: 0, side: "START", label: "起点" }];

  for (const t of filled) {
    const notional = (t.quantity ?? 0) * (t.price ?? 0);
    cumulative += t.side === "SELL" ? notional : -notional;
    points.push({
      at: t.created_at,
      value: cumulative,
      side: t.side,
      label: `${t.side} ${(t.quantity ?? 0).toFixed(6)} @ ${t.price}`,
    });
  }
  return points;
}

export function formatUsdt(v: number) {
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(4)}`;
}
