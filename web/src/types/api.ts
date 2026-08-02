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
  execution_mode?: string;
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

export interface TickerResponse {
  symbol: string | null;
  last: number | null;
  bid: number | null;
  ask: number | null;
  timestamp: number | null;
}

export interface DecisionLogItem {
  id: string;
  model_used: string;
  prompt_summary: string | null;
  parsed_signal: Record<string, unknown>;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  created_at: string;
}

export interface DecisionListResponse {
  items: DecisionLogItem[];
  total: number;
}

export interface RiskEventItem {
  id: string;
  event_type: string;
  detail: Record<string, unknown>;
  related_trade_id: string | null;
  created_at: string;
}

export interface RiskEventListResponse {
  items: RiskEventItem[];
  total: number;
}

export interface CheckpointThreadItem {
  thread_id: string;
  checkpoint_count: number;
  latest_checkpoint_id: string | null;
}

export interface CheckpointThreadListResponse {
  items: CheckpointThreadItem[];
  total: number;
}

export interface CheckpointStateItem {
  checkpoint_id: string | null;
  thread_id: string | null;
  created_at: string | null;
  next_nodes: string[];
  metadata: Record<string, unknown>;
  state: Record<string, unknown>;
}

export interface CheckpointHistoryResponse {
  thread_id: string;
  items: CheckpointStateItem[];
  total: number;
}

export interface ConfirmPendingResponse {
  status: string;
  message?: string | null;
  trade_log_id?: string | null;
}

export interface PendingSignalItem {
  id: string;
  strategy_id?: string | null;
  signal: Record<string, unknown>;
  status: string;
  expires_at: string;
  created_at: string;
  session_id?: string | null;
  decision_id?: string | null;
}

export interface PendingSignalListResponse {
  items: PendingSignalItem[];
  total: number;
}

export interface StrategyItem {
  id: string;
  name: string;
  type: string;
  market: string;
  execution_mode: string;
  params: Record<string, unknown>;
  state: Record<string, unknown>;
  status: string;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  stopped_at?: string | null;
}

export interface StrategyListResponse {
  items: StrategyItem[];
  total: number;
}

export interface StrategyTickResponse {
  status: string;
  signal?: Record<string, unknown>;
  reason?: string | null;
  trade_log_id?: string | null;
  pending_signal_id?: string | null;
}
