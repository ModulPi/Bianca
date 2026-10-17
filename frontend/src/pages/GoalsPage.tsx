import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { GoalItem } from "../types/api";
import { fmtPct, fmtTime, fmtUsdt, statusTone } from "../utils/format";

const PAGE = 50;

const STATUS_LABEL: Record<string, string> = {
  proposed: "待确认",
  active: "已生效",
  achieved: "已达成",
  aborted: "已中止",
  expired: "已到期",
};

export default function GoalsPage() {
  const [items, setItems] = useState<GoalItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await api.goals({
          limit: PAGE,
          offset,
          status: status || undefined,
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
    const id = window.setInterval(() => void load(), 15_000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [offset, status]);

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-semibold">目标历史</h1>
        <p className="mt-1 text-sm text-zinc-500">
          每个目标就是一段会话。原话、复述、解析结果、计划都在详情里。
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-2 text-xs">
        <label className="flex flex-col gap-1 text-zinc-500">
          状态
          <select
            value={status}
            onChange={(e) => {
              setOffset(0);
              setStatus(e.target.value);
            }}
            className="rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-200"
          >
            <option value="">全部</option>
            {Object.entries(STATUS_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label}（{value}）
              </option>
            ))}
          </select>
        </label>
        <span className="ml-auto text-zinc-500">
          共 {total} 个目标 · 第 {Math.floor(offset / PAGE) + 1} 页
        </span>
      </div>

      {error ? (
        <p className="rounded-lg border border-rose-900/50 bg-rose-950/20 px-3 py-2 text-sm text-rose-300">
          {error}
        </p>
      ) : items.length === 0 ? (
        <p className="rounded-xl border border-zinc-800 px-4 py-8 text-center text-sm text-zinc-500">
          还没有目标。去「当前目标」说一句你想做什么。
        </p>
      ) : (
        <div className="space-y-2">
          {items.map((g) => (
            <Link
              key={g.id}
              to={`/goals/${g.id}`}
              className="block rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 transition-colors hover:border-zinc-700 hover:bg-zinc-900/70"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-sm text-zinc-200">{g.objective ?? g.raw_text}</p>
                  <p className="mono mt-1 text-[10px] text-zinc-600">
                    {g.id} · {fmtTime(g.created_at)}
                  </p>
                </div>
                <span className={`mono text-xs ${statusTone(g.status)}`}>
                  {STATUS_LABEL[g.status] ?? g.status}
                </span>
              </div>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-500">
                {g.target_return_pct !== null ? <span>目标 {fmtPct(g.target_return_pct)}</span> : null}
                {g.target_equity !== null ? <span>目标权益 {fmtUsdt(g.target_equity)}</span> : null}
                {g.max_drawdown_pct !== null ? <span>回撤上限 {fmtPct(g.max_drawdown_pct)}</span> : null}
                {g.horizon_hours !== null ? <span>期限 {g.horizon_hours}h</span> : null}
                {g.baseline_equity !== null ? <span>基准 {fmtUsdt(g.baseline_equity)}</span> : null}
                <span>{g.plan_id ? "有计划" : "无计划"}</span>
              </div>
              {g.end_reason ? (
                <p className="mt-1 text-xs text-zinc-500">结束原因：{g.end_reason}</p>
              ) : null}
            </Link>
          ))}
        </div>
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
    </div>
  );
}
