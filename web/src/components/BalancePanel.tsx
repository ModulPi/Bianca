import type { BalanceResponse, TickerResponse } from "../types/api";

interface BalancePanelProps {
  balance: BalanceResponse | null;
  ticker: TickerResponse | null;
  error?: string | null;
}

export default function BalancePanel({ balance, ticker, error }: BalancePanelProps) {
  if (error) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 text-sm text-rose-400">
        余额加载失败：{error}
      </div>
    );
  }

  const usdt = balance?.free.USDT ?? 0;
  const btc = balance?.free.BTC ?? 0;
  const last = ticker?.last ?? null;
  const btcNotional = last != null ? btc * last : null;

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
      <p className="text-xs uppercase tracking-wide text-zinc-500">Demo 持仓</p>
      <div className="mt-3 grid grid-cols-2 gap-4">
        <div>
          <p className="text-xs text-zinc-500">USDT</p>
          <p className="mono text-xl font-semibold">{usdt.toFixed(2)}</p>
        </div>
        <div>
          <p className="text-xs text-zinc-500">BTC</p>
          <p className="mono text-xl font-semibold">{btc.toFixed(6)}</p>
          {btcNotional != null ? (
            <p className="text-xs text-zinc-500">≈ {btcNotional.toFixed(2)} USDT</p>
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
