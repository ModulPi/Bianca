import { useEffect, useState } from "react";
import { api } from "../api/client";
import RiskEventsTable from "../components/RiskEventsTable";
import type { RiskEventItem } from "../types/api";

const PAGE = 50;

export default function RiskPage() {
  const [items, setItems] = useState<RiskEventItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [eventType, setEventType] = useState("");
  const [error, setError] = useState<string | null>(null);

  const [reason, setReason] = useState("");
  const [resetMsg, setResetMsg] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await api.riskEvents({
          limit: PAGE,
          offset,
          event_type: eventType.trim() || undefined,
        });
        if (!alive) return;
        setItems(res.items);
        setTotal(res.total);
        setError(null);
      } catch (err) {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      }
    };
    void load();
    const id = window.setInterval(() => void load(), 10_000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [offset, eventType]);

  const reset = async () => {
    if (!reason.trim()) {
      setResetMsg("理由不能为空 —— 这是唯一能松开回撤线的操作。");
      return;
    }
    setResetting(true);
    setResetMsg(null);
    try {
      const res = await api.resetDrawdown(reason.trim());
      setResetMsg(
        `已重置 · 回撤 ${res.drawdown_before?.toFixed(2) ?? "—"}% → 0% · ` +
          `峰值前 ${res.peak_before?.toFixed(2) ?? "—"} · 当前 ${res.current_equity?.toFixed(2) ?? "—"}`,
      );
      setReason("");
      setOffset(0);
    } catch (err) {
      setResetMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setResetting(false);
    }
  };

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-semibold">风控事件</h1>
        <p className="mt-1 text-sm text-zinc-500">
          破线、强平、达成清仓、人工全平、回撤重置 —— 都在这里留痕。
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-2 text-xs">
        <label className="flex flex-col gap-1 text-zinc-500">
          类型
          <input
            value={eventType}
            onChange={(e) => {
              setOffset(0);
              setEventType(e.target.value.trim());
            }}
            placeholder="stop_loss / drawdown / manual_close…"
            className="w-64 rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-200"
          />
        </label>
        <span className="ml-auto text-zinc-500">
          共 {total} 条 · 第 {Math.floor(offset / PAGE) + 1} 页
        </span>
      </div>

      {error ? (
        <p className="rounded-lg border border-rose-900/50 bg-rose-950/20 px-3 py-2 text-sm text-rose-300">
          {error}
        </p>
      ) : (
        <RiskEventsTable items={items} />
      )}

      <div className="flex gap-2">
        <button
          type="button"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - PAGE))}
          className="rounded-lg border border-zinc-800 px-3 py-1.5 text-sm text-zinc-300 disabled:opacity-40"
        >
          上一页
        </button>
        <button
          type="button"
          disabled={offset + PAGE >= total}
          onClick={() => setOffset(offset + PAGE)}
          className="rounded-lg border border-zinc-800 px-3 py-1.5 text-sm text-zinc-300 disabled:opacity-40"
        >
          下一页
        </button>
      </div>

      <section className="space-y-3 rounded-xl border border-rose-900/40 bg-zinc-900/40 p-4">
        <div>
          <h2 className="text-sm font-medium text-rose-300">重置回撤基准</h2>
          <p className="mt-1 text-xs text-zinc-500">
            这是唯一能让回撤线松开的操作。理由必填，会写进 `risk_events`。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="为什么重置？"
            className="min-w-64 flex-1 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-200"
          />
          <button
            type="button"
            disabled={resetting}
            onClick={() => void reset()}
            className="rounded-lg bg-rose-900 px-4 py-2 text-sm font-medium text-rose-100 disabled:opacity-50"
          >
            {resetting ? "重置中…" : "重置"}
          </button>
        </div>
        {resetMsg ? <p className="text-xs text-zinc-400">{resetMsg}</p> : null}
      </section>
    </div>
  );
}
