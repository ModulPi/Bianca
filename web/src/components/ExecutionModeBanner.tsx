import type { AgentStatus } from "../types/api";

const modeLabel: Record<string, string> = {
  auto: "全自动",
  semi_auto: "半自动",
  signal_only: "仅记录信号",
};

export default function ExecutionModeBanner({ status }: { status: AgentStatus | null }) {
  const mode = status?.execution_mode ?? (status?.llm_auto_execute ? "auto" : "signal_only");
  const label = modeLabel[mode] ?? mode;

  const styles =
    mode === "semi_auto"
      ? "border-sky-900/60 bg-sky-950/30 text-sky-200"
      : mode === "signal_only"
        ? "border-zinc-700 bg-zinc-900/50 text-zinc-300"
        : "border-amber-900/60 bg-amber-950/30 text-amber-200";

  return (
    <div className={`rounded-xl border px-4 py-3 text-sm ${styles}`}>
      <p className="font-medium">执行模式：{label}</p>
      <p className="mt-1 text-xs opacity-80">
        {mode === "auto" && "BUY/SELL 经风控后直接下单。"}
        {mode === "semi_auto" && "BUY/SELL 推送至下方确认队列，确认后走风控与执行。"}
        {mode === "signal_only" && "仅写入 decision/trade 信号，不执行下单。"}
      </p>
    </div>
  );
}
