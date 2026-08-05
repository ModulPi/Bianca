import { useCallback, useState } from "react";
import { api } from "../api/client";
import type { PendingSignalItem } from "../types/api";

interface ConfirmQueueProps {
  items: PendingSignalItem[];
  onActionComplete: () => void;
}

export default function ConfirmQueue({ items, onActionComplete }: ConfirmQueueProps) {
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const act = useCallback(
    async (item: PendingSignalItem, action: "confirm" | "reject") => {
      setBusyId(item.id);
      setActionMsg(null);
      try {
        if (action === "confirm") {
          const res = await api.confirmPending(item.id);
          setActionMsg(`已确认 · ${res.status}`);
        } else {
          await api.rejectPending(item.id);
          setActionMsg("已拒绝");
        }
        onActionComplete();
      } catch (err) {
        setActionMsg(err instanceof Error ? err.message : String(err));
      } finally {
        setBusyId(null);
      }
    },
    [onActionComplete],
  );

  if (items.length === 0) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 px-4 py-3 text-sm text-zinc-500">
        无待确认信号（半自动 / 降级模式下 BUY/SELL 会出现在此）
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {actionMsg ? <p className="text-xs text-zinc-400">{actionMsg}</p> : null}
      {items.map((item) => {
        const symbol = String(item.signal.symbol ?? "—");
        return (
          <div
            key={item.id}
            className="rounded-xl border border-amber-900/50 bg-amber-950/20 p-4"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-amber-200">
                  {symbol} · {(item.signal.action as string) ?? "?"} · 置信度{" "}
                  {String(item.signal.confidence ?? "—")}
                </p>
                <p className="mt-1 text-xs text-zinc-400">{String(item.signal.reason ?? "")}</p>
                <p className="mt-1 mono text-[10px] text-zinc-600">
                  过期 {new Date(item.expires_at).toLocaleString()}
                </p>
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={busyId === item.id}
                  onClick={() => void act(item, "confirm")}
                  className="rounded-lg bg-emerald-950 px-3 py-1.5 text-sm text-emerald-300 ring-1 ring-emerald-800 hover:bg-emerald-900 disabled:opacity-50"
                >
                  确认执行
                </button>
                <button
                  type="button"
                  disabled={busyId === item.id}
                  onClick={() => void act(item, "reject")}
                  className="rounded-lg bg-zinc-800 px-3 py-1.5 text-sm text-zinc-300 hover:bg-zinc-700 disabled:opacity-50"
                >
                  拒绝
                </button>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
