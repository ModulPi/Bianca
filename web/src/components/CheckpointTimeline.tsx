import type { CheckpointStateItem } from "../types/api";

interface CheckpointTimelineProps {
  items: CheckpointStateItem[];
}

const nodeLabels: Record<string, string> = {
  fetch_market: "拉取行情",
  analysis: "LLM 分析",
  risk: "风控",
  execute: "执行下单",
  log_only: "记录信号",
};

export default function CheckpointTimeline({ items }: CheckpointTimelineProps) {
  if (items.length === 0) {
    return <p className="text-sm text-zinc-500">该线程暂无 checkpoint</p>;
  }

  return (
    <ol className="relative border-l border-zinc-700 ml-3 space-y-6">
      {items.map((item, idx) => {
        const state = item.state ?? {};
        const signal = state.llm_signal as { action?: string; reason?: string } | undefined;
        const status = state.status as string | undefined;
        const next = item.next_nodes?.map((n) => nodeLabels[n] ?? n).join(", ");

        return (
          <li key={item.checkpoint_id ?? idx} className="ml-6">
            <span className="absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full bg-amber-500 ring-4 ring-zinc-950" />
            <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-3">
              <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
                <span className="mono">#{items.length - idx}</span>
                {item.created_at ? (
                  <span>{new Date(item.created_at).toLocaleString()}</span>
                ) : null}
                {status ? (
                  <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-zinc-300">{status}</span>
                ) : null}
              </div>
              {signal?.action ? (
                <p className="mt-2 text-sm">
                  信号 <span className="font-medium text-amber-300">{signal.action}</span>
                  {signal.reason ? (
                    <span className="text-zinc-400"> — {signal.reason.slice(0, 120)}</span>
                  ) : null}
                </p>
              ) : null}
              {state.risk_decision ? (
                <p className="mt-1 text-xs text-zinc-500">
                  风控 {(state.risk_decision as { approved?: boolean }).approved ? "通过" : "拒绝"}
                </p>
              ) : null}
              {next ? <p className="mt-1 text-xs text-zinc-600">下一步：{next}</p> : null}
              <p className="mt-1 mono text-[10px] text-zinc-700 truncate">
                {item.checkpoint_id}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
