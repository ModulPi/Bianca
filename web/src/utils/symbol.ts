/** 从交易对 symbol 推导 base 资产（如 BTCUSDT → BTC） */
export function baseFromSymbol(symbol: string): string {
  const compact = symbol.replace("/", "").toUpperCase();
  if (compact.endsWith("USDT")) return compact.slice(0, -4);
  if (compact.includes(":")) return compact.split(":")[0] ?? compact;
  return compact.slice(0, 3) || compact;
}

export function workerStatusTone(
  lastStatus: string | null | undefined,
  lastError: string | null | undefined,
): "ok" | "warn" | "error" {
  if (lastError) return "error";
  const s = (lastStatus ?? "").toLowerCase();
  if (s.includes("await") || s.includes("confirmation") || s.includes("pending")) return "warn";
  if (s.includes("reject") || s.includes("fail") || s.includes("error")) return "error";
  return "ok";
}
