import { Link } from "react-router-dom";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import CheckpointTimeline from "../components/CheckpointTimeline";
import { usePolling } from "../hooks/usePolling";
import type { CheckpointHistoryResponse } from "../types/api";

export default function CheckpointsPage({ initialThread }: { initialThread?: string | null }) {
  const { data, error, loading } = usePolling(() => api.checkpointThreads(50), 20000);
  const [selected, setSelected] = useState<string | null>(initialThread ?? null);
  const [history, setHistory] = useState<CheckpointHistoryResponse | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const loadHistory = useCallback(async (threadId: string) => {
    setSelected(threadId);
    setHistoryError(null);
    try {
      const res = await api.checkpointHistory(threadId, 50);
      setHistory(res);
    } catch (err) {
      setHistory(null);
      setHistoryError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    const preset = initialThread ?? sessionStorage.getItem("bianca.checkpoint.thread");
    if (preset) {
      void loadHistory(preset);
      return;
    }
    if (data?.items.length && !selected) {
      void loadHistory(data.items[0].thread_id);
    }
  }, [data, selected, loadHistory, initialThread]);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">决策回放</h1>
        <p className="text-sm text-zinc-500">LangGraph Checkpointer · 按 thread 查看 tick 链路</p>
      </header>

      {loading && !data ? <p className="text-sm text-zinc-500">加载中…</p> : null}
      {error ? <p className="text-sm text-rose-400">{error}</p> : null}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-2">
          <h2 className="text-sm uppercase tracking-wide text-zinc-500">线程</h2>
          {(data?.items ?? []).map((t) => (
            <button
              key={t.thread_id}
              type="button"
              onClick={() => void loadHistory(t.thread_id)}
              className={`w-full rounded-lg border px-3 py-2 text-left text-sm transition-colors ${
                selected === t.thread_id
                  ? "border-amber-700 bg-amber-950/40"
                  : "border-zinc-800 bg-zinc-900/40 hover:border-zinc-700"
              }`}
            >
              <p className="mono text-xs truncate">{t.thread_id}</p>
              <p className="mt-1 text-xs text-zinc-500">{t.checkpoint_count} checkpoints</p>
            </button>
          ))}
          {data && data.items.length === 0 ? (
            <p className="text-sm text-zinc-500">暂无 checkpoint，先运行 Agent tick</p>
          ) : null}
        </div>

        <div className="lg:col-span-2 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm uppercase tracking-wide text-zinc-500">时间线</h2>
            {selected ? (
              <Link
                to={`/sessions/${selected}`}
                className="text-xs text-amber-400 hover:underline"
              >
                若 thread=session_id，查看会话汇总 →
              </Link>
            ) : null}
          </div>
          {historyError ? <p className="text-sm text-rose-400">{historyError}</p> : null}
          <CheckpointTimeline items={history?.items ?? []} />
        </div>
      </div>
    </div>
  );
}
