import type { TradeLogItem } from "../types/api";

interface TradesTableProps {
  items: TradeLogItem[];
  showSymbol?: boolean;
}

const statusColor: Record<string, string> = {
  filled: "text-emerald-400",
  failed: "text-rose-400",
  signal_only: "text-zinc-400",
};

export default function TradesTable({ items, showSymbol = false }: TradesTableProps) {
  if (items.length === 0) {
    return <p className="text-sm text-zinc-500">暂无交易记录</p>;
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-zinc-800">
      <table className="w-full text-sm">
        <thead className="bg-zinc-900/80 text-left text-xs uppercase text-zinc-500">
          <tr>
            <th className="px-4 py-3">时间</th>
            {showSymbol ? <th className="px-4 py-3">Symbol</th> : null}
            <th className="px-4 py-3">方向</th>
            <th className="px-4 py-3">数量</th>
            <th className="px-4 py-3">价格</th>
            <th className="px-4 py-3">状态</th>
            <th className="px-4 py-3">风控</th>
            <th className="px-4 py-3">理由</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800">
          {items.map((t) => (
            <tr key={t.id} className="hover:bg-zinc-900/40">
              <td className="px-4 py-3 mono text-xs text-zinc-400 whitespace-nowrap">
                {new Date(t.created_at).toLocaleString()}
              </td>
              {showSymbol ? (
                <td className="px-4 py-3 mono text-amber-300">{t.symbol}</td>
              ) : null}
              <td className="px-4 py-3">
                <span
                  className={
                    t.side === "BUY" ? "text-emerald-400 font-medium" : "text-rose-400 font-medium"
                  }
                >
                  {t.side}
                </span>
              </td>
              <td className="px-4 py-3 mono">{t.quantity ?? "—"}</td>
              <td className="px-4 py-3 mono">{t.price ?? "—"}</td>
              <td className={`px-4 py-3 ${statusColor[t.status] ?? "text-zinc-300"}`}>
                {t.status}
              </td>
              <td className="px-4 py-3 text-zinc-400">{t.risk_decision ?? "—"}</td>
              <td className="px-4 py-3 max-w-xs truncate text-zinc-400" title={t.decision_reason}>
                {t.decision_reason}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
