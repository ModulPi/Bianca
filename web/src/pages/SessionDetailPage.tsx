import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import PnLChart from "../components/PnLChart";
import SessionSummaryPanel from "../components/SessionSummaryPanel";
import TradesTable from "../components/TradesTable";
import type { SessionSummary, TradeLogItem } from "../types/api";

export default function SessionDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [summary, setSummary] = useState<SessionSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [items, setItems] = useState<TradeLogItem[]>([]);

  useEffect(() => {
    if (!id) return;
    void api
      .summarySession(id)
      .then((s) => {
        setSummary(s);
        const end = s.ended_at ?? new Date().toISOString();
        return api.trades({ limit: 200 }).then((res) =>
          res.items.filter((t) => t.created_at >= s.started_at && t.created_at <= end),
        );
      })
      .then(setItems)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [id]);

  return (
    <div className="space-y-6">
      <Link to="/sessions" className="text-sm text-amber-400 hover:underline">
        ← 返回会话列表
      </Link>
      {error ? <p className="text-sm text-rose-400">{error}</p> : null}
      {summary ? (
        <>
          <SessionSummaryPanel summary={summary} title="会话详情" />
          <PnLChart trades={items} />
          <section>
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-lg font-medium">同期成交</h2>
              <Link
                to={`/checkpoints?thread=${summary.session_id}`}
                className="text-sm text-amber-400 hover:underline"
              >
                查看决策回放 →
              </Link>
            </div>
            <TradesTable items={items} />
          </section>
        </>
      ) : !error ? (
        <p className="text-sm text-zinc-500">加载中…</p>
      ) : null}
    </div>
  );
}
