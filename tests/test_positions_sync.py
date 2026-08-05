import pytest

from agent.positions.sync import sync_positions_from_balance


@pytest.mark.asyncio
async def test_sync_skipped_on_poc_schema():
    """PoC SQLite 栈不写 positions 表。"""
    count = await sync_positions_from_balance(
        balance_free={"USDT": 1000.0, "BTC": 0.01},
        symbol="BTCUSDT",
        last_price=65000.0,
    )
    assert count == 0
