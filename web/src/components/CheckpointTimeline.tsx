import type { CheckpointStateItem } from "../types/api";

interface AgentSignalEntry {
  agent?: string;
  signal?: { action?: string; reason?: string; confidence?: number };
  raw_reason?: string;
  strategy_name?: string;
}

interface MergeMeta {
  mode?: string;
  conflict?: boolean;
  winner?: string;
  reason?: string;
  analysis_action?: string;
  strategy_action?: string;
}

interface CheckpointTimelineProps {
  items: CheckpointStateItem[];
}

const nodeLabels: Record<string, string> = {
  fetch_market: "拉取行情",
  orchestrator: "调度",
  analysis: "AI 分析",
  strategy: "趋势策略",
  merge: "合并意见",
  risk: "风控",
  execute: "执行下单",
  log_only: "记录信号",
};

function pickLatestWithSignals(items: CheckpointStateItem[]): CheckpointStateItem | null {
  for (const item of items) {
    const signals = (item.state?.agent_signals as AgentSignalEntry[] | undefined) ?? [];
    if (signals.length > 0) return item;
  }
  return items[0] ?? null;
}

function CollaborationPanel({ state }: { state: Record<string, unknown> }) {
  const signals = (state.agent_signals as AgentSignalEntry[] | undefined) ?? [];
  const merge = (state.merge_meta as MergeMeta | undefined) ?? {};
  const analysis = signals.find((s) => s.agent === "analysis");
  const strategy = signals.find((s) => s.agent === "strategy");
  const finalSig = state.llm_signal as { action?: string; reason?: string } | undefined;

  if (!signals.length && !finalSig) return null;

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/30 p-4 space-y-3">
      <h3 className="text-sm font-medium text-zinc-300">多 Agent 协作详情</h3>
      <div className="grid gap-3 md:grid-cols-3 text-sm">
        <div className="rounded-lg border border-zinc-800 p-3">
          <p className="text-xs text-zinc-500 mb-1">AI 说</p>
          <p className="text-amber-300">{analysis?.signal?.action ?? "—"}</p>
          <p className="text-xs text-zinc-400 mt-1 line-clamp-4">
            {analysis?.signal?.reason ?? analysis?.raw_reason ?? "无"}
          </p>
        </div>
        <div className="rounded-lg border border-zinc-800 p-3">
          <p className="text-xs text-zinc-500 mb-1">策略说</p>
          <p className="text-amber-300">{strategy?.signal?.action ?? "—"}</p>
          <p className="text-xs text-zinc-400 mt-1 line-clamp-4">
            {strategy?.signal?.reason ?? "无"}
            {strategy?.strategy_name ? (
              <span className="block text-zinc-600 mt-1">{strategy.strategy_name}</span>
            ) : null}
          </p>
        </div>
        <div className="rounded-lg border border-zinc-800 p-3">
          <p className="text-xs text-zinc-500 mb-1">最后怎么定</p>
          <p className="text-amber-300">{finalSig?.action ?? "—"}</p>
          <p className="text-xs text-zinc-400 mt-1 line-clamp-4">
            {merge.reason ?? finalSig?.reason ?? "无"}
          </p>
          {merge.conflict ? (
            <span className="inline-block mt-2 text-xs text-amber-500">意见曾冲突</span>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default function CheckpointTimeline({ items }: CheckpointTimelineProps) {
  if (items.length === 0) {
    return <p className="text-sm text-zinc-500">该线程暂无 checkpoint</p>;
  }

  const latest = pickLatestWithSignals(items);

  return (
    <div className="space-y-4">
      {latest?.state ? (
        <CollaborationPanel state={latest.state as Record<string, unknown>} />
      ) : null}
      <ol className="relative border-l border-zinc-700 ml-3 space-y-6">
        {items.map((item, idx) => {
          const state = item.state ?? {};
          const signal = state.llm_signal as { action?: string; reason?: string } | undefined;
          const status = state.status as string | undefined;
          const next = item.next_nodes?.map((n) => nodeLabels[n] ?? n).join(", ");
          const plan = state.orchestrator_plan as { use_analysis?: boolean; use_strategy?: boolean } | undefined;

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
                {plan ? (
                  <p className="mt-1 text-xs text-zinc-500">
                    参与：{plan.use_analysis ? "AI" : ""}
                    {plan.use_analysis && plan.use_strategy ? " + " : ""}
                    {plan.use_strategy ? "趋势" : ""}
                  </p>
                ) : null}
                {signal?.action ? (
                  <p className="mt-2 text-sm">
                    最终 <span className="font-medium text-amber-300">{signal.action}</span>
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
    </div>
  );
}
