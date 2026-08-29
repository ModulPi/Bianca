import type { RiskEventItem } from "../types/api";

export default function RiskEventsTable({ items }: { items: RiskEventItem[] }) {
  if (items.length === 0) {
    return <p className="text-sm text-zinc-500">暂无风控事件</p>;
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-zinc-800">
      <table className="w-full text-sm">
        <thead className="bg-zinc-900/80 text-left text-xs uppercase text-zinc-500">
          <tr>
            <th className="px-4 py-3">时间</th>
            <th className="px-4 py-3">类型</th>
            <th className="px-4 py-3">详情</th>
            <th className="px-4 py-3">关联交易</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800">
          {items.map((e) => (
            <tr key={e.id} className="hover:bg-zinc-900/40">
              <td className="px-4 py-3 mono text-xs text-zinc-400 whitespace-nowrap">
                {new Date(e.created_at).toLocaleString()}
              </td>
              <td className="px-4 py-3 text-rose-300">{e.event_type}</td>
              <td className="px-4 py-3 max-w-lg truncate text-zinc-400">
                {JSON.stringify(e.detail)}
              </td>
              <td className="px-4 py-3 mono text-xs text-zinc-500">
                {e.related_trade_id ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
