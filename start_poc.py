#!/usr/bin/env python
"""Bianca PoC 一键启动：起服务 → 等健康 → 启动 Agent 循环 → 前台保持。

用法：
    python start_poc.py          # 正常启动（前台保持）
    python start_poc.py --once   # 启动、验证一次闭环后退出（不进入前台）

退出：Ctrl+C 会停止服务并（尝试）停止 Agent runner。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)  # 确保 .env / data 相对路径正确

# Windows 控制台默认 GBK，强制 UTF-8 输出避免乱码/崩溃（字符集不支持时用替换符兜底）
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

HEALTH_TIMEOUT = 60  # 秒
HEALTH_INTERVAL = 2  # 秒


def load_env() -> dict[str, str]:
    """读取 .env（简易解析，覆盖 pydantic-settings 所需的键）。"""
    env: dict[str, str] = {}
    env_file = ROOT / ".env"
    if not env_file.exists():
        print("[start] [!!] 缺少 .env，请先复制 .env.example 并填入真实密钥")
        sys.exit(1)
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def check_prereq(env: dict[str, str]) -> None:
    problems: list[str] = []
    if not (env.get("BINANCE_API_KEY") and env.get("BINANCE_API_SECRET")):
        problems.append("BINANCE_API_KEY / BINANCE_API_SECRET 未配置")
    provider = env.get("LLM_PROVIDER", "deepseek")
    if provider == "ollama":
        if not env.get("LLM_BASE_URL"):
            problems.append("Ollama 模式需配置 LLM_BASE_URL")
    elif not env.get("LLM_API_KEY"):
        problems.append("LLM_API_KEY 未配置")
    if env.get("BINANCE_PROXY"):
        print(f"[start] 币安走代理: {env['BINANCE_PROXY']}")
    if problems:
        for p in problems:
            print(f"[start] [!!] {p}")
        sys.exit(1)


def http_json(method: str, url: str, *, timeout: float = 10.0) -> dict | None:
    req = urllib.request.Request(url, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError):
        return None


def wait_health(base: str) -> dict:
    deadline = time.time() + HEALTH_TIMEOUT
    while time.time() < deadline:
        health = http_json("GET", f"{base}/api/v1/health")
        if health is not None:
            return health
        time.sleep(HEALTH_INTERVAL)
    print(f"[start] [!!] 服务 {HEALTH_TIMEOUT}s 内未就绪，请检查日志")
    sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bianca PoC 一键启动")
    parser.add_argument("--host", default=os.environ.get("API_HOST", "127.0.0.1"))
    parser.add_argument("--port", default=int(os.environ.get("API_PORT", "8000")))
    parser.add_argument("--once", action="store_true", help="启动、验证一次闭环后退出")
    args = parser.parse_args()

    env = load_env()
    check_prereq(env)
    base = f"http://{args.host}:{args.port}"

    print(f"[start] 启动 API 服务  {base} ...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agent.main:app", "--host", args.host, "--port", str(args.port)],
        cwd=ROOT,
    )

    try:
        health = wait_health(base)
        bn = health.get("binance_demo")
        llm = health.get("llm")
        overall = health.get("status")
        print(f"[start] /health → overall={overall}  binance_demo={bn}  llm={llm}")
        if bn != "ok":
            print(f"[start] [!] 币安异常: {health.get('binance_detail')}")
        if llm != "ok":
            print(f"[start] [!] LLM 异常: {health.get('llm_detail')}")

        print("[start] 启动 Agent 循环 ...")
        resp = http_json("POST", f"{base}/api/v1/agent/start")
        if resp is None:
            print("[start] [!!] 启动 Agent 失败（服务未响应）")
            return 1
        print(f"[start] {resp.get('message', '')}")

        status = http_json("GET", f"{base}/api/v1/agent/status")
        if status:
            print("[start] Agent 状态:", json.dumps(status, ensure_ascii=False))
        print("[start] [OK] 就绪。Ctrl+C 停止。")

        if args.once:
            return 0
        proc.wait()
        return 0
    finally:
        if proc.poll() is None:
            print("[start] 停止服务 ...")
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
