import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import DecisionsTable from "../components/DecisionsTable";
import type { DecisionDetail, DecisionLogItem } from "../types/api";
import { fmtTime } from "../utils/format";

const PAGE = 50;

export default function DecisionsPage() {
  const [searchParams] = useSearchParams();
  const [items, setItems] = useState<DecisionLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [goalId, setGoalId] = useState(searchParams.get("goal_id") ?? "");
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<DecisionDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await api.decisions({
          limit: PAGE,
          offset,
          goal_id: goalId.trim() || undefined,
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
  }, [offset, goalId]);

  const open = async (id: string) => {
    setDetailError(null);
    try {
      setDetail(await api.decision(id));
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-semibold">决策回放</h1>
        <p className="mt-1 text-sm text-zinc-500">
          每一次 LLM 决策的输入与输出。点一行看完整 prompt 与原始回答。
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-2 text-xs">
        <label className="flex flex-col gap-1 text-zinc-500">
          目标 id
          <input
            value={goalId}
            onChange={(e) => {
              setOffset(0);
              setGoalId(e.target.value.trim());
            }}
            placeholder="按目标过滤"
            className="w-52 rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-200"
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
        <DecisionsTable items={items} onSelect={open} selectedId={detail?.id} />
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

      {detailError ? <p className="text-sm text-rose-400">{detailError}</p> : null}
      {detail ? (
        <section className="space-y-3 rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-medium text-amber-300">
                {String(detail.parsed_signal?.action ?? "—")} · {detail.model_used}
              </h2>
              <p className="mono mt-1 text-[10px] text-zinc-600">{detail.id}</p>
            </div>
            <button
              type="button"
              onClick={() => setDetail(null)}
              className="text-xs text-zinc-500 hover:text-zinc-200"
            >
              关闭
            </button>
          </div>
          <p className="text-xs text-zinc-500">
            {fmtTime(detail.created_at)} · 计划 {detail.plan_id ?? "—"} · 第{" "}
            {detail.plan_step ?? "—"} 步 · prompt {detail.prompt_tokens ?? "—"} / completion{" "}
            {detail.completion_tokens ?? "—"}
          </p>
          <Block title="解析出的信号" value={detail.parsed_signal} />
          <Block title="原始输出" value={detail.raw_output} mono />
          {detail.market_data ? <Block title="行情输入" value={detail.market_data} /> : null}
          {detail.prompt ? <Block title="完整 prompt" value={detail.prompt} /> : null}
        </section>
      ) : null}
    </div>
  );
}

function Block({
  title,
  value,
  mono,
}: {
  title: string;
  value: unknown;
  mono?: boolean;
}) {
  return (
    <div>
      <p className="text-xs text-zinc-500">{title}</p>
      <pre
        className={`mt-1 max-h-80 overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-[11px] text-zinc-400 ${
          mono ? "mono" : ""
        }`}
      >
        {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}
