import type { DecisionLogItem } from "../types/api";
import { fmtTime } from "../utils/format";

interface DecisionsTableProps {
  items: DecisionLogItem[];
  onSelect?: (id: string) => void;
  selectedId?: string | null;
}

export default function DecisionsTable({ items, onSelect, selectedId }: DecisionsTableProps) {
  if (items.length === 0) {
    return (
      <p className="rounded-xl border border-zinc-800 px-4 py-8 text-center text-sm text-zinc-500">
        没有符合筛选条件的决策记录
      </p>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-zinc-800">
      <table className="w-full text-sm">
        <thead className="bg-zinc-900/80 text-left text-xs uppercase text-zinc-500">
          <tr>
            <th className="px-4 py-3">时间</th>
            <th className="px-4 py-3">模型</th>
            <th className="px-4 py-3">动作</th>
            <th className="px-4 py-3">计划步骤</th>
            <th className="px-4 py-3">Token</th>
            <th className="px-4 py-3">摘要</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800">
          {items.map((d) => (
            <tr
              key={d.id}
              onClick={onSelect ? () => onSelect(d.id) : undefined}
              className={`${onSelect ? "cursor-pointer" : ""} ${
                selectedId === d.id ? "bg-zinc-800/60" : "hover:bg-zinc-900/40"
              }`}
            >
              <td className="px-4 py-3 mono text-xs text-zinc-400 whitespace-nowrap">
                {fmtTime(d.created_at)}
              </td>
              <td className="px-4 py-3 text-zinc-400">{d.model_used}</td>
              <td className="px-4 py-3 font-medium text-amber-300">
                {(d.parsed_signal?.action as string) ?? "—"}
              </td>
              <td className="px-4 py-3 mono text-xs text-zinc-500">
                {d.plan_step === null ? "—" : `第 ${d.plan_step} 步`}
              </td>
              <td className="px-4 py-3 mono text-xs">{d.total_tokens ?? "—"}</td>
              <td
                className="max-w-md truncate px-4 py-3 text-zinc-500"
                title={d.prompt_summary ?? ""}
              >
                {d.prompt_summary ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
