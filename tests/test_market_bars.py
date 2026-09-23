"""行情模块纯函数单测：解析、时间对齐、缺口检测、分片。无网络。"""

import pytest

from agent.market.bars import (
    chunk_ranges,
    count_bars,
    find_gaps,
    interval_ms,
    last_closed_bar_open,
    parse_rest_row,
    parse_ws_kline,
)

MIN = 60_000

# 实测捕获的真实收盘事件（设计文档 §6），未做裁剪
CLOSED_EVENT = {
    "e": "kline",
    "E": 1790163720000,
    "s": "BTCUSDT",
    "k": {
        "t": 1790163720000,
        "T": 1790163779999,
        "s": "BTCUSDT",
        "i": "1m",
        "f": 6706393771,
        "L": 6706395452,
        "o": "85497.29000000",
        "c": "85493.70000000",
        "h": "85497.29000000",
        "l": "85476.00000000",
        "v": "8.27187000",
        "n": 1682,
        "x": True,
        "q": "707101.04629110",
        "V": "5.83506000",
        "Q": "498789.85468940",
        "B": "0",
    },
}


def test_interval_ms():
    assert interval_ms("1m") == MIN
    assert interval_ms("1h") == 3_600_000
    with pytest.raises(ValueError):
        interval_ms("7s")


def test_parse_ws_kline_closed():
    row = parse_ws_kline(CLOSED_EVENT)
    assert row is not None
    assert row["time"] == 1790163720000
    assert row["symbol"] == "BTCUSDT"
    assert row["interval"] == "1m"
    assert row["open"] == 85497.29
    assert row["close"] == 85493.70
    assert row["volume"] == 8.27187
    assert row["quote_volume"] == 707101.0462911
    assert row["trades"] == 1682
    assert row["taker_buy_base"] == 5.83506
    assert row["taker_buy_quote"] == 498789.8546894


def test_parse_ws_kline_in_progress_is_dropped():
    """未完成 bar 必须丢弃 —— 实测每根 bar 内推送多次增量。"""
    event = {**CLOSED_EVENT, "k": {**CLOSED_EVENT["k"], "x": False}}
    assert parse_ws_kline(event) is None
    # 显式要求不过滤时仍可解析（供未完成 bar 的其它用途）
    assert parse_ws_kline(event, require_closed=False) is not None


def test_parse_ws_kline_combined_stream_shape():
    combined = {"stream": "btcusdt@kline_1m", "data": CLOSED_EVENT}
    row = parse_ws_kline(combined)
    assert row is not None
    assert row["time"] == CLOSED_EVENT["k"]["t"]


def test_parse_ws_kline_missing_optional_fields():
    """可选字段缺失不应导致整根 bar 丢弃。"""
    bare = {
        "e": "kline",
        "k": {"t": 1, "s": "BTCUSDT", "i": "1m", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10, "x": True},
    }
    row = parse_ws_kline(bare)
    assert row is not None
    assert row["quote_volume"] is None
    assert row["trades"] is None


def test_parse_ws_kline_rejects_garbage():
    assert parse_ws_kline({"e": "kline"}) is None
    assert parse_ws_kline({}) is None
    assert parse_ws_kline({"k": {"t": "not-a-number", "x": True}}) is None


def test_parse_rest_row():
    raw = [
        1502942400000, "4261.48000000", "4261.48000000", "4261.48000000", "4261.48000000",
        "1.77518300", 1502942459999, "7564.90685084", 3, "0.07518300", "320.39085084", "0",
    ]
    row = parse_rest_row(raw, "BTCUSDT", "1m")
    assert row is not None
    assert row["time"] == 1502942400000
    assert row["quote_volume"] == 7564.90685084
    assert row["trades"] == 3
    assert row["taker_buy_base"] == 0.075183


def test_parse_rest_row_short_and_garbage():
    assert parse_rest_row([], "BTCUSDT", "1m") is None
    assert parse_rest_row([1, 2], "BTCUSDT", "1m") is None
    assert parse_rest_row(["x"] * 12, "BTCUSDT", "1m") is None


def test_last_closed_bar_open():
    step = MIN
    # 正好落在边界上：当前这根刚开始，未完成
    assert last_closed_bar_open(1790163720000, "1m") == 1790163660000
    # 周期中途
    assert last_closed_bar_open(1790163720000 + 30_000, "1m") == 1790163660000
    # 距下一根仅差 1ms
    assert last_closed_bar_open(1790163780000 - 1, "1m") == 1790163660000
    assert last_closed_bar_open(1790163780000, "1m") == 1790163720000


def test_find_gaps_empty_store():
    assert find_gaps([], start_ms=0, end_ms=2 * MIN, interval="1m") == [(0, 2 * MIN)]


def test_find_gaps_contiguous():
    times = [0, MIN, 2 * MIN]
    assert find_gaps(times, start_ms=0, end_ms=2 * MIN, interval="1m") == []


def test_find_gaps_in_middle():
    times = [0, MIN, 4 * MIN]
    assert find_gaps(times, start_ms=0, end_ms=4 * MIN, interval="1m") == [(2 * MIN, 3 * MIN)]


def test_find_gaps_at_both_ends():
    times = [2 * MIN, 3 * MIN]
    gaps = find_gaps(times, start_ms=0, end_ms=5 * MIN, interval="1m")
    assert gaps == [(0, MIN), (4 * MIN, 5 * MIN)]


def test_find_gaps_deduplicates_and_sorts():
    times = [2 * MIN, 0, MIN, 2 * MIN]
    assert find_gaps(times, start_ms=0, end_ms=2 * MIN, interval="1m") == []


def test_count_bars():
    assert count_bars(0, 2 * MIN, "1m") == 3
    assert count_bars(0, 0, "1m") == 1
    assert count_bars(0, -1, "1m") == 0


def test_chunk_ranges_covers_whole_span_without_overlap():
    start, end = 0, 10 * MIN
    chunks = chunk_ranges(start, end, "1m", bars_per_chunk=4)
    # 每片 4 根，且首尾相接
    assert chunks[0][0] == start
    assert chunks[-1][1] == end
    for (_, prev_end), (next_start, _) in zip(chunks, chunks[1:]):
        assert next_start == prev_end + MIN
    assert count_bars(start, end, "1m") == sum(count_bars(a, b, "1m") for a, b in chunks)


def test_chunk_ranges_single_chunk_when_small():
    assert chunk_ranges(0, 3 * MIN, "1m", bars_per_chunk=1000) == [(0, 3 * MIN)]
    assert chunk_ranges(10 * MIN, 0, "1m") == []
