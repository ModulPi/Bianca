import type { AgentStatus } from "../types/api";

export default function ExecutionModeBanner({ status }: { status: AgentStatus | null }) {
  const auto = status?.llm_auto_execute ?? true;

  return (
    <div
      className={`rounded-xl border px-4 py-3 text-sm ${
        auto
          ? "border-amber-900/60 bg-amber-950/30 text-amber-200"
          : "border-sky-900/60 bg-sky-950/30 text-sky-200"
      }`}
    >
      <p className="font-medium">{auto ? "全自动模式" : "信号记录模式"}</p>
      <p className="mt-1 text-xs opacity-80">
        {auto
          ? "LLM_AUTO_EXECUTE=true：BUY/SELL 信号经风控后直接下单。"
          : "LLM_AUTO_EXECUTE=false：仅记录信号，不执行下单。"}
        {" "}半自动确认（WebSocket + 手动 confirm）将在 M6 提供。
      </p>
    </div>
  );
}
