import { type FormEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { DialogueReply, MessageItem } from "../types/api";
import { fmtTime, statusTone } from "../utils/format";

const STATUS_LABEL: Record<string, string> = {
  proposed: "待确认",
  active: "已生效",
  achieved: "已达成",
  aborted: "已中止",
  expired: "已到期",
};

export default function DialoguePage() {
  const [reply, setReply] = useState<DialogueReply | null>(null);
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);

  const load = async () => {
    try {
      const current = await api.dialogueCurrent();
      setReply(current);
      setError(null);
      try {
        const res = await api.goalMessages(current.goal_id, { limit: 200 });
        setMessages(res.items);
      } catch {
        // F-1 之前没有这个端点；不影响对话本身
        setMessages([]);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setReply(null);
        setMessages([]);
        setError(null);
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), 10_000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, reply]);

  const run = async (fn: () => Promise<DialogueReply>) => {
    setBusy(true);
    setError(null);
    try {
      const res = await fn();
      setReply(res);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const send = async (e: FormEvent) => {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    if (reply) {
      await run(() => api.dialogueSay(reply.goal_id, text));
    } else {
      await run(() => api.dialogueStart(text));
    }
  };

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
      <div className="flex h-[calc(100vh-6rem)] flex-col">
        <header className="mb-3">
          <h1 className="text-2xl font-semibold">对话</h1>
          <p className="mt-1 text-sm text-zinc-500">
            用自然语言说目标。agent 会复述、点名问缺什么；确认后才生效。
          </p>
        </header>

        {loading ? (
          <p className="text-sm text-zinc-500">读取当前目标…</p>
        ) : (
          <>
            <div className="flex-1 space-y-3 overflow-y-auto rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
              {messages.length > 0
                ? messages.map((m) => <Bubble key={m.id} role={m.role} text={m.content} at={m.created_at} />)
                : reply ? (
                  <>
                    <Bubble role="assistant" text={reply.text} at={null} />
                  </>
                ) : (
                  <p className="text-sm text-zinc-500">
                    当前没有未结束的目标。说一句你想做什么，例如：
                    <br />
                    「用 10000 USDT 本金，两周内在 BTCUSDT 上赚 3%，最大回撤不超过 5%」
                  </p>
                )}
              <div ref={bottomRef} />
            </div>

            {error ? <p className="mt-2 text-sm text-rose-400">{error}</p> : null}

            <form onSubmit={(e) => void send(e)} className="mt-3 flex gap-2">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder={reply ? "补充或修改目标…" : "说出你的目标…"}
                className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
                disabled={busy}
              />
              <button
                type="submit"
                disabled={busy || !input.trim()}
                className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-zinc-950 disabled:opacity-50"
              >
                {busy ? "处理中…" : reply ? "说" : "开始"}
              </button>
            </form>

            {reply && reply.status === "proposed" ? (
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void run(() => api.dialogueConfirm(reply.goal_id))}
                  className="rounded-lg bg-emerald-800 px-4 py-2 text-sm font-medium text-emerald-100 disabled:opacity-50"
                >
                  确认目标
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void run(() => api.dialoguePlan(reply.goal_id))}
                  className="rounded-lg border border-zinc-700 px-4 py-2 text-sm text-zinc-300 disabled:opacity-50"
                >
                  直接规划
                </button>
              </div>
            ) : null}
          </>
        )}
      </div>

      <aside className="space-y-3">
        <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-zinc-300">当前目标</h2>
            {reply ? (
              <span className={`mono text-xs ${statusTone(reply.status)}`}>
                {STATUS_LABEL[reply.status] ?? reply.status}
              </span>
            ) : null}
          </div>
          {reply ? (
            <div className="mt-2 space-y-2 text-xs text-zinc-400">
              <p className="mono break-all text-[10px] text-zinc-600">{reply.goal_id}</p>
              {reply.changed.length > 0 ? (
                <p>
                  <span className="text-zinc-500">这一轮改动：</span>
                  {reply.changed.join("、")}
                </p>
              ) : null}
              {reply.unclear.length > 0 ? (
                <p className="text-amber-300">还缺：{reply.unclear.join("、")}</p>
              ) : null}
              {reply.activated ? <p className="text-emerald-400">目标已生效</p> : null}
              {reply.plan_id ? (
                <p className="mono text-[10px] text-zinc-600">本次规划 {reply.plan_id}</p>
              ) : null}
              <Link
                to={`/goals/${reply.goal_id}`}
                className="inline-block rounded bg-zinc-800 px-2 py-1 text-xs hover:text-amber-300"
              >
                查看目标详情与计划 →
              </Link>
            </div>
          ) : (
            <p className="mt-2 text-xs text-zinc-500">没有未结束的目标。</p>
          )}
        </section>

        <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 text-xs leading-relaxed text-zinc-500">
          <p className="text-zinc-400">这一页的边界</p>
          <ul className="mt-1 list-inside list-disc space-y-1">
            <li>「说」永远不会激活目标；只有「确认」会。</li>
            <li>空着的地方就是「用户没说」，不是默认值。</li>
            <li>规划失败是正常结果，失败记录同样会留下。</li>
          </ul>
        </section>
      </aside>
    </div>
  );
}

function Bubble({
  role,
  text,
  at,
}: {
  role: string;
  text: string;
  at: string | null;
}) {
  const isUser = role === "user";
  return (
    <div className={`max-w-[85%] ${isUser ? "ml-auto" : ""}`}>
      <div
        className={`whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ${
          isUser ? "bg-amber-950/50 text-amber-100" : "bg-zinc-800 text-zinc-200"
        }`}
      >
        {text}
      </div>
      <p className={`mt-1 text-[10px] text-zinc-600 ${isUser ? "text-right" : ""}`}>
        {role} · {at ? fmtTime(at) : "刚刚"}
      </p>
    </div>
  );
}
