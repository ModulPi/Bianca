import type { TradeLogItem } from "../types/api";
import { fmtQty, fmtTime, fmtUsdt, statusTone } from "../utils/format";

interface TradesTableProps {
  items: TradeLogItem[];
  showSymbol?: boolean;
  onSelect?: (id: string) => void;
  selectedId?: string | null;
}

export default function TradesTable({
  items,
  showSymbol = true,
  onSelect,
  selectedId,
}: TradesTableProps) {
  if (items.length === 0) {
    return (
      <p className="rounded-xl border border-zinc-800 px-4 py-8 text-center text-sm text-zinc-500">
        没有符合筛选条件的交易记录
      </p>
    );
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
            <th className="px-4 py-3">目标</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800">
          {items.map((t) => (
            <tr
              key={t.id}
              onClick={onSelect ? () => onSelect(t.id) : undefined}
              className={`${onSelect ? "cursor-pointer" : ""} ${
                selectedId === t.id ? "bg-zinc-800/60" : "hover:bg-zinc-900/40"
              }`}
            >
              <td className="px-4 py-3 mono text-xs text-zinc-400 whitespace-nowrap">
                {fmtTime(t.created_at)}
              </td>
              {showSymbol ? <td className="px-4 py-3 mono text-amber-300">{t.symbol}</td> : null}
              <td className="px-4 py-3">
                <span
                  className={
                    t.side === "BUY" ? "font-medium text-emerald-400" : "font-medium text-rose-400"
                  }
                >
                  {t.side}
                </span>
              </td>
              <td className="px-4 py-3 mono">{fmtQty(t.quantity)}</td>
              <td className="px-4 py-3 mono">{fmtUsdt(t.price)}</td>
              <td className={`px-4 py-3 ${statusTone(t.status)}`}>{t.status}</td>
              <td className={`px-4 py-3 ${statusTone(t.risk_decision)}`}>
                {t.risk_decision ?? "—"}
              </td>
              <td className="max-w-xs truncate px-4 py-3 text-zinc-400" title={t.decision_reason}>
                {t.decision_reason}
              </td>
              <td className="px-4 py-3 mono text-xs text-zinc-500">
                {t.goal_id ? `${t.goal_id.slice(0, 8)}…` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
