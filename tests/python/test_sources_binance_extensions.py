"""Tests for new Binance fetch methods."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from bt.data.sources.binance import BinanceSource


def test_fetch_oi_hist_returns_canonical_schema():
    src = BinanceSource()
    fake_response = [
        {"symbol": "BTCUSDT", "sumOpenInterest": "10000.5",
         "sumOpenInterestValue": "500000000.0", "timestamp": 1700000000000},
        {"symbol": "BTCUSDT", "sumOpenInterest": "10100.0",
         "sumOpenInterestValue": "505000000.0", "timestamp": 1700000300000},
    ]
    with patch("bt.data.sources.binance.HttpClient") as MockHttp:
        instance = MockHttp.return_value
        instance.get = AsyncMock(return_value=fake_response)
        instance.close = AsyncMock()
        df = asyncio.run(src.fetch(
            pair="BTC-USDT", data_type="oi_hist", timeframe="5m",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        ))
    assert df.columns == ["timestamp", "open_interest", "open_interest_value"]
    assert df["open_interest"].to_list() == [10000.5, 10100.0]


def test_oi_hist_in_supported_types():
    assert "oi_hist" in BinanceSource().supported_types()


@pytest.mark.parametrize("dtype,endpoint", [
    ("lsr_top_pos", "/futures/data/topLongShortPositionRatio"),
    ("lsr_top_acct", "/futures/data/topLongShortAccountRatio"),
    ("lsr_global", "/futures/data/globalLongShortAccountRatio"),
])
def test_fetch_lsr_variants(dtype, endpoint):
    src = BinanceSource()
    fake = [
        {"symbol": "BTCUSDT", "longShortRatio": "1.5",
         "longAccount": "0.6", "shortAccount": "0.4", "timestamp": 1700000000000},
    ]
    captured_endpoint = {}

    async def fake_get(path, params=None):
        captured_endpoint["path"] = path
        return fake

    with patch("bt.data.sources.binance.HttpClient") as MockHttp:
        instance = MockHttp.return_value
        instance.get = fake_get
        instance.close = AsyncMock()
        df = asyncio.run(src.fetch(
            pair="BTC-USDT", data_type=dtype, timeframe="1h",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        ))
    assert captured_endpoint["path"] == endpoint
    assert df.columns == ["timestamp", "long_short_ratio", "long_account", "short_account"]
    assert df["long_short_ratio"].to_list() == [1.5]


def test_lsr_in_supported_types():
    types = BinanceSource().supported_types()
    assert "lsr_top_pos" in types
    assert "lsr_top_acct" in types
    assert "lsr_global" in types


def test_fetch_taker_ratio():
    src = BinanceSource()
    fake = [{
        "buySellRatio": "1.2", "buyVol": "120.0",
        "sellVol": "100.0", "timestamp": 1700000000000,
    }]
    with patch("bt.data.sources.binance.HttpClient") as MockHttp:
        instance = MockHttp.return_value
        instance.get = AsyncMock(return_value=fake)
        instance.close = AsyncMock()
        df = asyncio.run(src.fetch(
            pair="BTC-USDT", data_type="taker_ratio", timeframe="5m",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        ))
    assert df.columns == ["timestamp", "buy_sell_ratio", "buy_vol", "sell_vol"]
    assert df["buy_sell_ratio"].to_list() == [1.2]


def test_fetch_depth_snap_aggregates_book():
    src = BinanceSource()
    fake_book = {
        "lastUpdateId": 1,
        "E": 1700000000000,
        "T": 1700000000000,
        "bids": [["100.0", "1.0"], ["99.5", "2.0"], ["99.0", "3.0"]],
        "asks": [["101.0", "1.5"], ["101.5", "2.5"], ["102.0", "1.0"]],
    }
    with patch("bt.data.sources.binance.BinanceFuturesUssdtRestClient") as MockClient:
        instance = MockClient.return_value
        instance.get_order_book = AsyncMock(return_value=fake_book)
        with patch("bt.data.sources.binance.HttpClient") as MockHttp:
            MockHttp.return_value.close = AsyncMock()
            df = asyncio.run(src.fetch(
                pair="BTC-USDT", data_type="depth_snap", timeframe="1m",
                start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                end=datetime(2024, 1, 2, tzinfo=timezone.utc),
            ))
    assert len(df) == 1
    assert df["bid_total_qty"][0] == pytest.approx(6.0)
    assert df["ask_total_qty"][0] == pytest.approx(5.0)
    assert df["imbalance"][0] == pytest.approx((6.0 - 5.0) / (6.0 + 5.0))
    assert df["spread_bps"][0] == pytest.approx(((101.0 - 100.0) / 100.5) * 10_000)
