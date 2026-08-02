#!/usr/bin/env python
"""等待 PoC 最小买卖闭环：至少 1 笔 BUY filled + 1 笔 SELL filled。

用法：
    python run_poc_closure.py
    python run_poc_closure.py --base http://127.0.0.1:8000 --timeout 900
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def http_json(method: str, url: str, *, timeout: float = 30.0) -> dict | list | None:
    req = urllib.request.Request(url, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        print(f"[closure] HTTP error: {exc}", file=sys.stderr)
        return None


def count_filled(trades: list[dict]) -> tuple[int, int]:
    buys = sum(1 for t in trades if t.get("side") == "BUY" and t.get("status") == "filled")
    sells = sum(1 for t in trades if t.get("side") == "SELL" and t.get("status") == "filled")
    return buys, sells


def main() -> int:
    parser = argparse.ArgumentParser(description="Wait for PoC BUY+SELL closure")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=int, default=900, help="Max wait seconds (default 15min)")
    parser.add_argument("--poll", type=int, default=15, help="Poll interval seconds")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    deadline = time.time() + args.timeout

    health = http_json("GET", f"{base}/api/v1/health")
    if not health:
        print("[closure] [!!] API 不可达，请先启动服务")
        return 1
    print(f"[closure] health: overall={health.get('status')} binance={health.get('binance_demo')} llm={health.get('llm')}")

    status = http_json("GET", f"{base}/api/v1/agent/status")
    if status and not status.get("running"):
        resp = http_json("POST", f"{base}/api/v1/agent/start")
        print(f"[closure] start agent: {resp}")

    while time.time() < deadline:
        status = http_json("GET", f"{base}/api/v1/agent/status")
        trades_resp = http_json("GET", f"{base}/api/v1/trades?limit=50")
        items = (trades_resp or {}).get("items") or []
        buys, sells = count_filled(items)
        tick = (status or {}).get("tick_count", "?")
        last = (status or {}).get("last_status", "?")
        print(f"[closure] ticks={tick} last={last} filled BUY={buys} SELL={sells}")

        if buys >= 1 and sells >= 1:
            print("[closure] [OK] 买卖闭环已完成！")
            for t in items[:10]:
                if t.get("status") == "filled" and t.get("side") in {"BUY", "SELL"}:
                    print(
                        f"  - {t.get('side')} qty={t.get('quantity')} price={t.get('price')} "
                        f"reason={t.get('decision_reason', '')[:60]}"
                    )
            return 0

        time.sleep(args.poll)

    print(f"[closure] [!!] {args.timeout}s 内未完成闭环，请检查 /api/v1/trades 与 agent 日志")
    return 1


if __name__ == "__main__":
    sys.exit(main())
