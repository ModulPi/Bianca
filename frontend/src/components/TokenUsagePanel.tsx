import type { UsageSummary } from "../types/api";
import StatCard from "./StatCard";

interface TokenUsagePanelProps {
  usage: UsageSummary | null;
  error?: string | null;
}

export default function TokenUsagePanel({ usage, error }: TokenUsagePanelProps) {
  if (error) {
    return (
      <div className="h-full rounded-xl border border-zinc-800 bg-zinc-900/60 p-4 text-sm text-rose-400">
        Token 用量加载失败：{error}
      </div>
    );
  }

  return (
    <div className="h-full rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
      <p className="text-xs uppercase tracking-wide text-zinc-500">Token 用量</p>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <StatCard
          label="今日 calls"
          value={String(usage?.today.calls ?? 0)}
          hint={`${(usage?.today.total_tokens ?? 0).toLocaleString()} tokens`}
        />
        <StatCard
          label="累计 calls"
          value={String(usage?.total.calls ?? 0)}
          hint={`${(usage?.total.total_tokens ?? 0).toLocaleString()} tokens`}
        />
        <StatCard
          label="今日 prompt"
          value={(usage?.today.prompt_tokens ?? 0).toLocaleString()}
          hint={`completion ${(usage?.today.completion_tokens ?? 0).toLocaleString()}`}
        />
        <StatCard
          label="累计 tokens"
          value={(usage?.total.total_tokens ?? 0).toLocaleString()}
          hint={`prompt ${(usage?.total.prompt_tokens ?? 0).toLocaleString()}`}
        />
      </div>
    </div>
  );
}
