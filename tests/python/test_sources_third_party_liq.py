"""Tests for 3rd-party liquidation adapters."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest


def test_coinalyze_supported_types():
    from bt.data.sources.coinalyze import CoinalyzeSource

    assert CoinalyzeSource().supported_types() == ["liquidations"]


def test_coinalyze_missing_key_raises_clear_error(monkeypatch):
    monkeypatch.delenv("COINALYZE_API_KEY", raising=False)

    from bt.data.sources.coinalyze import CoinalyzeSource

    with pytest.raises(RuntimeError, match="COINALYZE_API_KEY"):
        asyncio.run(CoinalyzeSource().fetch(
            pair="BTC-USDT",
            data_type="liquidations",
            timeframe="1h",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        ))


def test_coinalyze_parses_response(monkeypatch):
    monkeypatch.setenv("COINALYZE_API_KEY", "dummy")
    fake = [{"symbol": "BTCUSDT_PERP.A", "history": [
        {"t": 1700000000, "l": 50000.0, "s": 30000.0},
        {"t": 1700003600, "l": 100000.0, "s": 0.0},
    ]}]

    from bt.data.sources.coinalyze import CoinalyzeSource

    src = CoinalyzeSource()
    with patch.object(src, "_get", new=AsyncMock(return_value=fake)):
        df = asyncio.run(src.fetch(
            pair="BTC-USDT",
            data_type="liquidations",
            timeframe="1h",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        ))

    assert df.columns == ["timestamp", "long_liq_usd", "short_liq_usd"]
    assert df["long_liq_usd"].to_list() == [50000.0, 100000.0]


def test_coinglass_supported_types():
    from bt.data.sources.coinglass import CoinglassSource

    assert CoinglassSource().supported_types() == ["liquidations"]


def test_coinglass_missing_key_raises(monkeypatch):
    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)

    from bt.data.sources.coinglass import CoinglassSource

    with pytest.raises(RuntimeError, match="COINGLASS_API_KEY"):
        asyncio.run(CoinglassSource().fetch(
            pair="BTC-USDT",
            data_type="liquidations",
            timeframe="1h",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        ))
