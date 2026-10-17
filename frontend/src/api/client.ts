import type {
  AgentStatus,
  BackfillResponse,
  BalanceResponse,
  DecisionDetail,
  DecisionListResponse,
  DialogueReply,
  DrawdownResetResponse,
  EquityHistoryResponse,
  GoalItem,
  GoalListResponse,
  HealthResponse,
  KlineListResponse,
  MarketStatus,
  MessageListResponse,
  MessageResponse,
  OrderDetail,
  OrderListResponse,
  PlanItem,
  PlanListResponse,
  PortfolioResponse,
  RiskEventListResponse,
  TickerResponse,
  TradeDetail,
  TradeListResponse,
  UsageSummary,
} from "../types/api";

const BASE = "/api/v1";

export class ApiError extends Error {
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
      const body = (await res.json()) as { detail?: unknown };
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* 非 JSON 错误体，保留 statusText */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

function query(params: object): string {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params as Record<string, unknown>)) {
    if (value !== undefined && value !== null && value !== "") {
      q.set(key, String(value));
    }
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export interface ListFilter {
  limit?: number;
  offset?: number;
  since?: string;
  until?: string;
}

export const api = {
  health: () => request<HealthResponse>("/health"),

  agentStatus: () => request<AgentStatus>("/agent/status"),
  agentStart: () => request<MessageResponse>("/agent/start", { method: "POST" }),
  agentStop: () => request<MessageResponse>("/agent/stop", { method: "POST" }),

  // ---- 账本 ----
  trades: (params?: ListFilter & { symbol?: string; side?: string; status?: string; goal_id?: string }) =>
    request<TradeListResponse>(`/trades${query(params ?? {})}`),
  trade: (id: string) => request<TradeDetail>(`/trades/${encodeURIComponent(id)}`),
  orders: (params?: ListFilter & { symbol?: string; side?: string }) =>
    request<OrderListResponse>(`/orders${query(params ?? {})}`),
  order: (id: string) => request<OrderDetail>(`/orders/${encodeURIComponent(id)}`),

  decisions: (params?: ListFilter & { goal_id?: string; plan_id?: string }) =>
    request<DecisionListResponse>(`/decisions${query(params ?? {})}`),
  decision: (id: string) => request<DecisionDetail>(`/decisions/${encodeURIComponent(id)}`),

  riskEvents: (params?: ListFilter & { event_type?: string }) =>
    request<RiskEventListResponse>(`/risk/events${query(params ?? {})}`),
  resetDrawdown: (reason: string) =>
    request<DrawdownResetResponse>("/risk/reset-drawdown", {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),

  usage: () => request<UsageSummary>("/usage"),

  // ---- 组合 / 权益 ----
  portfolio: () => request<PortfolioResponse>("/portfolio"),
  captureSnapshot: () => request<PortfolioResponse>("/portfolio/snapshot", { method: "POST" }),
  equityHistory: (params?: { limit?: number; since?: string; until?: string }) =>
    request<EquityHistoryResponse>(`/equity/history${query(params ?? {})}`),

  // ---- 行情 ----
  marketStatus: () => request<MarketStatus>("/market/status"),
  marketKlines: (symbol: string, interval: string, limit = 120, end?: number) =>
    request<KlineListResponse>(
      `/market/klines${query({ symbol, interval, limit, end })}`,
    ),
  marketBackfill: () => request<BackfillResponse>("/market/backfill", { method: "POST" }),

  exchangeBalance: () => request<BalanceResponse>("/exchange/balance"),
  exchangeTicker: (symbol?: string) =>
    request<TickerResponse>(`/exchange/ticker${query({ symbol })}`),

  // ---- 对话 / 目标 ----
  dialogueCurrent: () => request<DialogueReply>("/dialogue/current"),
  dialogueShow: (goalId: string) =>
    request<DialogueReply>(`/dialogue/${encodeURIComponent(goalId)}`),
  dialogueStart: (text: string) =>
    request<DialogueReply>("/dialogue/start", {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  dialogueSay: (goalId: string, text: string) =>
    request<DialogueReply>(`/dialogue/${encodeURIComponent(goalId)}/say`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  dialogueConfirm: (goalId: string) =>
    request<DialogueReply>(`/dialogue/${encodeURIComponent(goalId)}/confirm`, {
      method: "POST",
    }),
  dialoguePlan: (goalId: string) =>
    request<DialogueReply>(`/dialogue/${encodeURIComponent(goalId)}/plan`, {
      method: "POST",
    }),

  goals: (params?: ListFilter & { status?: string }) =>
    request<GoalListResponse>(`/goals${query(params ?? {})}`),
  goal: (id: string) => request<GoalItem>(`/goals/${encodeURIComponent(id)}`),
  goalMessages: (id: string, params?: { limit?: number; offset?: number }) =>
    request<MessageListResponse>(
      `/goals/${encodeURIComponent(id)}/messages${query(params ?? {})}`,
    ),
  goalPlans: (id: string) =>
    request<PlanListResponse>(`/goals/${encodeURIComponent(id)}/plans`),
  plan: (id: string, includeTranscript = false) =>
    request<PlanItem>(
      `/plans/${encodeURIComponent(id)}${includeTranscript ? "?include_transcript=1" : ""}`,
    ),
};
