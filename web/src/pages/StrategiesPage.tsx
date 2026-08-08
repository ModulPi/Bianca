import { useState } from "react";
import { api } from "../api/client";
import type { StrategyItem } from "../types/api";
import { usePolling } from "../hooks/usePolling";

export default function StrategiesPage() {
  const { data, error, loading, refresh } = usePolling(() => api.strategies(50), 10000);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const act = async (id: string, action: "start" | "stop" | "tick") => {
    setBusyId(id);
    setMsg(null);
    try {
      if (action === "start") await api.startStrategy(id);
      else if (action === "stop") await api.stopStrategy(id);
      else {
        const res = await api.tickStrategy(id);
        setMsg(`${id.slice(0, 8)}… tick → ${res.status}`);
      }
      refresh();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">策略模板</h1>
        <p className="text-sm text-zinc-500">网格 / DCA / 趋势 · 后端实验</p>
      </header>

      {msg ? <p className="text-xs text-zinc-400">{msg}</p> : null}
      {loading && !data ? <p className="text-sm text-zinc-500">加载中…</p> : null}
      {error ? <p className="text-sm text-rose-400">{error}</p> : null}

      <div className="space-y-3">
        {(data?.items ?? []).map((s: StrategyItem) => (
          <div
            key={s.id}
            className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-zinc-800 bg-zinc-900/50 p-4"
          >
            <div>
              <p className="font-medium">{s.name}</p>
              <p className="mt-1 text-xs text-zinc-500">
                {s.type} · {s.execution_mode} · {s.status}
              </p>
            </div>
            <div className="flex gap-2">
              {s.status !== "running" ? (
                <button
                  type="button"
                  disabled={busyId === s.id}
                  onClick={() => void act(s.id, "start")}
                  className="rounded-lg bg-emerald-950 px-3 py-1.5 text-sm text-emerald-300 ring-1 ring-emerald-800 disabled:opacity-50"
                >
                  启动
                </button>
              ) : (
                <button
                  type="button"
                  disabled={busyId === s.id}
                  onClick={() => void act(s.id, "stop")}
                  className="rounded-lg bg-rose-950 px-3 py-1.5 text-sm text-rose-300 ring-1 ring-rose-800 disabled:opacity-50"
                >
                  停止
                </button>
              )}
              <button
                type="button"
                disabled={busyId === s.id}
                onClick={() => void act(s.id, "tick")}
                className="rounded-lg border border-zinc-700 px-3 py-1.5 text-sm text-zinc-300 disabled:opacity-50"
              >
                Tick
              </button>
            </div>
          </div>
        ))}
        {data && data.items.length === 0 ? (
          <p className="text-sm text-zinc-500">暂无策略，可通过 API POST /strategies 创建</p>
        ) : null}
      </div>
    </div>
  );
}
