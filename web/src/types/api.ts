export interface AgentStatus {
  running: boolean;
  last_tick: string | null;
  last_status: string | null;
  last_error: string | null;
  tick_count: number;
  daily_pnl: number;
  tick_interval: number;
  llm_auto_execute: boolean;
  session_id: string | null;
  session_started_at: string | null;
}

export interface SessionAgentInfo {
  tick_count: number;
  tick_interval_sec?: number | null;
  trading_style: string;
  last_status: string | null;
}

export interface SessionUsageInfo {
  llm_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
}

export interface SessionTradesInfo {
  buy_filled: number;
  sell_filled: number;
  failed: number;
  signal_only: number;
  loop_closed: boolean;
}

export interface SessionPnLInfo {
  cash_flow_usdt: number;
  realized_usdt: number;
  unrealized_usdt: number;
  total_usdt: number;
  daily_pnl_legacy: number;
}

export interface SessionPositionsInfo {
  base_asset: string;
  base_free: number;
  usdt_free: number;
  mark_price: number | null;
}

export interface SessionSummary {
  session_id: string;
  started_at: string;
  ended_at: string | null;
  agent: SessionAgentInfo;
  usage: SessionUsageInfo;
  trades: SessionTradesInfo;
  pnl: SessionPnLInfo;
  positions: SessionPositionsInfo;
  highlights: string[];
}

export interface SessionListResponse {
  items: SessionSummary[];
  total: number;
}

export interface TradeLogItem {
  id: string;
  symbol: string;
  side: string;
  quantity: number | null;
  price: number | null;
  order_type: string | null;
  status: string;
  risk_decision: string | null;
  risk_reason: string | null;
  decision_reason: string;
  llm_confidence: number | null;
  external_order_id: string | null;
  decision_id: string | null;
  created_at: string;
}

export interface TradeListResponse {
  items: TradeLogItem[];
  total: number;
}

export interface UsageBucket {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface UsageSummary {
  today: UsageBucket;
  total: UsageBucket;
}

export interface HealthResponse {
  status: string;
  database: string;
  binance_demo: string;
  binance_detail?: string | null;
  llm_provider: string;
  llm: string;
  llm_detail?: string | null;
}

export interface BalanceResponse {
  total: Record<string, number>;
  free: Record<string, number>;
  used: Record<string, number>;
}

export interface MessageResponse {
  message: string;
}
