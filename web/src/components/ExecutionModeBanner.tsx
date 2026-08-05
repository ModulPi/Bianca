import type { AgentStatus } from "../types/api";

const modeLabel: Record<string, string> = {
  auto: "全自动",
  semi_auto: "半自动（降级/人工）",
  signal_only: "仅记录信号",
};

export default function ExecutionModeBanner({ status }: { status: AgentStatus | null }) {
  const mode = status?.execution_mode ?? (status?.llm_auto_execute ? "auto" : "signal_only");
  const label = modeLabel[mode] ?? mode;
  const degraded = status?.degraded ?? false;

  const styles = degraded
    ? "border-rose-900/60 bg-rose-950/30 text-rose-200"
    : mode === "semi_auto"
      ? "border-sky-900/60 bg-sky-950/30 text-sky-200"
      : mode === "signal_only"
        ? "border-zinc-700 bg-zinc-900/50 text-zinc-300"
        : "border-amber-900/60 bg-amber-950/30 text-amber-200";

  return (
    <div className={`rounded-xl border px-4 py-3 text-sm ${styles}`}>
      <p className="font-medium">
        执行模式：{label}
        {degraded ? " · 已自动降级" : ""}
      </p>
      <p className="mt-1 text-xs opacity-80">
        {degraded &&
          "连续失败触发 semi_auto，请在下方确认队列人工介入，或点击「恢复 auto」。"}
        {!degraded && mode === "auto" && "Agent 自主决策，经风控后直接执行（24×7）。"}
        {!degraded && mode === "semi_auto" && "BUY/SELL 需人工确认后执行。"}
        {!degraded && mode === "signal_only" && "仅记录信号，不下单。"}
      </p>
    </div>
  );
}
