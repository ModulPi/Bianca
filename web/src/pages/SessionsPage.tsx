import { Link } from "react-router-dom";
import { api } from "../api/client";
import LoopClosedBadge from "../components/LoopClosedBadge";
import { usePolling } from "../hooks/usePolling";

export default function SessionsPage() {
  const { data, error, loading } = usePolling(() => api.summarySessions(50), 15000);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">会话历史</h1>
        <p className="text-sm text-zinc-500">Agent 启停周期快照</p>
      </header>

      {loading && !data ? <p className="text-sm text-zinc-500">加载中…</p> : null}
      {error ? <p className="text-sm text-rose-400">{error}</p> : null}

      <div className="space-y-3">
        {(data?.items ?? []).map((s) => (
          <Link
            key={s.session_id}
            to={`/sessions/${s.session_id}`}
            className="block rounded-xl border border-zinc-800 bg-zinc-900/50 p-4 hover:border-zinc-700 transition-colors"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="mono text-xs text-zinc-500">{s.session_id}</p>
                <p className="mt-1 text-sm">
                  {new Date(s.started_at).toLocaleString()}
                  {s.ended_at ? ` → ${new Date(s.ended_at).toLocaleString()}` : " · 进行中"}
                </p>
              </div>
              <LoopClosedBadge closed={s.trades.loop_closed} />
            </div>
            <div className="mt-3 flex flex-wrap gap-4 text-sm text-zinc-400">
              <span>Tick {s.agent.tick_count}</span>
              <span>Token {s.usage.total_tokens}</span>
              <span>PnL {s.pnl.realized_usdt.toFixed(4)} USDT</span>
            </div>
          </Link>
        ))}
        {data && data.items.length === 0 ? (
          <p className="text-sm text-zinc-500">暂无历史会话</p>
        ) : null}
      </div>
    </div>
  );
}
