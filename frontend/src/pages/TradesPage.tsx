import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import TradesTable from "../components/TradesTable";
import type { OrderItem, TradeDetail, TradeLogItem } from "../types/api";
import { fmtPct, fmtQty, fmtTime, fmtUsdt, statusTone } from "../utils/format";

const PAGE = 50;

type Tab = "trades" | "orders";

export default function TradesPage() {
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState<Tab>("trades");
  const [items, setItems] = useState<TradeLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [symbol, setSymbol] = useState("");
  const [side, setSide] = useState("");
  const [status, setStatus] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [goalId, setGoalId] = useState(searchParams.get("goal_id") ?? "");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<TradeDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await api.trades({
          limit: PAGE,
          offset,
          symbol: symbol.trim() || undefined,
          side: side || undefined,
          status: status || undefined,
          goal_id: goalId.trim() || undefined,
          since: since ? new Date(since).toISOString() : undefined,
          until: until ? new Date(until).toISOString() : undefined,
        });
        if (!alive) return;
        setItems(res.items);
        setTotal(res.total);
        setError(null);
      } catch (err) {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (alive) setLoading(false);
      }
    };
    void load();
    const id = window.setInterval(() => void load(), 10_000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [offset, symbol, side, status, since, until, goalId]);

  const resetPage = <T,>(setter: (v: T) => void) => (v: T) => {
    setOffset(0);
    setter(v);
  };

  const openDetail = async (id: string) => {
    setDetailError(null);
    try {
      setSelected(await api.trade(id));
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">交易明细</h1>
          <p className="mt-1 text-sm text-zinc-500">
            决策流水（含被风控拦下的）与真实订单回执分开看。点一行看当时的依据。
          </p>
        </div>
        <div className="flex rounded-lg ring-1 ring-zinc-800">
          {(["trades", "orders"] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              className={`px-3 py-1.5 text-sm first:rounded-l-lg last:rounded-r-lg ${
                tab === t ? "bg-zinc-800 text-amber-300" : "bg-zinc-950 text-zinc-400"
              }`}
            >
              {t === "trades" ? "交易流水" : "订单回执"}
            </button>
          ))}
        </div>
      </header>

      {tab === "trades" ? (
        <>
          <div className="flex flex-wrap items-end gap-2 text-xs">
            <label className="flex flex-col gap-1 text-zinc-500">
              Symbol
              <input
                value={symbol}
                onChange={(e) => resetPage(setSymbol)(e.target.value.toUpperCase())}
                placeholder="BTCUSDT"
                className="w-28 rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-200"
              />
            </label>
            <label className="flex flex-col gap-1 text-zinc-500">
              方向
              <select
                value={side}
                onChange={(e) => resetPage(setSide)(e.target.value)}
                className="rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-200"
              >
                <option value="">全部</option>
                <option value="BUY">BUY</option>
                <option value="SELL">SELL</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-zinc-500">
              状态
              <select
                value={status}
                onChange={(e) => resetPage(setStatus)(e.target.value)}
                className="rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-200"
              >
                <option value="">全部</option>
                <option value="filled">filled</option>
                <option value="failed">failed</option>
                <option value="rejected">rejected</option>
                <option value="signal_only">signal_only</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-zinc-500">
              目标 id
              <input
                value={goalId}
                onChange={(e) => resetPage(setGoalId)(e.target.value.trim())}
                placeholder="按目标过滤"
                className="w-40 rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-200"
              />
            </label>
            <label className="flex flex-col gap-1 text-zinc-500">
              起
              <input
                type="datetime-local"
                value={since}
                onChange={(e) => resetPage(setSince)(e.target.value)}
                className="rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-200"
              />
            </label>
            <label className="flex flex-col gap-1 text-zinc-500">
              止
              <input
                type="datetime-local"
                value={until}
                onChange={(e) => resetPage(setUntil)(e.target.value)}
                className="rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-200"
              />
            </label>
            <button
              type="button"
              onClick={() => {
                setOffset(0);
                setSymbol("");
                setSide("");
                setStatus("");
                setSince("");
                setUntil("");
                setGoalId("");
              }}
              className="rounded-lg border border-zinc-800 px-2 py-1.5 text-zinc-400 hover:text-zinc-200"
            >
              清空筛选
            </button>
            <span className="ml-auto text-zinc-500">
              共 {total} 条 · 第 {Math.floor(offset / PAGE) + 1} 页
            </span>
          </div>

          {error ? (
            <p className="rounded-lg border border-rose-900/50 bg-rose-950/20 px-3 py-2 text-sm text-rose-300">
              {error}
            </p>
          ) : loading ? (
            <p className="text-sm text-zinc-500">加载中…</p>
          ) : (
            <TradesTable items={items} onSelect={openDetail} selectedId={selected?.id} />
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

          {detailError ? (
            <p className="text-sm text-rose-400">详情加载失败：{detailError}</p>
          ) : null}
          {selected ? <TradeDetailPanel trade={selected} onClose={() => setSelected(null)} /> : null}
        </>
      ) : (
        <OrdersPanel />
      )}
    </div>
  );
}

function TradeDetailPanel({ trade, onClose }: { trade: TradeDetail; onClose: () => void }) {
  return (
    <section className="space-y-3 rounded-xl border border-amber-900/40 bg-zinc-900/50 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium text-amber-300">
            {trade.side} {fmtQty(trade.quantity)} {trade.symbol} @ {fmtUsdt(trade.price)}
          </h2>
          <p className="mono mt-1 text-[10px] text-zinc-600">{trade.id}</p>
        </div>
        <button type="button" onClick={onClose} className="text-xs text-zinc-500 hover:text-zinc-200">
          关闭
        </button>
      </div>
      <dl className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <Field label="状态" value={trade.status} tone={statusTone(trade.status)} />
        <Field label="风控" value={trade.risk_decision ?? "—"} tone={statusTone(trade.risk_decision)} />
        <Field label="权益（当时）" value={fmtUsdt(trade.equity)} />
        <Field label="置信度" value={trade.llm_confidence === null ? "—" : fmtPct(trade.llm_confidence * 100)} />
        <Field label="时间" value={fmtTime(trade.created_at)} />
        <Field label="交易所订单号" value={trade.external_order_id ?? "—"} />
        <Field label="目标" value={trade.goal_id ?? "—"} />
        <Field label="计划" value={trade.plan_id ?? "—"} />
      </dl>
      <div>
        <p className="text-xs text-zinc-500">决策理由</p>
        <p className="mt-1 text-sm text-zinc-300">{trade.decision_reason}</p>
        {trade.risk_reason ? (
          <p className="mt-1 text-xs text-zinc-500">风控理由：{trade.risk_reason}</p>
        ) : null}
      </div>
      {trade.positions && trade.positions.length > 0 ? (
        <div>
          <p className="text-xs text-zinc-500">决策时刻持仓</p>
          <p className="mono mt-1 text-xs text-zinc-400">
            {trade.positions
              .map((p) => `${p.asset} ${fmtQty(p.quantity)}${p.value_quote === null ? "" : ` (${fmtUsdt(p.value_quote)})`}`)
              .join(" · ")}
          </p>
        </div>
      ) : null}
      <div>
        <p className="text-xs text-zinc-500">完整输入（当天依据）</p>
        <pre className="mono mt-1 max-h-72 overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-[11px] text-zinc-400">
          {trade.context ? JSON.stringify(trade.context, null, 2) : "没有记录（这是旧数据或非 LLM 路径）"}
        </pre>
      </div>
    </section>
  );
}

function OrdersPanel() {
  const [items, setItems] = useState<OrderItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [symbol, setSymbol] = useState("");
  const [side, setSide] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await api.orders({
          limit: PAGE,
          offset,
          symbol: symbol.trim() || undefined,
          side: side || undefined,
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
  }, [offset, symbol, side]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-2 text-xs">
        <label className="flex flex-col gap-1 text-zinc-500">
          Symbol
          <input
            value={symbol}
            onChange={(e) => {
              setOffset(0);
              setSymbol(e.target.value.toUpperCase());
            }}
            placeholder="BTCUSDT"
            className="w-28 rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-200"
          />
        </label>
        <label className="flex flex-col gap-1 text-zinc-500">
          方向
          <select
            value={side}
            onChange={(e) => {
              setOffset(0);
              setSide(e.target.value);
            }}
            className="rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-200"
          >
            <option value="">全部</option>
            <option value="BUY">BUY</option>
            <option value="SELL">SELL</option>
          </select>
        </label>
        <span className="ml-auto text-zinc-500">共 {total} 张订单</span>
      </div>
      {error ? <p className="text-sm text-rose-400">{error}</p> : null}
      <div className="overflow-x-auto rounded-xl border border-zinc-800">
        <table className="w-full text-sm">
          <thead className="bg-zinc-900/80 text-left text-xs uppercase text-zinc-500">
            <tr>
              <th className="px-4 py-3">时间</th>
              <th className="px-4 py-3">Symbol</th>
              <th className="px-4 py-3">方向</th>
              <th className="px-4 py-3">请求量</th>
              <th className="px-4 py-3">成交</th>
              <th className="px-4 py-3">均价</th>
              <th className="px-4 py-3">状态</th>
              <th className="px-4 py-3">交易所单号</th>
              <th className="px-4 py-3">错误</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {items.map((o) => (
              <tr key={o.id} className="hover:bg-zinc-900/40">
                <td className="px-4 py-3 mono text-xs text-zinc-400 whitespace-nowrap">
                  {fmtTime(o.created_at)}
                </td>
                <td className="px-4 py-3 mono text-amber-300">{o.symbol}</td>
                <td className={o.side === "BUY" ? "px-4 py-3 text-emerald-400" : "px-4 py-3 text-rose-400"}>
                  {o.side}
                </td>
                <td className="px-4 py-3 mono">
                  {fmtQty(o.requested_amount)}{" "}
                  <span className="text-[10px] text-zinc-500">{o.amount_kind}</span>
                </td>
                <td className="px-4 py-3 mono">{fmtQty(o.filled_qty)}</td>
                <td className="px-4 py-3 mono">{fmtUsdt(o.avg_price)}</td>
                <td className={`px-4 py-3 ${statusTone(o.status)}`}>{o.status}</td>
                <td className="px-4 py-3 mono text-xs text-zinc-500">{o.exchange_order_id ?? "—"}</td>
                <td className="max-w-xs truncate px-4 py-3 text-xs text-rose-400" title={o.error ?? ""}>
                  {o.error ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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

function Field({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-wide text-zinc-600">{label}</dt>
      <dd className={`mono mt-0.5 break-all text-xs ${tone ?? "text-zinc-300"}`}>{value}</dd>
    </div>
  );
}
