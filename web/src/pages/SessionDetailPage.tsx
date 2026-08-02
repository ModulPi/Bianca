import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import SessionSummaryPanel from "../components/SessionSummaryPanel";
import TradesTable from "../components/TradesTable";
import type { SessionSummary } from "../types/api";

export default function SessionDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [summary, setSummary] = useState<SessionSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    void api
      .summarySession(id)
      .then(setSummary)
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
          <section>
            <h2 className="mb-3 text-lg font-medium">同期成交（全库最近记录）</h2>
            <SessionTrades startedAt={summary.started_at} endedAt={summary.ended_at} />
          </section>
        </>
      ) : !error ? (
        <p className="text-sm text-zinc-500">加载中…</p>
      ) : null}
    </div>
  );
}

function SessionTrades({ startedAt, endedAt }: { startedAt: string; endedAt: string | null }) {
  const [items, setItems] = useState<Awaited<ReturnType<typeof api.trades>>["items"]>([]);

  useEffect(() => {
    void api.trades({ limit: 200 }).then((res) => {
      const end = endedAt ?? new Date().toISOString();
      const filtered = res.items.filter((t) => t.created_at >= startedAt && t.created_at <= end);
      setItems(filtered);
    });
  }, [startedAt, endedAt]);

  return <TradesTable items={items} />;
}
