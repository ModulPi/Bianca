"""阶段 C-5：薄 CLI。

它只做三件事：拼请求、打印 `text`、把 `goal_id` 留给下一条命令。
所以这里也只盯这三件 —— 一旦它开始"帮忙"（自己判断、自己改目标），
就有了第二条绕过端点的路径。
"""

from __future__ import annotations

import pytest

from agent.dialogue import cli


class _Response:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


def _reply(**overrides) -> dict:
    body = {
        "goal_id": "g-1",
        "status": "proposed",
        "text": "复述……",
        "changed": [],
        "unclear": [],
        "activated": False,
        "plan_id": None,
    }
    body.update(overrides)
    return body


@pytest.fixture
def calls(monkeypatch):
    recorded: list[tuple[str, str, dict | None]] = []

    def fake_request(method, url, *, json=None, timeout=None):  # noqa: A002
        recorded.append((method, url, json))
        return _Response(200, _reply())

    monkeypatch.setattr(cli.httpx, "request", fake_request)
    return recorded


def test_start_posts_the_sentence_and_prints_the_goal_id(calls, capsys):
    code = cli.main(["start", "做到 1100"])

    assert code == 0
    method, url, body = calls[0]
    assert (method, url) == ("POST", "http://127.0.0.1:8000/api/v1/dialogue/start")
    assert body == {"text": "做到 1100"}
    out = capsys.readouterr().out
    assert "复述……" in out
    assert "goal_id: g-1" in out


def test_say_targets_the_goal(calls):
    cli.main(["say", "g-1", "回撤放到 8%"])

    assert calls[0][1].endswith("/dialogue/g-1/say")
    assert calls[0][2] == {"text": "回撤放到 8%"}


def test_show_without_an_id_asks_for_current(calls):
    cli.main(["show"])

    assert calls[0] == ("GET", "http://127.0.0.1:8000/api/v1/dialogue/current", None)


def test_current_is_the_same_endpoint(calls):
    cli.main(["current"])

    assert calls[0][1].endswith("/dialogue/current")


def test_confirm_and_plan_send_no_body(calls):
    cli.main(["confirm", "g-1"])
    cli.main(["plan", "g-1"])

    assert calls[0][1].endswith("/dialogue/g-1/confirm")
    assert calls[1][1].endswith("/dialogue/g-1/plan")
    assert calls[0][2] is None and calls[1][2] is None


def test_a_plan_attempt_id_is_shown_but_not_confused_with_having_a_plan(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        cli.httpx,
        "request",
        lambda *a, **kw: _Response(200, _reply(status="active", plan_id="p-attempt")),
    )

    cli.main(["plan", "g-1"])

    out = capsys.readouterr().out
    assert "plans.attempt_id: p-attempt" in out


def test_http_errors_print_the_detail_and_exit_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.httpx,
        "request",
        lambda *a, **kw: _Response(409, {"detail": "还没有权益快照"}),
    )

    with pytest.raises(SystemExit) as exc:
        cli.main(["confirm", "g-1"])

    assert exc.value.code == 1
    assert "HTTP 409: 还没有权益快照" in capsys.readouterr().err


def test_a_dead_server_is_not_a_stack_trace(monkeypatch, capsys):
    def boom(*_args, **_kwargs):
        raise cli.httpx.ConnectError("connection refused")

    monkeypatch.setattr(cli.httpx, "request", boom)

    with pytest.raises(SystemExit) as exc:
        cli.main(["current"])

    assert exc.value.code == 1
    assert "连不上" in capsys.readouterr().err
