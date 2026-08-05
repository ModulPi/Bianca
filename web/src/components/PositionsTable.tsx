import type { PositionItem } from "../types/api";

interface PositionsTableProps {
  items: PositionItem[];
}

export default function PositionsTable({ items }: PositionsTableProps) {
  if (items.length === 0) {
    return <p className="text-sm text-zinc-500">暂无持仓快照</p>;
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-zinc-800">
      <table className="w-full text-sm">
        <thead className="bg-zinc-900/80 text-left text-xs uppercase text-zinc-500">
          <tr>
            <th className="px-4 py-3">资产</th>
            <th className="px-4 py-3">数量</th>
            <th className="px-4 py-3">市价</th>
            <th className="px-4 py-3">名义价值</th>
            <th className="px-4 py-3">策略</th>
            <th className="px-4 py-3">更新</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800">
          {items.map((p) => {
            const notional =
              p.current_price != null ? p.quantity * p.current_price : null;
            return (
              <tr key={p.id} className="hover:bg-zinc-900/40">
                <td className="px-4 py-3 font-medium">{p.symbol}</td>
                <td className="px-4 py-3 mono">{p.quantity.toFixed(8).replace(/\.?0+$/, "")}</td>
                <td className="px-4 py-3 mono">
                  {p.current_price != null ? p.current_price.toLocaleString() : "—"}
                </td>
                <td className="px-4 py-3 mono">
                  {notional != null ? `${notional.toFixed(2)} USDT` : "—"}
                </td>
                <td className="px-4 py-3 mono text-xs text-zinc-500">
                  {p.strategy_id.slice(0, 8)}…
                </td>
                <td className="px-4 py-3 mono text-xs text-zinc-500 whitespace-nowrap">
                  {new Date(p.updated_at).toLocaleString()}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
