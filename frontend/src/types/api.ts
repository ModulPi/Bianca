// 与 `agent/api/schemas.py` 一一对应。
//
// 口径约定（与后端相同）：
// - 取不到的值是 null，不是 0；能不编就不编。
// - 列表的 total 是**同过滤条件下的数据库计数**，不是本页条数。
// - 详情才带大字段（prompt / transcript / context），列表不带。

export interface HealthResponse {
  status: string;
  database: string;
  binance_demo: string;
  binance_detail: string | null;
  llm_provider: string;
  llm: string;
  llm_detail: string | null;
  market: string;
  market_detail: string | null;
}

export interface AgentStatus {
  running: boolean;
  last_tick: string | null;
  last_status: string | null;
  last_error: string | null;
  tick_count: number;
  daily_pnl: number | null;
  tick_interval: number;
  llm_auto_execute: boolean;
}

export interface MessageResponse {
  message: string;
}

export interface TradeSignal {
  action: string;
  symbol: string;
  amount: number | null;
  confidence: number;
  reason: string;
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
  goal_id: string | null;
  plan_id: string | null;
  created_at: string;
}

/** 交易详情：多出「当时依据什么」。列表不返回这些。 */
export interface TradeDetail extends TradeLogItem {
  context: Record<string, unknown> | null;
  equity: number | null;
  positions: PositionItem[] | null;
}

export interface TradeListResponse {
  items: TradeLogItem[];
  total: number;
}

export interface OrderItem {
  id: string;
  client_order_id: string;
  trade_id: string | null;
  decision_id: string | null;
  symbol: string;
  side: string;
  order_type: string;
  /** quote=计价币名义(USDT)，base=基础币数量。只看数值无法判断量纲。 */
  amount_kind: string;
  requested_amount: number;
  status: string;
  exchange_order_id: string | null;
  filled_qty: number | null;
  avg_price: number | null;
  cost_quote: number | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface OrderDetail extends OrderItem {
  fee: string | null;
  raw_response: string | null;
}

export interface OrderListResponse {
  items: OrderItem[];
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

export interface DecisionLogItem {
  id: string;
  model_used: string;
  prompt_summary: string | null;
  parsed_signal: Record<string, unknown>;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  goal_id: string | null;
  plan_id: string | null;
  plan_step: number | null;
  created_at: string;
}

export interface DecisionDetail extends DecisionLogItem {
  raw_output: string;
  prompt: Record<string, unknown> | null;
  prompt_variant: string | null;
  market_data: Record<string, unknown> | null;
}

export interface DecisionListResponse {
  items: DecisionLogItem[];
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

export interface PositionItem {
  asset: string;
  quantity: number;
  price: number | null;
  /** null = 取不到价，该持仓不计入权益。不是「值 0」。 */
  value_quote: number | null;
}

export interface HoldingGapItem {
  asset: string;
  exchange_qty: number;
  attributed_qty: number;
  unexplained_qty: number;
}

export interface PortfolioResponse {
  captured_at: string | null;
  equity: number;
  cash_quote: number;
  holdings_value: number;
  /** 非空意味着 equity 只是下界。 */
  unpriced: string[];
  positions: PositionItem[];
  gaps: HoldingGapItem[];
  notes: string[];
}

export interface EquityPoint {
  id: string;
  captured_at: string;
  equity: number;
  cash_quote: number;
  holdings_value: number;
}

export interface EquityHistoryResponse {
  items: EquityPoint[];
  total: number;
}

export interface BalanceResponse {
  total: Record<string, number>;
  free: Record<string, number>;
  used: Record<string, number>;
}

export interface TickerResponse {
  symbol: string | null;
  last: number | null;
  bid: number | null;
  ask: number | null;
  timestamp: number | null;
  high_24h: number | null;
  low_24h: number | null;
  change_24h_pct: number | null;
  volume_24h_quote_usdt: number | null;
}

export interface MarketSession {
  connected: boolean;
  bars_written_session: number;
  reconnects_session: number;
  gaps_filled: number;
  last_gap_check_at: string | null;
  backfill_running: boolean;
  backfill_last: Record<string, unknown> | null;
  backfill_error: string | null;
  last_error: string | null;
  last_error_at: string | null;
  last_write_at: string | null;
  started_at: string | null;
}

export interface MarketStatus {
  database: string;
  lock_file: string | null;
  symbols: string[];
  interval: string;
  interval_error: string | null;
  last_bar_open_time: number | null;
  last_closed_bar_open_time: number | null;
  lag_seconds: number | null;
  bars_count_24h: number;
  bars_expected_24h: number | null;
  gap_count_24h: number | null;
  /** self / other / none / unknown */
  collector_owner: string;
  data_flowing: boolean;
  session: MarketSession | null;
}

export interface BackfillResponse {
  status: string;
  detail: string | null;
  requests: number | null;
  rows_received: number | null;
  rows_inserted: number | null;
  failed_chunks: number | null;
  ranges: number | null;
  duration_s: number | null;
}

export interface DrawdownResetResponse {
  reset_at: string;
  reason: string;
  peak_before: number | null;
  current_equity: number | null;
  drawdown_before: number | null;
  previous_reset_at: string | null;
}

export interface KlineItem {
  /** bar 开始时间，epoch ms。 */
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  quote_volume: number | null;
  trades: number | null;
  taker_buy_base: number | null;
  taker_buy_quote: number | null;
}

export interface KlineListResponse {
  symbol: string;
  interval: string;
  items: KlineItem[];
  total: number;
  source: string;
}

// ---------------------------------------------------------------- 对话层

export interface DialogueReply {
  goal_id: string;
  status: string;
  /** 回给用户看的话。 */
  text: string;
  changed: string[];
  unclear: string[];
  activated: boolean;
  /** 这一次规划尝试的 plans 行；不等于「目标已经有计划」。 */
  plan_id: string | null;
}

export interface GoalItem {
  id: string;
  status: string;
  raw_text: string;
  parsed: Record<string, unknown> | null;
  objective: string | null;
  symbols: string[] | null;
  target_return_pct: number | null;
  target_equity: number | null;
  max_drawdown_pct: number | null;
  horizon_hours: number | null;
  autonomy: string | null;
  stated_capital: number | null;
  baseline_equity: number | null;
  risk_snapshot: Record<string, unknown> | null;
  plan_id: string | null;
  created_at: string;
  confirmed_at: string | null;
  deadline_at: string | null;
  ended_at: string | null;
  end_reason: string | null;
}

export interface GoalListResponse {
  items: GoalItem[];
  total: number;
}

export interface MessageItem {
  id: string;
  goal_id: string;
  role: string;
  content: string;
  created_at: string;
}

export interface MessageListResponse {
  items: MessageItem[];
  total: number;
}

export interface PlanItem {
  id: string;
  goal_id: string;
  /** null = 真的没解出来，不拿散文顶上。 */
  plan: Record<string, unknown> | null;
  plan_shape: string;
  final_text: string;
  invocations: unknown[];
  shapes: string[];
  usage: Record<string, unknown> | null;
  errors: string[];
  rounds: number;
  created_at: string;
  transcript?: unknown[];
}

export interface PlanListResponse {
  items: PlanItem[];
  total: number;
}
