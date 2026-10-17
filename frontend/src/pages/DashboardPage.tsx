import { useCallback } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import AgentControl from "../components/AgentControl";
import CollapsibleSection from "../components/CollapsibleSection";
import PositionsTable from "../components/PositionsTable";
import RiskEventsTable from "../components/RiskEventsTable";
import StatCard from "../components/StatCard";
import TradesTable from "../components/TradesTable";
import { usePolling } from "../hooks/usePolling";
import { fmtTime, fmtUsdt, statusTone } from "../utils/format";

function HealthBadge({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail?: string | null;
}) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-950/60 px-3 py-2">
      <p className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</p>
      <p className={`mono text-sm ${statusTone(value)}`}>{value}</p>
      {detail ? (
        <p className="mt-0.5 truncate text-[10px] text-zinc-600" title={detail}>
          {detail}
        </p>
      ) : null}
    </div>
  );
}

export default function DashboardPage() {
  const health = usePolling(api.health, 5_000);
  const agent = usePolling(api.agentStatus, 5_000);
  const portfolio = usePolling(api.portfolio, 10_000);
  // fetcher 必须是稳定引用：内联箭头函数每次渲染都换新，会让 usePolling 的
  // effect 每渲染重跑一次 —— 而每轮 fetch 都会 setState 触发下一轮，请求风暴。
  const fetchTrades = useCallback(() => api.trades({ limit: 8 }), []);
  const fetchRisks = useCallback(() => api.riskEvents({ limit: 5 }), []);
  const trades = usePolling(fetchTrades, 10_000);
  const risks = usePolling(fetchRisks, 10_000);
  const usage = usePolling(api.usage, 30_000);

  const p = portfolio.data;
  const a = agent.data;
  const h = health.data;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">看板</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Agent 运行态 · 权益与持仓 · 最近交易与风控事件
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs text-zinc-500">
          <Link to="/dialogue" className="rounded bg-zinc-800 px-2 py-1 hover:text-amber-300">
            去对话
          </Link>
          <Link to="/trades" className="rounded bg-zinc-800 px-2 py-1 hover:text-amber-300">
            全部交易
          </Link>
          <Link to="/goals" className="rounded bg-zinc-800 px-2 py-1 hover:text-amber-300">
            目标历史
          </Link>
        </div>
      </header>

      {health.error ? (
        <p className="rounded-xl border border-rose-900/50 bg-rose-950/20 px-4 py-3 text-sm text-rose-300">
          API 连不上：{health.error} —— 确认后端跑在 127.0.0.1:8000
        </p>
      ) : null}

      <AgentControl status={a} onChange={agent.refresh} />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="权益"
          value={fmtUsdt(p?.equity)}
          hint={p ? `快照 ${fmtTime(p.captured_at)}` : "还没有快照"}
        />
        <StatCard label="现金" value={fmtUsdt(p?.cash_quote)} hint={p ? "交易所计价币" : undefined} />
        <StatCard
          label="持仓市值"
          value={fmtUsdt(p?.holdings_value)}
          hint={p && p.positions.length > 0 ? `${p.positions.length} 个资产` : "无持仓"}
        />
        <StatCard
          label="今日 PnL"
          value={fmtUsdt(a?.daily_pnl)}
          hint="相对当日第一张快照"
          tone={(a?.daily_pnl ?? 0) >= 0 ? "positive" : "negative"}
        />
      </div>

      {portfolio.error ? (
        <p className="rounded-xl border border-zinc-800 bg-zinc-900/40 px-4 py-3 text-sm text-zinc-400">
          {portfolio.error} —— 到「持仓 · 权益」页点一次采集。
        </p>
      ) : null}

      <CollapsibleSection title="系统健康" badge={h?.status ?? undefined}>
        {h ? (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <HealthBadge label="Database" value={h.database} />
            <HealthBadge label="Binance demo" value={h.binance_demo} detail={h.binance_detail} />
            <HealthBadge label="LLM" value={h.llm} detail={`${h.llm_provider} · ${h.llm_detail ?? ""}`} />
            <HealthBadge label="Market data" value={h.market} detail={h.market_detail} />
          </div>
        ) : (
          <p className="text-sm text-zinc-500">读取中…</p>
        )}
      </CollapsibleSection>

      <CollapsibleSection title="持仓" badge={p?.captured_at ? fmtTime(p.captured_at) : undefined}>
        <PositionsTable portfolio={p} />
      </CollapsibleSection>

      <CollapsibleSection title="最近交易" badge={`${trades.data?.total ?? 0} 条`}>
        {trades.error ? (
          <p className="text-sm text-rose-400">{trades.error}</p>
        ) : (
          <TradesTable items={trades.data?.items ?? []} />
        )}
      </CollapsibleSection>

      <CollapsibleSection title="最近风控事件" defaultOpen={false}>
        {risks.error ? (
          <p className="text-sm text-rose-400">{risks.error}</p>
        ) : (
          <RiskEventsTable items={risks.data?.items ?? []} />
        )}
      </CollapsibleSection>

      <CollapsibleSection title="Token 用量" defaultOpen={false}>
        <div className="grid gap-3 sm:grid-cols-4">
          <StatCard label="今日 calls" value={String(usage.data?.today.calls ?? 0)} />
          <StatCard label="今日 tokens" value={(usage.data?.today.total_tokens ?? 0).toLocaleString()} />
          <StatCard label="累计 calls" value={String(usage.data?.total.calls ?? 0)} />
          <StatCard label="累计 tokens" value={(usage.data?.total.total_tokens ?? 0).toLocaleString()} />
        </div>
      </CollapsibleSection>
    </div>
  );
}
