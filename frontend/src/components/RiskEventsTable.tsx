import type { RiskEventItem } from "../types/api";
import { fmtTime, statusTone } from "../utils/format";

export default function RiskEventsTable({ items }: { items: RiskEventItem[] }) {
  if (items.length === 0) {
    return (
      <p className="rounded-xl border border-zinc-800 px-4 py-8 text-center text-sm text-zinc-500">
        没有符合筛选条件的风控事件
      </p>
    );
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
            <tr key={e.id} className="align-top hover:bg-zinc-900/40">
              <td className="px-4 py-3 mono text-xs text-zinc-400 whitespace-nowrap">
                {fmtTime(e.created_at)}
              </td>
              <td className={`px-4 py-3 ${statusTone(e.event_type)}`}>{e.event_type}</td>
              <td className="px-4 py-3">
                <pre className="mono max-w-xl overflow-x-auto whitespace-pre-wrap break-words text-xs text-zinc-400">
                  {JSON.stringify(e.detail, null, 2)}
                </pre>
              </td>
              <td className="px-4 py-3 mono text-xs text-zinc-500">
                {e.related_trade_id ? `${e.related_trade_id.slice(0, 8)}…` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
