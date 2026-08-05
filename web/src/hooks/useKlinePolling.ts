import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { KlineListResponse } from "../types/api";

export function useKlinePolling(symbol: string, interval: string, intervalMs: number, enabled = true) {
  const [data, setData] = useState<KlineListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!symbol) return;
    try {
      const resp = await api.marketKlines(symbol, interval, 120);
      setData(resp);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [symbol, interval]);

  useEffect(() => {
    if (!enabled || !symbol) return;
    setLoading(true);
    void refresh();
    const id = window.setInterval(() => void refresh(), intervalMs);
    return () => window.clearInterval(id);
  }, [refresh, intervalMs, enabled, symbol]);

  return { data, error, loading, refresh };
}
