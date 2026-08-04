import type { HealthResponse, TradingModeResponse, ValidationStatus } from "../types/api";

interface TradingModePanelProps {
  tradingMode: TradingModeResponse | null;
  validation: ValidationStatus | null;
  health: HealthResponse | null;
  tradeMarket?: string;
}

function statusDot(status: string | null | undefined): string {
  if (!status) return "bg-zinc-600";
  if (status === "ok") return "bg-emerald-400";
  if (status === "not_configured") return "bg-zinc-500";
  return "bg-rose-400";
}

export default function TradingModePanel({
  tradingMode,
  validation,
  health,
  tradeMarket,
}: TradingModePanelProps) {
  const mode = tradingMode?.mode ?? validation?.trading_mode ?? "demo";
  const isLive = mode === "live";
  const canLive = tradingMode?.can_enable_live ?? validation?.can_enable_live ?? false;
  const validationStatus = tradingMode?.validation_status ?? validation?.status ?? "—";

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4 h-full">
      <p className="text-xs uppercase tracking-wide text-zinc-500">实盘 / 门禁</p>
      <div className="mt-2 flex items-center gap-2">
        <span
          className={`rounded px-2 py-0.5 text-xs font-medium ${
            isLive
              ? "bg-rose-950 text-rose-300 ring-1 ring-rose-800"
              : "bg-sky-950 text-sky-300 ring-1 ring-sky-800"
          }`}
        >
          {isLive ? "Live" : "Demo"}
        </span>
        <span className="text-xs text-zinc-500">{tradeMarket ?? "crypto"}</span>
      </div>

      <dl className="mt-3 space-y-2 text-xs">
        <div className="flex justify-between gap-2">
          <dt className="text-zinc-500">可切 Live</dt>
          <dd className={canLive ? "text-emerald-400" : "text-zinc-400"}>
            {canLive ? "是" : "否"}
          </dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-zinc-500">模拟验证</dt>
          <dd className="text-zinc-300">{validationStatus}</dd>
        </div>
      </dl>

      {validation?.reasons && validation.reasons.length > 0 ? (
        <ul className="mt-2 space-y-1 text-[11px] text-amber-400/90">
          {validation.reasons.slice(0, 3).map((r) => (
            <li key={r}>· {r}</li>
          ))}
        </ul>
      ) : null}

      <div className="mt-3 border-t border-zinc-800 pt-3 space-y-1.5 text-xs">
        <div className="flex items-center justify-between gap-2">
          <span className="text-zinc-500">Demo 交易所</span>
          <span className="flex items-center gap-1.5">
            <span className={`h-1.5 w-1.5 rounded-full ${statusDot(health?.binance_demo)}`} />
            <span className="text-zinc-400">{health?.binance_demo ?? "—"}</span>
          </span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-zinc-500">Live 交易所</span>
          <span className="flex items-center gap-1.5">
            <span className={`h-1.5 w-1.5 rounded-full ${statusDot(health?.binance_live)}`} />
            <span className="text-zinc-400">{health?.binance_live ?? "—"}</span>
          </span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-zinc-500">LLM</span>
          <span className="flex items-center gap-1.5">
            <span className={`h-1.5 w-1.5 rounded-full ${statusDot(health?.llm)}`} />
            <span className="text-zinc-400 truncate">{health?.llm_provider ?? "—"}</span>
          </span>
        </div>
      </div>

      <p className="mt-3 text-[10px] leading-relaxed text-zinc-600">
        Live 需 .env 中 LIVE_TRADING_CONFIRMED=true 且模拟验证通过；看板只读展示。
      </p>
    </div>
  );
}
