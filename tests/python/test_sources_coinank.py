"""Tests for CoinAnk liquidation source (no API key required)."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import polars as pl


def _mock_client(fake_payload):
    """Build httpx.AsyncClient mock returning fake_payload from .get(...)"""
    mock_resp = MagicMock()
    mock_resp.json = lambda: fake_payload
    mock_resp.raise_for_status = lambda: None
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_client)
    cm.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=cm)
    return factory


def test_coinank_supported_types():
    from bt.data.sources.coinank import CoinankSource
    assert CoinankSource().supported_types() == ["liquidations"]


def test_fetches_and_aggregates_all_exchanges():
    from bt.data.sources.coinank import CoinankSource
    fake = {"success": True, "data": [
        {"ts": 1700000000000,
         "details": {
             "Binance": {"longTurnover": 100.0, "shortTurnover": 50.0},
             "Bybit":   {"longTurnover": 30.0,  "shortTurnover": 20.0},
         },
         "all": {"longTurnover": 130.0, "shortTurnover": 70.0}},
        {"ts": 1700003600000,
         "details": {"Binance": {"longTurnover": 200.0, "shortTurnover": 100.0}},
         "all": {"longTurnover": 200.0, "shortTurnover": 100.0}},
    ]}
    with patch("bt.data.sources.coinank.httpx.AsyncClient", new=_mock_client(fake)):
        src = CoinankSource()
        df = asyncio.run(src.fetch(
            pair="BTC-USDT", data_type="liquidations", timeframe="4h",
            start=datetime(2023, 11, 14, tzinfo=timezone.utc),
            end=datetime(2023, 11, 16, tzinfo=timezone.utc),
        ))
    assert df.columns == ["timestamp", "long_liq_usd", "short_liq_usd"]
    assert df["long_liq_usd"].to_list() == [130.0, 200.0]
    assert df["short_liq_usd"].to_list() == [70.0, 100.0]


def test_filters_by_exchange_env(monkeypatch):
    from bt.data.sources.coinank import CoinankSource
    fake = {"success": True, "data": [
        {"ts": 1700000000000,
         "details": {
             "Binance": {"longTurnover": 100.0, "shortTurnover": 50.0},
             "Bybit":   {"longTurnover": 30.0,  "shortTurnover": 20.0},
         },
         "all": {"longTurnover": 130.0, "shortTurnover": 70.0}},
    ]}
    monkeypatch.setenv("COINANK_EXCHANGE", "Binance")
    with patch("bt.data.sources.coinank.httpx.AsyncClient", new=_mock_client(fake)):
        src = CoinankSource()
        df = asyncio.run(src.fetch(
            pair="BTC-USDT", data_type="liquidations", timeframe="4h",
            start=datetime(2023, 11, 14, tzinfo=timezone.utc),
            end=datetime(2023, 11, 16, tzinfo=timezone.utc),
        ))
    assert df["long_liq_usd"].to_list() == [100.0]
    assert df["short_liq_usd"].to_list() == [50.0]


def test_filters_by_time_range():
    from bt.data.sources.coinank import CoinankSource
    fake = {"success": True, "data": [
        {"ts": 1000, "details": {}, "all": {"longTurnover": 1.0, "shortTurnover": 1.0}},
        {"ts": 2000, "details": {}, "all": {"longTurnover": 2.0, "shortTurnover": 2.0}},
        {"ts": 3000, "details": {}, "all": {"longTurnover": 3.0, "shortTurnover": 3.0}},
    ]}
    with patch("bt.data.sources.coinank.httpx.AsyncClient", new=_mock_client(fake)):
        src = CoinankSource()
        df = asyncio.run(src.fetch(
            pair="BTC-USDT", data_type="liquidations", timeframe="1d",
            start=datetime.fromtimestamp(1.5, tz=timezone.utc),
            end=datetime.fromtimestamp(2.5, tz=timezone.utc),
        ))
    assert df["long_liq_usd"].to_list() == [2.0]
