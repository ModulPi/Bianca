import { api } from "../api/client";
import PositionsTable from "../components/PositionsTable";
import SystemHealthPanel from "../components/SystemHealthPanel";
import { usePolling } from "../hooks/usePolling";

export default function PositionsPage() {
  const { data, error, loading, refresh } = usePolling(() => api.positions(), 15000);
  const healthPoll = usePolling(() => api.health(), 30000);

  const isMvp = data?.schema_mode === "mvp";

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">持仓快照</h1>
          <p className="text-sm text-zinc-500">
            positions 表 · schema_mode={data?.schema_mode ?? "—"}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-400 hover:text-zinc-200"
        >
          刷新
        </button>
      </header>

      <SystemHealthPanel health={healthPoll.data} />

      {loading && !data ? <p className="text-sm text-zinc-500">加载中…</p> : null}
      {error ? <p className="text-sm text-rose-400">{error}</p> : null}

      {!isMvp && !loading ? (
        <p className="text-sm text-amber-400/90">
          当前为 PoC/SQLite 栈，positions 表无数据。请使用 M4 Docker 栈（PostgreSQL）查看持仓快照。
        </p>
      ) : null}

      <PositionsTable items={data?.items ?? []} />

      {(data?.items.length ?? 0) > 0 ? (
        <p className="text-xs text-zinc-600">共 {data!.total} 条 · Agent tick 后自动同步</p>
      ) : null}
    </div>
  );
}
