import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { getApiToken, setApiToken } from "../api/token";
import type { ApiKeyItem } from "../types/api";

const KEY_TYPES = [
  { value: "binance", label: "Binance（API_KEY:API_SECRET）" },
  { value: "llm", label: "LLM（OpenAI / DeepSeek 等）" },
  { value: "telegram", label: "Telegram（BOT_TOKEN:CHAT_ID）" },
  { value: "custom", label: "Custom" },
];

export default function SettingsPage() {
  const [tokenInput, setTokenInput] = useState(getApiToken());
  const [tokenSaved, setTokenSaved] = useState(false);
  const [secrets, setSecrets] = useState<ApiKeyItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState("binance");
  const [newValue, setNewValue] = useState("");
  const [creating, setCreating] = useState(false);
  const [formats, setFormats] = useState<Record<string, string>>({});

  const loadSecrets = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listSecrets();
      setSecrets(res.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "加载密钥失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSecrets();
    api
      .secretFormats()
      .then(setFormats)
      .catch(() => setFormats({}));
  }, [loadSecrets]);

  function saveToken() {
    setApiToken(tokenInput);
    setTokenSaved(true);
    setTimeout(() => setTokenSaved(false), 2000);
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim() || !newValue.trim()) return;
    setCreating(true);
    setActionMsg(null);
    setError(null);
    try {
      await api.createSecret({ name: newName.trim(), key_type: newType, value: newValue.trim() });
      setNewName("");
      setNewValue("");
      setActionMsg("密钥已保存");
      await loadSecrets();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "创建失败");
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(id: string, name: string) {
    if (!confirm(`确定删除密钥「${name}」？`)) return;
    setError(null);
    try {
      await api.deleteSecret(id);
      setActionMsg("已删除");
      await loadSecrets();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "删除失败");
    }
  }

  async function handleReload() {
    setError(null);
    try {
      const res = await api.reloadSecrets();
      setActionMsg(res.message);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "重载失败");
    }
  }

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-2xl font-semibold">设置</h1>
        <p className="text-sm text-zinc-500">API Token · 运行时密钥 · 重载配置</p>
      </header>

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <h2 className="mb-3 text-lg font-medium">API Token</h2>
        <p className="mb-4 text-sm text-zinc-500">
          后端启用 <code className="mono text-xs">API_TOKEN</code> 时，请求需携带 Bearer Token（存于浏览器 localStorage）。
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex min-w-[280px] flex-1 flex-col gap-1 text-sm">
            <span className="text-zinc-400">Token</span>
            <input
              type="password"
              value={tokenInput}
              onChange={(e) => setTokenInput(e.target.value)}
              placeholder="留空则不带 Authorization"
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 mono text-sm"
            />
          </label>
          <button
            type="button"
            onClick={saveToken}
            className="rounded-lg bg-amber-500/90 px-4 py-2 text-sm font-medium text-zinc-950 hover:bg-amber-400"
          >
            {tokenSaved ? "已保存" : "保存 Token"}
          </button>
        </div>
      </section>

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-medium">运行时密钥</h2>
            <p className="text-sm text-zinc-500">写入数据库后需点击「重载到运行时」生效（环境变量优先）。</p>
            {Object.keys(formats).length > 0 ? (
              <ul className="mt-2 text-xs text-zinc-500 space-y-1">
                {Object.entries(formats).map(([k, v]) => (
                  <li key={k}>
                    <span className="mono text-zinc-400">{k}</span>: {v}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
          <button
            type="button"
            onClick={() => void handleReload()}
            className="rounded-lg border border-zinc-600 px-4 py-2 text-sm hover:bg-zinc-800"
          >
            重载到运行时
          </button>
        </div>

        {actionMsg ? <p className="mb-3 text-sm text-emerald-400">{actionMsg}</p> : null}
        {error ? <p className="mb-3 text-sm text-rose-400">{error}</p> : null}

        {loading ? (
          <p className="text-sm text-zinc-500">加载中…</p>
        ) : secrets.length === 0 ? (
          <p className="mb-4 text-sm text-zinc-500">暂无已存密钥。</p>
        ) : (
          <div className="mb-6 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-zinc-800 text-zinc-500">
                  <th className="py-2 pr-4">名称</th>
                  <th className="py-2 pr-4">类型</th>
                  <th className="py-2 pr-4">掩码值</th>
                  <th className="py-2">操作</th>
                </tr>
              </thead>
              <tbody>
                {secrets.map((s) => (
                  <tr key={s.id} className="border-b border-zinc-800/60">
                    <td className="py-2 pr-4">{s.name}</td>
                    <td className="py-2 pr-4 mono text-xs text-zinc-400">{s.key_type}</td>
                    <td className="py-2 pr-4 mono text-xs">{s.masked_value}</td>
                    <td className="py-2">
                      <button
                        type="button"
                        onClick={() => void handleDelete(s.id, s.name)}
                        className="text-rose-400 hover:underline"
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <form onSubmit={(e) => void handleCreate(e)} className="grid gap-3 border-t border-zinc-800 pt-4 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-zinc-400">名称</span>
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              required
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-zinc-400">类型</span>
            <select
              value={newType}
              onChange={(e) => setNewType(e.target.value)}
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2"
            >
              {KEY_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm sm:col-span-2">
            <span className="text-zinc-400">值（仅提交一次，列表中掩码显示）</span>
            <input
              type="password"
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              required
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 mono text-sm"
            />
          </label>
          <div className="sm:col-span-2">
            <button
              type="submit"
              disabled={creating}
              className="rounded-lg bg-zinc-100 px-4 py-2 text-sm font-medium text-zinc-900 disabled:opacity-50"
            >
              {creating ? "保存中…" : "添加密钥"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
