import { useState } from "react";
import { api } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import type { FuturesProbe, ValidationStatus } from "../types/api";

function ProbeBadge({ label, probe }: { label: string; probe?: FuturesProbe | null }) {
  if (!probe) return null;
  const ok = probe.status === "ok";
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-zinc-400">{label}</span>
      <span className={ok ? "text-emerald-400" : "text-amber-400"}>
        {probe.status}
        {probe.detail ? ` · ${probe.detail}` : ""}
      </span>
    </div>
  );
}

export default function ValidationPage() {
  const { data, error, loading, refresh } = usePolling(() => api.validationStatus(), 10000);
  const futuresPoll = usePolling(() => api.futuresStatus(), 15000);
  const notifyPoll = usePolling(() => api.notifyStatus(), 30000);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function run(action: () => Promise<{ message?: string } | ValidationStatus | { mode: string }>) {
    setBusy(true);
    setMsg(null);
    try {
      const res = await action();
      if ("message" in res && res.message) {
        setMsg(res.message);
      } else if ("mode" in res) {
        setMsg(`已切换为 ${res.mode}`);
      } else {
        setMsg("已刷新");
      }
      await refresh();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  const metrics = data?.metrics ?? {};
  const reasons = data?.reasons ?? [];

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">模拟门禁 & 通知</h1>
        <p className="text-sm text-zinc-500">M8 · paper_validations · Telegram · live 模式切换</p>
      </header>

      {loading && !data ? <p className="text-sm text-zinc-500">加载中…</p> : null}
      {error ? <p className="text-sm text-rose-400">{error}</p> : null}

      {data ? (
        <div className="grid gap-4 md:grid-cols-2">
          <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 space-y-2">
            <h2 className="font-medium text-amber-300">验证状态</h2>
            <p className="text-sm">
              状态: <span className="text-zinc-200">{data.status}</span>
              {data.can_enable_live ? (
                <span className="ml-2 text-emerald-400">可切换 live</span>
              ) : (
                <span className="ml-2 text-zinc-500">未达标</span>
              )}
            </p>
            <ul className="text-xs text-zinc-400 space-y-1">
              <li>累计模拟 {Number(metrics.cumulative_hours ?? 0).toFixed(1)}h / 需 {data.requirements?.min_hours ?? 24}h</li>
              <li>闭环会话 {metrics.loop_closed_sessions ?? 0}</li>
              <li>BUY/SELL filled: {metrics.buy_filled_total ?? 0} / {metrics.sell_filled_total ?? 0}</li>
            </ul>
            {reasons.length > 0 ? (
              <ul className="text-xs text-rose-300/90 list-disc pl-4">
                {reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            ) : null}
          </section>

          <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 space-y-2">
            <h2 className="font-medium text-amber-300">运行模式</h2>
            <p className="text-sm text-zinc-300">当前: {data.trading_mode}</p>
            <p className="text-xs text-zinc-500">
              Telegram: {data.telegram_configured ? "已配置" : "未配置"}
              {notifyPoll.data
                ? ` · 邮件: ${notifyPoll.data.email_configured ? "已配置" : "未配置"}`
                : null}
            </p>
            <p className="text-xs text-zinc-500">
              切换 live 需在 <code className="mono">.env</code> 设置{" "}
              <code className="mono">LIVE_TRADING_CONFIRMED=true</code>，并通过上方模拟验证。
            </p>
            <div className="flex flex-wrap gap-2 pt-2">
              <button
                type="button"
                disabled={busy || !data.can_enable_live}
                className="rounded-lg bg-amber-600/80 px-3 py-1.5 text-sm disabled:opacity-40"
                onClick={() => run(() => api.setTradingMode("live"))}
              >
                切换 live
              </button>
              <button
                type="button"
                disabled={busy}
                className="rounded-lg bg-zinc-700 px-3 py-1.5 text-sm"
                onClick={() => run(() => api.setTradingMode("demo"))}
              >
                切回 demo
              </button>
            </div>
          </section>
        </div>
      ) : null}

      {futuresPoll.data ? (
        <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 space-y-3">
          <h2 className="font-medium text-amber-300">合约双栈</h2>
          <p className="text-sm text-zinc-300">
            {futuresPoll.data.enabled ? futuresPoll.data.message : futuresPoll.data.message}
          </p>
          <p className="text-xs text-zinc-500">连通性: {futuresPoll.data.connectivity}</p>
          <ProbeBadge label="U 本位 Demo" probe={futuresPoll.data.futures_u} />
          <ProbeBadge label="币本位 Demo" probe={futuresPoll.data.futures_coin} />
        </section>
      ) : futuresPoll.error ? (
        <p className="text-sm text-rose-400">{futuresPoll.error}</p>
      ) : null}

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy}
          className="rounded-lg border border-zinc-700 px-3 py-1.5 text-sm"
          onClick={() => run(() => api.notifyTest())}
        >
          通知测试（Telegram + 邮件）
        </button>
        <button
          type="button"
          disabled={busy || !notifyPoll.data?.email_configured}
          className="rounded-lg border border-zinc-700 px-3 py-1.5 text-sm disabled:opacity-40"
          title={notifyPoll.data?.email_configured ? undefined : "需配置 SMTP_* / NOTIFY_EMAIL_*"}
          onClick={() => run(() => api.notifyTest())}
        >
          邮件通道测试
        </button>
        <button
          type="button"
          disabled={busy}
          className="rounded-lg border border-zinc-700 px-3 py-1.5 text-sm"
          onClick={() => run(() => api.validationEvaluate())}
        >
          重新评估
        </button>
        <button
          type="button"
          disabled={busy}
          className="rounded-lg border border-zinc-700 px-3 py-1.5 text-sm"
          onClick={() => run(() => api.notifyDailyDigest())}
        >
          发送日摘要
        </button>
        <button
          type="button"
          disabled={busy}
          className="rounded-lg border border-zinc-700 px-3 py-1.5 text-sm text-zinc-400"
          onClick={() => run(() => api.validationReset())}
        >
          重置验证（测试）
        </button>
      </div>

      {msg ? <p className="text-sm text-zinc-400">{msg}</p> : null}
    </div>
  );
}
