import type { BalanceResponse, TickerResponse } from "../types/api";

interface BalancePanelProps {
  balance: BalanceResponse | null;
  ticker: TickerResponse | null;
  tradingMode?: string;
  error?: string | null;
}

function baseAssetFromSymbol(symbol: string | undefined): string {
  if (!symbol) return "BTC";
  const compact = symbol.replace("/", "").toUpperCase();
  if (compact.endsWith("USDT")) return compact.slice(0, -4);
  if (compact.includes(":")) return compact.split(":")[0];
  return compact.slice(0, 3) || "BTC";
}

export default function BalancePanel({ balance, ticker, tradingMode, error }: BalancePanelProps) {
  if (error) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 text-sm text-rose-400">
        余额加载失败：{error}
      </div>
    );
  }

  const base = baseAssetFromSymbol(ticker?.symbol ?? undefined);
  const usdt = balance?.free.USDT ?? 0;
  const baseQty = balance?.free[base] ?? balance?.free.BTC ?? 0;
  const last = ticker?.last ?? null;
  const baseNotional = last != null ? baseQty * last : null;
  const modeLabel = tradingMode === "live" ? "Live 持仓" : "Demo 持仓";

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
      <p className="text-xs uppercase tracking-wide text-zinc-500">{modeLabel}</p>
      <div className="mt-3 grid grid-cols-2 gap-4">
        <div>
          <p className="text-xs text-zinc-500">USDT</p>
          <p className="mono text-xl font-semibold">{usdt.toFixed(2)}</p>
        </div>
        <div>
          <p className="text-xs text-zinc-500">{base}</p>
          <p className="mono text-xl font-semibold">{baseQty.toFixed(6)}</p>
          {baseNotional != null ? (
            <p className="text-xs text-zinc-500">≈ {baseNotional.toFixed(2)} USDT</p>
          ) : null}
        </div>
      </div>
      {ticker ? (
        <p className="mt-3 text-xs text-zinc-500">
          {ticker.symbol} · 最新价{" "}
          <span className="mono text-amber-400">{ticker.last?.toLocaleString()}</span>
        </p>
      ) : null}
    </div>
  );
}
