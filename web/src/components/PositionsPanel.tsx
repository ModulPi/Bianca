import type { BalanceResponse, DashboardPosition, SessionSummary, TickerResponse } from "../types/api";
import { baseFromSymbol } from "../utils/symbol";

export interface PositionRow {
  symbol: string;
  base: string;
  free: number;
  used: number;
  mark: number | null;
  notionalUsdt: number | null;
}

interface PositionsPanelProps {
  balance: BalanceResponse | null;
  tickers: TickerResponse[];
  symbols: string[];
  session: SessionSummary | null;
  snapshotPositions?: DashboardPosition[];
  error?: string | null;
}

export function buildPositionRows(
  balance: BalanceResponse | null,
  tickers: TickerResponse[],
  symbols: string[],
): PositionRow[] {
  const tickerMap = new Map(tickers.map((t) => [t.symbol ?? "", t]));
  const list = symbols.length > 0 ? symbols : tickers.map((t) => t.symbol).filter(Boolean) as string[];

  return list.map((symbol) => {
    const base = baseFromSymbol(symbol);
    const free = balance?.free[base] ?? 0;
    const used = balance?.used[base] ?? 0;
    const mark = tickerMap.get(symbol)?.last ?? null;
    const notionalUsdt = mark != null ? free * mark : null;
    return { symbol, base, free, used, mark, notionalUsdt };
  });
}

export default function PositionsPanel({
  balance,
  tickers,
  symbols,
  session,
  snapshotPositions,
  error,
}: PositionsPanelProps) {
  if (error) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 text-sm text-rose-400">
        仓位加载失败：{error}
      </div>
    );
  }

  const usdtFree = balance?.free.USDT ?? 0;
  const usdtUsed = balance?.used.USDT ?? 0;
  const rows: PositionRow[] =
    snapshotPositions?.map((p) => ({
      symbol: p.symbol,
      base: p.base,
      free: p.free,
      used: p.used,
      mark: p.mark,
      notionalUsdt: p.notional_usdt,
    })) ?? buildPositionRows(balance, tickers, symbols);
  const totalNotional =
    rows.reduce((sum, r) => sum + (r.notionalUsdt ?? 0), 0) + usdtFree;

  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium text-zinc-300">仓位快照</h2>
        <span className="text-xs text-zinc-500">
          合计 ≈ {totalNotional.toFixed(2)} USDT
        </span>
      </div>

      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <div>
          <p className="text-xs text-zinc-500">USDT 可用</p>
          <p className="mono text-lg font-semibold">{usdtFree.toFixed(2)}</p>
        </div>
        <div>
          <p className="text-xs text-zinc-500">USDT 冻结</p>
          <p className="mono text-lg font-semibold text-zinc-400">{usdtUsed.toFixed(2)}</p>
        </div>
        {session?.positions ? (
          <div>
            <p className="text-xs text-zinc-500">会话持仓</p>
            <p className="mono text-sm text-zinc-300">
              {session.positions.base_free} {session.positions.base_asset}
            </p>
          </div>
        ) : null}
      </div>

      {rows.length === 0 ? (
        <p className="text-sm text-zinc-500">无 symbol 配置或未获取行情</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-zinc-800 text-zinc-500">
                <th className="py-2 pr-3">Symbol</th>
                <th className="py-2 pr-3">可用</th>
                <th className="py-2 pr-3">冻结</th>
                <th className="py-2 pr-3">标记价</th>
                <th className="py-2">名义 USDT</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.symbol} className="border-b border-zinc-800/50">
                  <td className="py-2 pr-3 mono text-amber-300">{r.symbol}</td>
                  <td className="py-2 pr-3 mono">{r.free.toFixed(6)}</td>
                  <td className="py-2 pr-3 mono text-zinc-500">{r.used.toFixed(6)}</td>
                  <td className="py-2 pr-3 mono">{r.mark?.toLocaleString() ?? "—"}</td>
                  <td className="py-2 mono">{r.notionalUsdt?.toFixed(2) ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
