import { useState } from "react";
import type { AgentStatus } from "../types/api";
import { api } from "../api/client";

interface AgentControlProps {
  status: AgentStatus | null;
  onChange: () => void;
}

export default function AgentControl({ status, onChange }: AgentControlProps) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const act = async (fn: () => Promise<{ message: string }>) => {
    setBusy(true);
    setMsg(null);
    try {
      const res = await fn();
      setMsg(res.message);
      onChange();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const running = status?.running ?? false;

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-wide text-zinc-500">Agent 引擎</p>
          <div className="mt-1 flex items-center gap-2">
            <span
              className={`h-2.5 w-2.5 rounded-full ${running ? "bg-emerald-400 animate-pulse" : "bg-zinc-600"}`}
            />
            <span className="font-medium">{running ? "24×7 运行中" : "已停止"}</span>
          </div>
          {status?.last_tick ? (
            <p className="mt-1 text-xs text-zinc-500">
              上次 tick · {new Date(status.last_tick).toLocaleString()}
            </p>
          ) : null}
          {status?.session_started_at ? (
            <p className="mt-1 text-xs text-zinc-500">
              会话开始 · {new Date(status.session_started_at).toLocaleString()}
            </p>
          ) : null}
          <p className="mt-1 text-xs text-zinc-500">
            模式 · {status?.execution_mode ?? "—"} · ticks {status?.tick_count ?? 0}
            {status?.tick_interval ? ` · 间隔 ${status.tick_interval}s` : ""}
          </p>
          {status?.last_error ? (
            <p className="mt-1 text-xs text-rose-400">{status.last_error}</p>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => void act(() => (running ? api.agentStop() : api.agentStart()))}
            className={`rounded-lg px-4 py-2 text-sm font-medium disabled:opacity-50 ${
              running
                ? "bg-rose-950 text-rose-300 ring-1 ring-rose-800"
                : "bg-emerald-950 text-emerald-300 ring-1 ring-emerald-800"
            }`}
          >
            {busy ? "处理中…" : running ? "停止" : "启动"}
          </button>
          {status?.degraded ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => void act(() => api.agentRecover())}
              className="rounded-lg border border-amber-700 px-4 py-2 text-sm text-amber-300"
            >
              恢复 auto
            </button>
          ) : null}
        </div>
      </div>
      {msg ? <p className="mt-3 text-xs text-zinc-400">{msg}</p> : null}
      {status?.session_id ? (
        <p className="mt-2 mono text-xs text-zinc-600 truncate">session · {status.session_id}</p>
      ) : null}
    </div>
  );
}
