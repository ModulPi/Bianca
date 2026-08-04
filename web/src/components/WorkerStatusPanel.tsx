import type { WorkerStatus } from "../types/api";

interface WorkerStatusPanelProps {
  workers: WorkerStatus[];
  tradeMarket?: string;
  symbols?: string[];
}

export default function WorkerStatusPanel({ workers, tradeMarket, symbols }: WorkerStatusPanelProps) {
  const list: WorkerStatus[] =
    workers.length > 0
      ? workers
      : (symbols ?? []).map((symbol) => ({
          symbol,
          tick_count: 0,
          last_status: null,
          last_error: null,
        }));

  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium text-zinc-300">并行 Worker</h2>
        {tradeMarket ? <span className="text-xs text-zinc-500">市场 · {tradeMarket}</span> : null}
      </div>
      {list.length === 0 ? (
        <p className="text-sm text-zinc-500">未启动（配置 AGENT_SYMBOLS）</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-zinc-800 text-zinc-500">
                <th className="py-2 pr-3">Symbol</th>
                <th className="py-2 pr-3">Ticks</th>
                <th className="py-2 pr-3">状态</th>
                <th className="py-2">错误</th>
              </tr>
            </thead>
            <tbody>
              {list.map((w) => (
                <tr key={w.symbol} className="border-b border-zinc-800/50">
                  <td className="py-2 pr-3 mono text-amber-300">{w.symbol}</td>
                  <td className="py-2 pr-3">{w.tick_count ?? 0}</td>
                  <td className="py-2 pr-3 text-zinc-300">{w.last_status ?? "—"}</td>
                  <td className="py-2 text-rose-400">{w.last_error ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
