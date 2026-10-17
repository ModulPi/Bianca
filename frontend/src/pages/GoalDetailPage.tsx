import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { GoalItem, MessageItem, PlanItem } from "../types/api";
import { fmtPct, fmtTime, fmtUsdt, statusTone } from "../utils/format";

const STATUS_LABEL: Record<string, string> = {
  proposed: "待确认",
  active: "已生效",
  achieved: "已达成",
  aborted: "已中止",
  expired: "已到期",
};

export default function GoalDetailPage() {
  const { id = "" } = useParams();
  const [goal, setGoal] = useState<GoalItem | null>(null);
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [plans, setPlans] = useState<PlanItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [planDetail, setPlanDetail] = useState<PlanItem | null>(null);
  const [includeTranscript, setIncludeTranscript] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [g, m, p] = await Promise.all([
          api.goal(id),
          api.goalMessages(id, { limit: 200 }),
          api.goalPlans(id),
        ]);
        if (!alive) return;
        setGoal(g);
        setMessages(m.items);
        setPlans(p.items);
        setError(null);
      } catch (err) {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 15_000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [id]);

  const openPlan = async (planId: string, transcript = includeTranscript) => {
    try {
      setPlanDetail(await api.plan(planId, transcript));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  if (error) {
    return (
      <div className="space-y-3">
        <Link to="/goals" className="text-xs text-zinc-500 hover:text-amber-300">
          ← 返回目标历史
        </Link>
        <p className="rounded-lg border border-rose-900/50 bg-rose-950/20 px-3 py-2 text-sm text-rose-300">
          {error}
        </p>
      </div>
    );
  }

  if (!goal) {
    return <p className="text-sm text-zinc-500">读取目标…</p>;
  }

  return (
    <div className="space-y-5">
      <div>
        <Link to="/goals" className="text-xs text-zinc-500 hover:text-amber-300">
          ← 返回目标历史
        </Link>
        <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold">{goal.objective ?? "（经理还没复述）"}</h1>
            <p className="mono mt-1 text-[10px] text-zinc-600">{goal.id}</p>
          </div>
          <span className={`mono rounded px-2 py-0.5 text-xs ${statusTone(goal.status)}`}>
            {STATUS_LABEL[goal.status] ?? goal.status}
          </span>
        </div>
      </div>

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
        <h2 className="text-sm font-medium text-zinc-300">用户原话</h2>
        <p className="mt-1 whitespace-pre-wrap text-sm text-zinc-200">{goal.raw_text}</p>
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
          <h2 className="text-sm font-medium text-zinc-300">目标维度</h2>
          <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <Field label="标的" value={goal.symbols?.join("、") ?? "没说"} />
            <Field label="目标收益" value={goal.target_return_pct === null ? "没说" : fmtPct(goal.target_return_pct)} />
            <Field label="目标权益" value={goal.target_equity === null ? "没说" : fmtUsdt(goal.target_equity)} />
            <Field label="回撤上限" value={goal.max_drawdown_pct === null ? "没说" : fmtPct(goal.max_drawdown_pct)} />
            <Field label="期限" value={goal.horizon_hours === null ? "没说" : `${goal.horizon_hours}h`} />
            <Field label="自主度" value={goal.autonomy ?? "没说"} />
            <Field label="口述本金" value={goal.stated_capital === null ? "没说" : fmtUsdt(goal.stated_capital)} />
            <Field label="基准权益" value={goal.baseline_equity === null ? "未固化" : fmtUsdt(goal.baseline_equity)} />
          </dl>
          <p className="mt-2 text-[10px] text-zinc-600">
            「没说」不是默认值 —— 这些维度在库里就是空。
          </p>
        </section>

        <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
          <h2 className="text-sm font-medium text-zinc-300">时间线</h2>
          <dl className="mt-3 space-y-2 text-xs">
            <Field label="创建" value={fmtTime(goal.created_at)} />
            <Field label="确认" value={fmtTime(goal.confirmed_at)} />
            <Field label="截止" value={fmtTime(goal.deadline_at)} />
            <Field label="结束" value={fmtTime(goal.ended_at)} />
            {goal.end_reason ? <Field label="结束原因" value={goal.end_reason} /> : null}
          </dl>
          <div className="mt-3 flex flex-wrap gap-2">
            <Link
              to={`/decisions?goal_id=${encodeURIComponent(goal.id)}`}
              className="rounded bg-zinc-800 px-2 py-1 text-xs text-zinc-300 hover:text-amber-300"
            >
              该目标的决策 →
            </Link>
            <Link
              to={`/trades?goal_id=${encodeURIComponent(goal.id)}`}
              className="rounded bg-zinc-800 px-2 py-1 text-xs text-zinc-300 hover:text-amber-300"
            >
              该目标的交易 →
            </Link>
          </div>
        </section>
      </div>

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
        <h2 className="text-sm font-medium text-zinc-300">
          风险快照 <span className="text-[10px] font-normal text-zinc-600">确认时固化，之后只读（ADR-021）</span>
        </h2>
        {goal.risk_snapshot ? (
          <pre className="mono mt-2 max-h-60 overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-[11px] text-zinc-400">
            {JSON.stringify(goal.risk_snapshot, null, 2)}
          </pre>
        ) : (
          <p className="mt-2 text-xs text-zinc-500">还没有快照 —— 目标未确认。</p>
        )}
      </section>

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
        <h2 className="text-sm font-medium text-zinc-300">
          解析结果 <span className="text-[10px] font-normal text-zinc-600">机器第一次读出来的，与定稿分开存</span>
        </h2>
        <pre className="mono mt-2 max-h-60 overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-[11px] text-zinc-400">
          {goal.parsed ? JSON.stringify(goal.parsed, null, 2) : "解析结果为空"}
        </pre>
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-medium text-zinc-300">对话记录（{messages.length} 条）</h2>
        {messages.length === 0 ? (
          <p className="rounded-xl border border-zinc-800 px-4 py-6 text-center text-sm text-zinc-500">
            这个目标的 messages 表里还没有记录
          </p>
        ) : (
          <div className="space-y-2">
            {messages.map((m) => (
              <div
                key={m.id}
                className={`max-w-[90%] rounded-lg px-3 py-2 text-sm ${
                  m.role === "user"
                    ? "ml-auto bg-amber-950/40 text-amber-100"
                    : "bg-zinc-800 text-zinc-200"
                }`}
              >
                <p className="whitespace-pre-wrap">{m.content}</p>
                <p className="mt-1 text-[10px] text-zinc-600">
                  {m.role} · {fmtTime(m.created_at)}
                </p>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-medium text-zinc-300">规划尝试（{plans.length} 次）</h2>
        {plans.length === 0 ? (
          <p className="rounded-xl border border-zinc-800 px-4 py-6 text-center text-sm text-zinc-500">
            还没有规划过这个目标
          </p>
        ) : (
          <div className="space-y-2">
            {plans.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => void openPlan(p.id)}
                className="block w-full rounded-xl border border-zinc-800 bg-zinc-900/40 p-3 text-left hover:border-zinc-700"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="mono text-xs text-zinc-400">
                    {fmtTime(p.created_at)} · {p.plan_shape} · {p.rounds} 轮
                  </span>
                  <span className={p.plan ? "text-xs text-emerald-400" : "text-xs text-rose-400"}>
                    {p.plan ? "解出计划" : "未解出"}
                  </span>
                </div>
                {p.errors.length > 0 ? (
                  <p className="mt-1 truncate text-xs text-rose-300">{p.errors.join("；")}</p>
                ) : null}
              </button>
            ))}
          </div>
        )}
      </section>

      {planDetail ? (
        <section className="space-y-3 rounded-xl border border-amber-900/40 bg-zinc-900/50 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-medium text-amber-300">计划 {planDetail.id}</h2>
            <div className="flex items-center gap-3">
              <label className="flex items-center gap-1 text-xs text-zinc-500">
                <input
                  type="checkbox"
                  checked={includeTranscript}
                  onChange={(e) => {
                    setIncludeTranscript(e.target.checked);
                    void openPlan(planDetail.id, e.target.checked);
                  }}
                />
                含完整 transcript
              </label>
              <button
                type="button"
                onClick={() => setPlanDetail(null)}
                className="text-xs text-zinc-500 hover:text-zinc-200"
              >
                关闭
              </button>
            </div>
          </div>
          <Block title="计划（JSON）" value={planDetail.plan ?? "未解出 —— 不拿散文顶上"} />
          <Block title="模型最终文本" value={planDetail.final_text || "（空）"} />
          <Block title="工具调用" value={planDetail.invocations} />
          <Block title="usage" value={planDetail.usage ?? "无"} />
          {planDetail.errors.length > 0 ? <Block title="错误" value={planDetail.errors} /> : null}
          {planDetail.transcript ? <Block title="transcript" value={planDetail.transcript} /> : null}
        </section>
      ) : null}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-wide text-zinc-600">{label}</dt>
      <dd className="mono mt-0.5 break-words text-xs text-zinc-300">{value}</dd>
    </div>
  );
}

function Block({ title, value }: { title: string; value: unknown }) {
  return (
    <div>
      <p className="text-xs text-zinc-500">{title}</p>
      <pre className="mono mt-1 max-h-72 overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-[11px] text-zinc-400">
        {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}
