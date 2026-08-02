import type {
  AgentStatus,
  BalanceResponse,
  HealthResponse,
  MessageResponse,
  SessionListResponse,
  SessionSummary,
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
