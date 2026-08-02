import type { DecisionLogItem } from "../types/api";

export default function DecisionsTable({ items }: { items: DecisionLogItem[] }) {
  if (items.length === 0) {
    return <p className="text-sm text-zinc-500">暂无决策记录</p>;
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-zinc-800">
      <table className="w-full text-sm">
        <thead className="bg-zinc-900/80 text-left text-xs uppercase text-zinc-500">
          <tr>
            <th className="px-4 py-3">时间</th>
            <th className="px-4 py-3">模型</th>
            <th className="px-4 py-3">动作</th>
            <th className="px-4 py-3">Token</th>
            <th className="px-4 py-3">摘要</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800">
          {items.map((d) => (
            <tr key={d.id} className="hover:bg-zinc-900/40">
              <td className="px-4 py-3 mono text-xs text-zinc-400 whitespace-nowrap">
                {new Date(d.created_at).toLocaleString()}
              </td>
              <td className="px-4 py-3 text-zinc-400">{d.model_used}</td>
              <td className="px-4 py-3 font-medium text-amber-300">
                {(d.parsed_signal?.action as string) ?? "—"}
              </td>
              <td className="px-4 py-3 mono text-xs">{d.total_tokens ?? "—"}</td>
              <td className="px-4 py-3 max-w-md truncate text-zinc-500" title={d.prompt_summary ?? ""}>
                {d.prompt_summary ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
