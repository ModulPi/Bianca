import { api } from "../api/client";
import TokenUsagePanel from "../components/TokenUsagePanel";
import { usePolling } from "../hooks/usePolling";

export default function UsagePage() {
  const usage = usePolling(api.usage, 30_000);

  return (
    <div className="max-w-3xl space-y-5">
      <header>
        <h1 className="text-2xl font-semibold">Token 用量</h1>
        <p className="mt-1 text-sm text-zinc-500">
          按 decision_logs 汇总：today 为 UTC 当日，total 为全部历史。
        </p>
      </header>
      <TokenUsagePanel usage={usage.data} error={usage.error} />
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 text-xs leading-relaxed text-zinc-500">
        <p>口径说明：</p>
        <ul className="mt-1 list-inside list-disc space-y-1">
          <li>只统计落库的决策调用；规划（plans）的 usage 单独记录在 plans 表。</li>
          <li>calls 是决策次数，不等于下单次数 —— HOLD 也算一次调用。</li>
          <li>这里没有成本估算：模型单价会变，界面不编一个写死的汇率。</li>
        </ul>
      </div>
    </div>
  );
}
