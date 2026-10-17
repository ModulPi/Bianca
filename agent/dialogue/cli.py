"""对话层的薄 CLI（ADR-032）：只拼 HTTP 请求、打印 `text`。

**它不调用 `Dialogue`、不碰库。** 所有判断都在端点后面；CLI 一旦自己也能改
目标，就有了第二条绕过 /api/v1/dialogue 的路径，而那两套行为的差异迟早会
以"命令行里对、脚本里不对"的形式冒出来。

用法：

    python -m agent.dialogue.cli start "用 1000 USDT 做 BTCUSDT，两周内赚 10%，最多亏 5% 就停"
    python -m agent.dialogue.cli say <goal_id> "回撤放到 8%"
    python -m agent.dialogue.cli show [goal_id]      # 不给就是当前目标
    python -m agent.dialogue.cli confirm <goal_id>
    python -m agent.dialogue.cli plan <goal_id>

服务地址取 `BIANCA_API_BASE`，默认 `http://127.0.0.1:8000`。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

DEFAULT_BASE = "http://127.0.0.1:8000"


def _base() -> str:
    return os.environ.get("BIANCA_API_BASE", DEFAULT_BASE).rstrip("/")


def _request(method: str, path: str, *, body: dict | None = None) -> dict:
    url = f"{_base()}/api/v1/dialogue{path}"
    try:
        response = httpx.request(method, url, json=body, timeout=60.0)
    except httpx.HTTPError as exc:
        print(f"连不上 {url}：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    try:
        payload = response.json()
    except json.JSONDecodeError:
        payload = {"detail": response.text}

    if response.status_code >= 400:
        # 端点的 detail 是给人看的话；别吞掉它，也别把状态码藏起来。
        detail = payload.get("detail", payload)
        print(f"HTTP {response.status_code}: {detail}", file=sys.stderr)
        raise SystemExit(1)
    return payload


def _print_reply(payload: dict) -> None:
    print(payload["text"])
    print()
    print(f"goal_id: {payload['goal_id']}   status: {payload['status']}")
    if payload.get("plan_id"):
        # 这是**这一次尝试**的 plans 行，不等于目标已经有计划 —— 后看 status 没有用，
        # 要看 show 里那一行的 plan_id。
        print(f"plans.attempt_id: {payload['plan_id']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent.dialogue.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="开一个目标")
    p_start.add_argument("text")

    p_say = sub.add_parser("say", help="用户又说了一句（不会激活目标，ADR-037）")
    p_say.add_argument("goal_id")
    p_say.add_argument("text")

    p_show = sub.add_parser("show", help="重念当前目标，不读新话")
    p_show.add_argument("goal_id", nargs="?")

    p_confirm = sub.add_parser("confirm", help="确认（唯一能让目标生效的动作）")
    p_confirm.add_argument("goal_id")

    p_plan = sub.add_parser("plan", help="让经理做计划并落库")
    p_plan.add_argument("goal_id")

    sub.add_parser("current", help="当前未结束的目标")

    args = parser.parse_args(argv)

    if args.command == "start":
        payload = _request("POST", "/start", body={"text": args.text})
    elif args.command == "say":
        payload = _request(
            "POST", f"/{args.goal_id}/say", body={"text": args.text}
        )
    elif args.command == "confirm":
        payload = _request("POST", f"/{args.goal_id}/confirm")
    elif args.command == "plan":
        payload = _request("POST", f"/{args.goal_id}/plan")
    elif args.command == "show":
        payload = _request("GET", f"/{args.goal_id}" if args.goal_id else "/current")
    else:  # current
        payload = _request("GET", "/current")

    _print_reply(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
