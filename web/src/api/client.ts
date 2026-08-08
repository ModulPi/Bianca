import type {
  AgentStatus,
  BalanceResponse,
  CheckpointHistoryResponse,
  CheckpointThreadListResponse,
  ConfirmPendingResponse,
  DecisionListResponse,
  DashboardSnapshot,
  HealthResponse,
  KlineListResponse,
  MessageResponse,
  PositionListResponse,
  FuturesStatusResponse,
  NotifyStatusResponse,
  PendingSignalListResponse,
  RiskEventListResponse,
  SessionListResponse,
  SessionSummary,
  StrategyListResponse,
  StrategyItem,
  StrategyTickResponse,
  TickerResponse,
  TradeListResponse,
  UsageSummary,
  TradingModeResponse,
  ValidationStatus,
} from "../types/api";

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
    headers: { "Content-Type": "application/json", ...init?.headers },
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

export const api = {
  health: () => request<HealthResponse>("/health"),

  agentStatus: () => request<AgentStatus>("/agent/status"),
  agentStart: () => request<MessageResponse>("/agent/start", { method: "POST" }),
  agentStop: () => request<MessageResponse>("/agent/stop", { method: "POST" }),
  agentRecover: () => request<MessageResponse>("/agent/recover", { method: "POST" }),

  marketKlines: (symbol: string, interval: string, limit = 120) =>
    request<KlineListResponse>(
      `/market/klines?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(interval)}&limit=${limit}`,
    ),

  summaryCurrent: () => request<SessionSummary>("/summary/session/current"),
  summaryLatest: () => request<SessionSummary>("/summary/session/latest"),
  summarySessions: (limit = 20, offset = 0) =>
    request<SessionListResponse>(`/summary/sessions?limit=${limit}&offset=${offset}`),
  summarySession: (id: string) => request<SessionSummary>(`/summary/sessions/${id}`),
  summaryDaily: (date?: string) =>
    request<SessionListResponse>(`/summary/daily${date ? `?date=${date}` : ""}`),

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
  positions: (strategyId?: string, limit = 50) => {
    const q = new URLSearchParams();
    if (strategyId) q.set("strategy_id", strategyId);
    q.set("limit", String(limit));
    return request<PositionListResponse>(`/positions?${q.toString()}`);
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

  strategies: (limit = 50) => request<StrategyListResponse>(`/strategies?limit=${limit}`),
  createStrategy: (body: {
    name: string;
    type: string;
    execution_mode?: string;
    market?: string;
    params?: Record<string, unknown>;
  }) => request<StrategyItem>("/strategies", { method: "POST", body: JSON.stringify(body) }),
  startStrategy: (id: string) =>
    request<StrategyItem>(`/strategies/${id}/start`, { method: "POST" }),
  stopStrategy: (id: string) =>
    request<StrategyItem>(`/strategies/${id}/stop`, { method: "POST" }),
  tickStrategy: (id: string) =>
    request<StrategyTickResponse>(`/strategies/${id}/tick`, { method: "POST" }),

  validationStatus: () => request<ValidationStatus>("/validation/status"),
  validationEvaluate: () => request<ValidationStatus>("/validation/evaluate", { method: "POST" }),
  validationReset: () => request<MessageResponse>("/validation/reset", { method: "POST" }),
  notifyStatus: () => request<NotifyStatusResponse>("/notify/status"),
  notifyTest: () => request<MessageResponse>("/notify/test", { method: "POST" }),
  notifyDailyDigest: () =>
    request<MessageResponse>("/notify/daily-digest", { method: "POST" }),
  tradingModeGet: () => request<TradingModeResponse>("/trading/mode"),
  futuresStatus: () => request<FuturesStatusResponse>("/futures/status"),
  setTradingMode: (mode: string) =>
    request<TradingModeResponse>("/trading/mode", {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),

  exportSessionCsv: async (sessionId: string) => {
    const res = await fetch(`${BASE}/summary/sessions/${encodeURIComponent(sessionId)}/export.csv`);
    if (!res.ok) {
      throw new ApiError(res.status, res.statusText);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `session-${sessionId.slice(0, 8)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  },
};

export { ApiError };

export type SnapshotFetchResult =
  | { kind: "updated"; data: DashboardSnapshot; etag: string | null }
  | { kind: "not_modified" };

export async function fetchDashboardSnapshot(etag: string | null): Promise<SnapshotFetchResult> {
  const headers: Record<string, string> = {};
  if (etag) headers["If-None-Match"] = etag;

  const res = await fetch(`${BASE}/dashboard/snapshot`, { headers });
  if (res.status === 304) {
    return { kind: "not_modified" };
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
  const data = (await res.json()) as DashboardSnapshot;
  return { kind: "updated", data, etag: res.headers.get("ETag") };
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
