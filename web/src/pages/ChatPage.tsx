import { FormEvent, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { ChatMessageItem } from "../types/api";

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessageItem[]>([]);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void api.chatHistory().then((res) => setMessages(res.messages)).catch(() => undefined);
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function onSend(e: FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || sending) return;
    setSending(true);
    setError(null);
    setInput("");
    try {
      const res = await api.chatSend(text);
      setMessages(res.messages);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-3rem)] max-w-3xl">
      <header className="mb-4">
        <h1 className="text-2xl font-semibold">Agent 聊天</h1>
        <p className="text-sm text-zinc-500">用自然语言指挥 Agent：启动、暂停、查状态等</p>
      </header>

      <div className="flex-1 overflow-y-auto rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 space-y-3">
        {messages.length === 0 ? (
          <p className="text-sm text-zinc-500">试试：「启动 Agent」「暂停 BTC」「现在什么状态？」</p>
        ) : null}
        {messages.map((m, i) => (
          <div
            key={i}
            className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
              m.role === "user"
                ? "ml-auto bg-amber-950/50 text-amber-100"
                : "bg-zinc-800 text-zinc-200"
            }`}
          >
            {m.content}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {error ? <p className="text-sm text-rose-400 mt-2">{error}</p> : null}

      <form onSubmit={(e) => void onSend(e)} className="mt-4 flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="输入指令…"
          className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
          disabled={sending}
        />
        <button
          type="submit"
          disabled={sending || !input.trim()}
          className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-zinc-950 disabled:opacity-50"
        >
          发送
        </button>
      </form>
    </div>
  );
}
