from unittest.mock import AsyncMock, patch

import pytest

from agent.config import Settings
from agent.notify.telegram import notify_risk_reject


@pytest.mark.asyncio
async def test_notify_risk_reject_uses_all_channels():
    cfg = Settings(
        notify_on_risk_reject=True,
        telegram_bot_token="bot",
        telegram_chat_id="chat",
        smtp_host="smtp.test",
        smtp_user="user",
        smtp_password="pass",
        notify_email_to="a@test.com",
        notify_email_from="b@test.com",
    )
    with patch("agent.notify.telegram.get_settings", return_value=cfg):
        with patch("agent.notify.email.send_email", AsyncMock(return_value=True)) as email_mock:
            with patch("agent.notify.telegram.send_telegram", AsyncMock(return_value=True)) as tg_mock:
                result = await notify_risk_reject("test reason", {"action": "BUY"})
    assert result["telegram"] is True
    assert result["email"] is True
    tg_mock.assert_awaited_once()
    email_mock.assert_awaited_once()
