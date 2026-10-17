import type { HoldingGapItem, PortfolioResponse } from "../types/api";
import { fmtNum, fmtQty, fmtTime, fmtUsdt } from "../utils/format";

interface PositionsTableProps {
  portfolio: PortfolioResponse | null;
}

export default function PositionsTable({ portfolio }: PositionsTableProps) {
  if (!portfolio) {
    return (
      <p className="rounded-xl border border-zinc-800 px-4 py-8 text-center text-sm text-zinc-500">
        还没有权益快照 —— 先点「采集快照」向交易所读一次
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric label="权益" value={fmtUsdt(portfolio.equity)} />
        <Metric label="现金" value={fmtUsdt(portfolio.cash_quote)} />
        <Metric label="持仓市值" value={fmtUsdt(portfolio.holdings_value)} />
        <Metric label="快照时间" value={fmtTime(portfolio.captured_at)} small />
      </div>

      {portfolio.unpriced.length > 0 ? (
        <p className="rounded-lg border border-amber-900/60 bg-amber-950/20 px-3 py-2 text-xs text-amber-300">
          取不到价：{portfolio.unpriced.join("、")} —— 这些持仓未计入权益，权益只是下界。
        </p>
      ) : null}

      {portfolio.positions.length === 0 ? (
        <p className="rounded-xl border border-zinc-800 px-4 py-6 text-center text-sm text-zinc-500">
          账户全是现金，没有持仓
        </p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-zinc-800">
          <table className="w-full text-sm">
            <thead className="bg-zinc-900/80 text-left text-xs uppercase text-zinc-500">
              <tr>
                <th className="px-4 py-3">资产</th>
                <th className="px-4 py-3">数量</th>
                <th className="px-4 py-3">价格</th>
                <th className="px-4 py-3">名义价值</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800">
              {portfolio.positions.map((p) => (
                <tr key={p.asset} className="hover:bg-zinc-900/40">
                  <td className="px-4 py-3 font-medium text-amber-300">{p.asset}</td>
                  <td className="px-4 py-3 mono">{fmtQty(p.quantity)}</td>
                  <td className="px-4 py-3 mono">{fmtUsdt(p.price)}</td>
                  <td className="px-4 py-3 mono">
                    {p.value_quote === null ? (
                      <span className="text-amber-400">取不到价</span>
                    ) : (
                      fmtUsdt(p.value_quote)
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <GapsTable gaps={portfolio.gaps} />
      {portfolio.notes.length > 0 ? (
        <p className="text-xs text-zinc-500">说明：{portfolio.notes.join("；")}</p>
      ) : null}
    </div>
  );
}

function Metric({ label, value, small }: { label: string; value: string; small?: boolean }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-3">
      <p className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</p>
      <p className={`mono mt-0.5 ${small ? "text-sm" : "text-lg"} font-semibold text-zinc-100`}>
        {value}
      </p>
    </div>
  );
}

function GapsTable({ gaps }: { gaps: HoldingGapItem[] }) {
  if (gaps.length === 0) return null;
  const nonzero = gaps.filter(
    (g) => Math.abs(g.exchange_qty - g.attributed_qty) > 1e-12 || g.unexplained_qty !== 0,
  );
  return (
    <div className="rounded-xl border border-zinc-800">
      <p className="border-b border-zinc-800 px-4 py-2 text-xs text-zinc-500">
        交易所持仓 vs 本地账本（差不为零不一定是错：账外持仓本来就解释不了）
      </p>
      <table className="w-full text-xs">
        <thead className="bg-zinc-900/80 text-left uppercase text-zinc-500">
          <tr>
            <th className="px-4 py-2">资产</th>
            <th className="px-4 py-2">交易所</th>
            <th className="px-4 py-2">账本归因</th>
            <th className="px-4 py-2">未解释</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800">
          {(nonzero.length > 0 ? nonzero : gaps).map((g) => (
            <tr key={g.asset} className={nonzero.length === 0 ? "text-zinc-600" : ""}>
              <td className="px-4 py-2 mono">{g.asset}</td>
              <td className="px-4 py-2 mono">{fmtNum(g.exchange_qty, 8)}</td>
              <td className="px-4 py-2 mono">{fmtNum(g.attributed_qty, 8)}</td>
              <td
                className={`px-4 py-2 mono ${
                  Math.abs(g.unexplained_qty) > 1e-12 ? "text-amber-400" : "text-zinc-500"
                }`}
              >
                {fmtNum(g.unexplained_qty, 8)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
