/** 展示口径：null / undefined 一律显示「—」，绝不显示 0。 */

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtUsdt(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${fmtNum(v, digits)} USDT`;
}

/** 币数量：最多 8 位，去掉无意义的尾零。 */
export function fmtQty(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v
    .toFixed(8)
    .replace(/(\.\d*?)0+$/, "$1")
    .replace(/\.$/, "");
}

export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(digits)}%`;
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function fmtTimeShort(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString();
}

/** 毫秒时间戳（行情库的 time 列）。 */
export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  return new Date(ms).toLocaleString();
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`;
  return `${Math.floor(seconds / 86400)}d ${Math.round((seconds % 86400) / 3600)}h`;
}

export function toneClass(tone: "default" | "positive" | "negative" | "warn" | "muted"): string {
  switch (tone) {
    case "positive":
      return "text-emerald-400";
    case "negative":
      return "text-rose-400";
    case "warn":
      return "text-amber-400";
    case "muted":
      return "text-zinc-500";
    default:
      return "text-zinc-100";
  }
}

/** 交易/订单状态色。`manual_close` 用琥珀色，与 `approved` 明确区分。 */
export function statusTone(status: string | null | undefined): string {
  const s = (status ?? "").toLowerCase();
  if (["filled", "closed", "active", "ok"].includes(s)) return "text-emerald-400";
  if (["failed", "rejected", "error", "aborted"].includes(s)) return "text-rose-400";
  if (["manual_close", "goal_achieved", "force_close", "risk_force_close", "proposed", "pending"].includes(s))
    return "text-amber-400";
  return "text-zinc-400";
}
