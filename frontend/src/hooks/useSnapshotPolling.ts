import { useCallback, useEffect, useRef, useState } from "react";
import { fetchDashboardSnapshot, type SnapshotFetchResult } from "../api/client";
import type { DashboardSnapshot } from "../types/api";

export function useSnapshotPolling(intervalMs: number, enabled = true) {
  const [data, setData] = useState<DashboardSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [notModifiedCount, setNotModifiedCount] = useState(0);
  const etagRef = useRef<string | null>(null);

  const applyResult = useCallback((result: SnapshotFetchResult) => {
    if (result.kind === "updated") {
      setData(result.data);
      etagRef.current = result.etag;
      setNotModifiedCount(0);
    } else {
      setNotModifiedCount((n) => n + 1);
    }
  }, []);

  const fetchOnce = useCallback(
    async (etag: string | null) => {
      const result = await fetchDashboardSnapshot(etag);
      applyResult(result);
      setError(null);
    },
    [applyResult],
  );

  const refresh = useCallback(async () => {
    try {
      await fetchOnce(etagRef.current);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [fetchOnce]);

  /** 变更操作后强制拉全量 snapshot（跳过 If-None-Match） */
  const forceRefresh = useCallback(async () => {
    etagRef.current = null;
    try {
      await fetchOnce(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [fetchOnce]);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const id = window.setInterval(() => void refresh(), intervalMs);
    return () => window.clearInterval(id);
  }, [refresh, intervalMs, enabled]);

  return { data, error, loading, refresh, forceRefresh, notModifiedCount };
}
