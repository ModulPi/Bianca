import type {
  AgentStatus,
  BalanceResponse,
  CheckpointHistoryResponse,
  CheckpointThreadListResponse,
  ConfirmPendingResponse,
  DecisionListResponse,
  HealthResponse,
  MessageResponse,
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
};

export { ApiError };

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
