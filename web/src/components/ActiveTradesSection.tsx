import { Link } from "react-router-dom";
import type { PendingSignalItem, RiskEventItem, TradeLogItem } from "../types/api";
import ConfirmQueue from "./ConfirmQueue";
import RiskEventsTable from "./RiskEventsTable";
import TradesTable from "./TradesTable";

interface ActiveTradesSectionProps {
  submitted: TradeLogItem[];
  recentFilled: TradeLogItem[];
  pendingSignals: PendingSignalItem[];
  showConfirmQueue: boolean;
  riskEvents: RiskEventItem[];
  onRefresh: () => void;
  embedded?: boolean;
}

export default function ActiveTradesSection({
  submitted,
  recentFilled,
  pendingSignals,
  showConfirmQueue,
  riskEvents,
  onRefresh,
  embedded = false,
}: ActiveTradesSectionProps) {
  return (
    <section className="space-y-6">
      {!embedded ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-medium">进行中交易 · 降级确认</h2>
          <Link to="/trades" className="text-sm text-amber-400 hover:underline">
            全部成交
          </Link>
        </div>
      ) : (
        <div className="flex justify-end">
          <Link to="/trades" className="text-sm text-amber-400 hover:underline">
            全部成交
          </Link>
        </div>
      )}

      {showConfirmQueue ? (
        <div>
          <h3 className="mb-2 text-sm font-medium text-amber-300">
            待确认信号 ({pendingSignals.length})
          </h3>
          <ConfirmQueue items={pendingSignals} onActionComplete={onRefresh} />
        </div>
      ) : null}

      <div>
        <h3 className="mb-2 text-sm font-medium text-zinc-400">
          已提交未成交 ({submitted.length})
        </h3>
        <TradesTable items={submitted} showSymbol />
      </div>

      <div>
        <h3 className="mb-2 text-sm font-medium text-zinc-400">最近成交</h3>
        <TradesTable items={recentFilled} showSymbol />
      </div>

      <div>
        <h3 className="mb-2 text-sm font-medium text-zinc-400">风控拒绝 / 事件</h3>
        <RiskEventsTable items={riskEvents} />
      </div>
    </section>
  );
}
