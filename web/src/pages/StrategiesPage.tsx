import { useState } from "react";
import { api } from "../api/client";
import type { StrategyItem } from "../types/api";
import { usePolling } from "../hooks/usePolling";

const templates = [
  { type: "grid", label: "网格", desc: "价格区间分层买卖" },
  { type: "dca", label: "DCA", desc: "定时定额买入" },
  { type: "trend", label: "趋势", desc: "快慢均线交叉" },
];

export default function StrategiesPage() {
  const { data, refresh } = usePolling(() => api.strategies(), 10000);
  const [name, setName] = useState("我的策略");
  const [type, setType] = useState("grid");
  const [mode, setMode] = useState("auto");
  const [msg, setMsg] = useState<string | null>(null);

  const create = async () => {
    setMsg(null);
    try {
      await api.createStrategy({ name, type, execution_mode: mode });
      setMsg("已创建");
      void refresh();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    }
  };

  const act = async (s: StrategyItem, action: "start" | "stop" | "tick") => {
    setMsg(null);
    try {
      if (action === "start") await api.startStrategy(s.id);
      else if (action === "stop") await api.stopStrategy(s.id);
      else {
        const r = await api.tickStrategy(s.id);
        setMsg(`Tick: ${r.status}`);
      }
      void refresh();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">策略模板</h1>
        <p className="text-sm text-zinc-500">网格 / DCA / 趋势 · M5</p>
      </header>

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4 space-y-3">
        <h2 className="text-sm font-medium text-zinc-300">新建策略</h2>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
          placeholder="策略名称"
        />
        <div className="flex flex-wrap gap-2">
          {templates.map((t) => (
            <button
              key={t.type}
              type="button"
              onClick={() => setType(t.type)}
              className={`rounded-lg px-3 py-2 text-sm ${
                type === t.type
                  ? "bg-amber-950 text-amber-300 ring-1 ring-amber-800"
                  : "bg-zinc-800 text-zinc-400"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value)}
          className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
        >
          <option value="auto">全自动</option>
          <option value="semi_auto">半自动</option>
        </select>
        <button
          type="button"
          onClick={() => void create()}
          className="rounded-lg bg-emerald-950 px-4 py-2 text-sm text-emerald-300 ring-1 ring-emerald-800"
        >
          创建
        </button>
        {msg ? <p className="text-xs text-zinc-400">{msg}</p> : null}
      </section>

      <div className="space-y-3">
        {(data?.items ?? []).map((s) => (
          <div
            key={s.id}
            className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="font-medium">{s.name}</p>
                <p className="text-xs text-zinc-500">
                  {s.type} · {s.execution_mode} · {s.status}
                </p>
              </div>
              <div className="flex gap-2">
                {s.status !== "running" ? (
                  <button
                    type="button"
                    onClick={() => void act(s, "start")}
                    className="text-xs text-emerald-400 hover:underline"
                  >
                    启动
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => void act(s, "stop")}
                    className="text-xs text-rose-400 hover:underline"
                  >
                    停止
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => void act(s, "tick")}
                  className="text-xs text-amber-400 hover:underline"
                >
                  手动 Tick
                </button>
              </div>
            </div>
            <pre className="mt-2 overflow-x-auto text-[10px] text-zinc-600">
              {JSON.stringify(s.params, null, 0)}
            </pre>
          </div>
        ))}
      </div>
    </div>
  );
}
