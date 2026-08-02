from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def demo_trading_mode():
    demo = AsyncMock(return_value="demo")
    with patch("agent.trading.mode.get_trading_mode", demo):
        with patch("agent.api.validation_routes.get_trading_mode", demo):
            yield
