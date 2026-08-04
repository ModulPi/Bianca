import type {
  AgentStatus,
  BalanceResponse,
  CheckpointHistoryResponse,
  CheckpointThreadListResponse,
  ConfirmPendingResponse,
  DecisionListResponse,
  FuturesStatus,
  HealthResponse,
  KlineListResponse,
  MessageResponse,
  NotifyStatus,
  PendingSignalListResponse,
  RiskEventListResponse,
  SessionListResponse,
  SessionSummary,
  TickerResponse,
  TradeListResponse,
  UsageSummary,
  TradingModeResponse,
  ValidationStatus,
  DashboardSnapshot,
  TickerListResponse,
} from "../types/api";
import { authHeaders } from "./token";

const BASE = "/api/v1";

class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...authHeaders(), ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      detail = body.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

async function download(path: string, filename: string): Promise<void> {
  const res = await fetch(`${BASE}${path}`, { headers: { ...authHeaders() } });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      detail = body.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export const api = {
  health: () => request<HealthResponse>("/health"),

  agentStatus: () => request<AgentStatus>("/agent/status"),
  agentStart: () => request<MessageResponse>("/agent/start", { method: "POST" }),
  agentStop: () => request<MessageResponse>("/agent/stop", { method: "POST" }),
  agentRecover: () => request<MessageResponse>("/agent/recover", { method: "POST" }),

  dashboardSnapshot: () => request<DashboardSnapshot>("/dashboard/snapshot"),

  summaryCurrent: () => request<SessionSummary>("/summary/session/current"),
  summaryLatest: () => request<SessionSummary>("/summary/session/latest"),
  summarySessions: (limit = 20, offset = 0) =>
    request<SessionListResponse>(`/summary/sessions?limit=${limit}&offset=${offset}`),
  summarySession: (id: string) => request<SessionSummary>(`/summary/sessions/${id}`),
  summaryDaily: (date?: string) =>
    request<SessionListResponse>(`/summary/daily${date ? `?date=${date}` : ""}`),
  exportSessionCsv: (id: string) =>
    download(`/summary/sessions/${encodeURIComponent(id)}/export.csv`, `session-${id.slice(0, 8)}.csv`),

  trades: (params?: { limit?: number; side?: string; status?: string }) => {
    const q = new URLSearchParams();
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.side) q.set("side", params.side);
    if (params?.status) q.set("status", params.status);
    const qs = q.toString();
    return request<TradeListResponse>(`/trades${qs ? `?${qs}` : ""}`);
  },

  usage: () => request<UsageSummary>("/usage"),
  balance: () => request<BalanceResponse>("/exchange/balance"),
  ticker: (symbol?: string) =>
    request<TickerResponse>(`/exchange/ticker${symbol ? `?symbol=${symbol}` : ""}`),
  tickers: (symbols?: string[]) => {
    const qs =
      symbols && symbols.length > 0
        ? `?symbols=${encodeURIComponent(symbols.join(","))}`
        : "";
    return request<TickerListResponse>(`/exchange/tickers${qs}`);
  },
  decisions: (limit = 50) => request<DecisionListResponse>(`/decisions?limit=${limit}`),
  riskEvents: (limit = 50) => request<RiskEventListResponse>(`/risk/events?limit=${limit}`),
  checkpointThreads: (limit = 50) =>
    request<CheckpointThreadListResponse>(`/checkpoints/threads?limit=${limit}`),
  checkpointHistory: (threadId: string, limit = 30) =>
    request<CheckpointHistoryResponse>(
      `/checkpoints/threads/${encodeURIComponent(threadId)}/history?limit=${limit}`,
    ),

  pendingSignals: (limit = 50) =>
    request<PendingSignalListResponse>(`/pending-signals?limit=${limit}`),
  confirmPending: (id: string) =>
    request<ConfirmPendingResponse>(`/pending-signals/${id}/confirm`, { method: "POST" }),
  rejectPending: (id: string) =>
    request<MessageResponse>(`/pending-signals/${id}/reject`, { method: "POST" }),

  validationStatus: () => request<ValidationStatus>("/validation/status"),
  validationEvaluate: () => request<ValidationStatus>("/validation/evaluate", { method: "POST" }),
  validationReset: () => request<MessageResponse>("/validation/reset", { method: "POST" }),
  notifyStatus: () => request<NotifyStatus>("/notify/status"),
  notifyTest: () => request<MessageResponse>("/notify/test", { method: "POST" }),
  notifyDailyDigest: () => request<MessageResponse>("/notify/daily-digest", { method: "POST" }),
  setTradingMode: (mode: string) =>
    request<TradingModeResponse>("/trading/mode", {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),
  tradingMode: () => request<TradingModeResponse>("/trading/mode"),

  marketKlines: (symbol = "BTCUSDT", interval = "1m", limit = 120) =>
    request<KlineListResponse>(
      `/market/klines?symbol=${encodeURIComponent(symbol)}&interval=${interval}&limit=${limit}`,
    ),

  futuresStatus: () => request<FuturesStatus>("/futures/status"),
};

export { ApiError };
export { getApiToken, setApiToken } from "./token";

export type SnapshotFetchResult =
  | { kind: "unchanged" }
  | { kind: "updated"; data: DashboardSnapshot; etag: string };

export async function fetchDashboardSnapshot(
  etag?: string | null,
): Promise<SnapshotFetchResult> {
  const headers: Record<string, string> = { ...authHeaders() };
  if (etag) {
    headers["If-None-Match"] = etag;
  }
  const res = await fetch(`${BASE}/dashboard/snapshot`, { headers });
  if (res.status === 304) {
    return { kind: "unchanged" };
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      detail = body.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  const nextEtag = res.headers.get("ETag");
  if (!nextEtag) {
    throw new ApiError(502, "snapshot response missing ETag");
  }
  const data = (await res.json()) as DashboardSnapshot;
  return { kind: "updated", data, etag: nextEtag };
}

export async function fetchDashboardSummary(
  running: boolean,
): Promise<SessionSummary | null> {
  try {
    if (running) {
      return await api.summaryCurrent();
    }
    return await api.summaryLatest();
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      return null;
    }
    throw err;
  }
}

export async function fetchTickersForSymbols(symbols: string[]): Promise<TickerResponse[]> {
  if (symbols.length === 0) {
    const single = await api.ticker();
    return single.symbol ? [single] : [];
  }
  const res = await api.tickers(symbols);
  return res.items;
}
