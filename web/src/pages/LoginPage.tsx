import { FormEvent, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { getApiToken, setApiToken } from "../api/token";

export default function LoginPage() {
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  if (getApiToken()) {
    return <Navigate to="/" replace />;
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    setApiToken(password.trim());
    try {
      await api.agentStatus();
      navigate("/", { replace: true });
    } catch (err) {
      setApiToken("");
      if (err instanceof ApiError && err.status === 401) {
        setError("密码不对，请重试");
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-zinc-950 p-6">
      <form
        onSubmit={(e) => void onSubmit(e)}
        className="w-full max-w-sm rounded-xl border border-zinc-800 bg-zinc-900/60 p-6 space-y-4"
      >
        <div>
          <p className="text-xs uppercase tracking-widest text-zinc-500">Bianca</p>
          <h1 className="text-xl font-semibold text-amber-400">看板登录</h1>
          <p className="text-sm text-zinc-500 mt-1">输入 API 密码（.env 里的 API_TOKEN）</p>
        </div>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="密码"
          className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
          autoFocus
        />
        {error ? <p className="text-sm text-rose-400">{error}</p> : null}
        <button
          type="submit"
          disabled={loading || !password.trim()}
          className="w-full rounded-lg bg-amber-600 py-2 text-sm font-medium text-zinc-950 disabled:opacity-50"
        >
          {loading ? "验证中…" : "进入看板"}
        </button>
      </form>
    </div>
  );
}
