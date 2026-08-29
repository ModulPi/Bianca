import { useEffect, useMemo, useState } from "react";
import type { KlineItem, TradeLogItem } from "../types/api";
import { useKlinePolling } from "../hooks/useKlinePolling";

interface TradeMarker {
  id: string;
  side: string;
  price: number;
  quantity: number | null;
  at: string;
  status: string;
}

interface KlineChartPanelProps {
  symbols: string[];
  trades: TradeLogItem[];
}

const INTERVALS = ["1m", "5m", "15m", "1h"] as const;

function parseTime(iso: string): number {
  return new Date(iso).getTime();
}

function buildMarkers(trades: TradeLogItem[], symbol: string): TradeMarker[] {
  const sym = symbol.toUpperCase();
  return trades
    .filter(
      (t) =>
        t.symbol.toUpperCase() === sym &&
        (t.status === "filled" || t.status === "submitted") &&
        t.price != null &&
        (t.side === "BUY" || t.side === "SELL"),
    )
    .map((t) => ({
      id: t.id,
      side: t.side,
      price: t.price as number,
      quantity: t.quantity,
      at: t.created_at,
      status: t.status,
    }))
    .sort((a, b) => a.at.localeCompare(b.at));
}

function KlineSvg({
  candles,
  markers,
  height = 300,
}: {
  candles: KlineItem[];
  markers: TradeMarker[];
  height?: number;
}) {
  const w = 880;
  const padL = 58;
  const padR = 12;
  const padT = 16;
  const padB = 28;
  const innerW = w - padL - padR;
  const innerH = height - padT - padB;

  const { coords, priceMin, priceMax, t0, t1 } = useMemo(() => {
    if (candles.length === 0) {
      return { coords: [], priceMin: 0, priceMax: 1, t0: 0, t1: 1 };
    }
    const lows = candles.map((c) => c.low);
    const highs = candles.map((c) => c.high);
    const tradePrices = markers.map((m) => m.price);
    let pMin = Math.min(...lows, ...tradePrices);
    let pMax = Math.max(...highs, ...tradePrices);
    const pad = (pMax - pMin) * 0.06 || pMax * 0.001 || 1;
    pMin -= pad;
    pMax += pad;
    const span = pMax - pMin || 1;

    const tStart = parseTime(candles[0].time);
    const tEnd = parseTime(candles[candles.length - 1].time);
    const n = candles.length;
    const bodyW = Math.max(3, (innerW / n) * 0.65);

    const candleCoords = candles.map((c, i) => {
      const x = padL + ((i + 0.5) / n) * innerW;
      const y = (v: number) => padT + innerH - ((v - pMin) / span) * innerH;
      const up = c.close >= c.open;
      return {
        x,
        bodyW,
        wickTop: y(c.high),
        wickBottom: y(c.low),
        bodyTop: y(Math.max(c.open, c.close)),
        bodyBottom: y(Math.min(c.open, c.close)),
        up,
        time: c.time,
        close: c.close,
      };
    });

    return {
      coords: candleCoords,
      priceMin: pMin,
      priceMax: pMax,
      t0: tStart,
      t1: tEnd,
    };
  }, [candles, markers, innerH, innerW, padL, padT]);

  const timeToX = (iso: string) => {
    const t = parseTime(iso);
    const ratio = (t - t0) / (t1 - t0 || 1);
    return padL + Math.max(0, Math.min(1, ratio)) * innerW;
  };

  const priceToY = (price: number) => {
    const span = priceMax - priceMin || 1;
    return padT + innerH - ((price - priceMin) / span) * innerH;
  };

  const yTicks = [priceMin, (priceMin + priceMax) / 2, priceMax];

  if (candles.length === 0) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-950/50 px-4 py-12 text-center text-sm text-zinc-500">
        暂无 K 线数据
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${w} ${height}`} className="min-w-full" role="img" aria-label="K 线图">
        {yTicks.map((p) => {
          const y = priceToY(p);
          return (
            <g key={p}>
              <line x1={padL} y1={y} x2={w - padR} y2={y} stroke="#27272a" strokeWidth="1" />
              <text x={padL - 6} y={y + 4} textAnchor="end" fill="#71717a" fontSize="10">
                {p.toFixed(p >= 100 ? 1 : 4)}
              </text>
            </g>
          );
        })}

        {coords.map((c) => (
          <g key={c.time}>
            <line
              x1={c.x}
              y1={c.wickTop}
              x2={c.x}
              y2={c.wickBottom}
              stroke={c.up ? "#34d399" : "#fb7185"}
              strokeWidth="1"
            />
            <rect
              x={c.x - c.bodyW / 2}
              y={c.bodyTop}
              width={c.bodyW}
              height={Math.max(1, c.bodyBottom - c.bodyTop)}
              fill={c.up ? "#34d399" : "#fb7185"}
              opacity={0.9}
            />
          </g>
        ))}

        {markers.map((m) => {
          const x = timeToX(m.at);
          const y = priceToY(m.price);
          const isBuy = m.side === "BUY";
          const filled = m.status === "filled";
          const color = isBuy ? "#34d399" : "#fb7185";
          const size = filled ? 7 : 5;
          const points = isBuy
            ? `${x},${y - size} ${x - size},${y + size} ${x + size},${y + size}`
            : `${x},${y + size} ${x - size},${y - size} ${x + size},${y - size}`;
          return (
            <g key={m.id}>
              <polygon
                points={points}
                fill={color}
                stroke="#09090b"
                strokeWidth="1"
                opacity={filled ? 1 : 0.55}
              />
              <title>
                {m.side} {m.quantity?.toFixed(6) ?? "?"} @ {m.price} · {m.status} ·{" "}
                {new Date(m.at).toLocaleString()}
              </title>
            </g>
          );
        })}

        <text x={padL} y={height - 6} fill="#71717a" fontSize="10">
          {new Date(t0).toLocaleTimeString()}
        </text>
        <text x={w - padR} y={height - 6} textAnchor="end" fill="#71717a" fontSize="10">
          {new Date(t1).toLocaleTimeString()}
        </text>
      </svg>
    </div>
  );
}

export default function KlineChartPanel({ symbols, trades }: KlineChartPanelProps) {
  const available = symbols.length > 0 ? symbols : ["BTCUSDT"];
  const [symbol, setSymbol] = useState(available[0]);
  const [interval, setInterval] = useState<(typeof INTERVALS)[number]>("1m");

  const activeSymbol = available.includes(symbol) ? symbol : available[0];
  const { data, error, loading } = useKlinePolling(activeSymbol, interval, 30_000);
  const markers = useMemo(() => buildMarkers(trades, activeSymbol), [trades, activeSymbol]);

  useEffect(() => {
    if (!available.includes(symbol)) {
      setSymbol(available[0]);
    }
  }, [available, symbol]);

  const filledCount = markers.filter((m) => m.status === "filled").length;

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium text-zinc-300">K 线 · 买卖点</h2>
          <p className="text-xs text-zinc-500">
            ▲ 买入 · ▼ 卖出 · 实心=已成交 · 半透明=进行中
            {filledCount > 0 ? ` · ${filledCount} 笔成交标注` : ""}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <div className="flex rounded-lg ring-1 ring-zinc-800">
            {available.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setSymbol(s)}
                className={`px-2.5 py-1 text-xs first:rounded-l-lg last:rounded-r-lg ${
                  activeSymbol === s
                    ? "bg-amber-500/20 text-amber-400"
                    : "bg-zinc-900 text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {s}
              </button>
            ))}
          </div>
          <select
            value={interval}
            onChange={(e) => setInterval(e.target.value as (typeof INTERVALS)[number])}
            className="rounded-lg border border-zinc-800 bg-zinc-900 px-2 py-1 text-xs text-zinc-300"
          >
            {INTERVALS.map((iv) => (
              <option key={iv} value={iv}>
                {iv}
              </option>
            ))}
          </select>
        </div>
      </div>

      {error ? (
        <div className="rounded-lg border border-rose-900/50 bg-rose-950/20 px-4 py-3 text-sm text-rose-300">
          K 线加载失败：{error}
        </div>
      ) : loading && !data ? (
        <div className="rounded-lg border border-zinc-800 bg-zinc-950/50 px-4 py-12 text-center text-sm text-zinc-500">
          加载 K 线…
        </div>
      ) : (
        <>
          <KlineSvg candles={data?.items ?? []} markers={markers} />
          <div className="flex flex-wrap gap-3 text-xs text-zinc-600">
            <span>
              {data?.total ?? 0} 根 · {data?.interval ?? interval} · {data?.source ?? "—"}
            </span>
            {markers.length === 0 ? (
              <span>当前 symbol 暂无买卖标注</span>
            ) : (
              markers.slice(-6).map((m) => (
                <span key={m.id} className={m.side === "BUY" ? "text-emerald-500" : "text-rose-400"}>
                  {m.side} @ {m.price}
                </span>
              ))
            )}
          </div>
        </>
      )}
    </section>
  );
}
