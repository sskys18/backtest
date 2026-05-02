"""Tests for on-chain BTC source."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch


def test_onchain_supported_types():
    from bt.data.sources.onchain import OnchainSource
    assert OnchainSource().supported_types() == ["onchain"]


def test_onchain_joins_three_charts():
    from bt.data.sources.onchain import OnchainSource
    chart_responses = {
        "n-transactions": {"values": [
            {"x": 1704067200, "y": 350000.0},
            {"x": 1704153600, "y": 360000.0},
        ]},
        "hash-rate": {"values": [
            {"x": 1704067200, "y": 500_000_000.0},
            {"x": 1704153600, "y": 510_000_000.0},
        ]},
        "transaction-fees-usd": {"values": [
            {"x": 1704067200, "y": 100000.0},
            {"x": 1704153600, "y": 120000.0},
        ]},
    }

    async def fake_get(path, params=None):
        chart = path.rsplit("/", 1)[-1]
        return chart_responses[chart]

    with patch("bt.data.sources.onchain.HttpClient") as MockHttp:
        instance = MockHttp.return_value
        instance.get = fake_get
        instance.close = AsyncMock()
        src = OnchainSource()
        df = asyncio.run(src.fetch(
            pair="BTC", data_type="onchain", timeframe="1d",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
        ))
    assert set(df.columns) == {"timestamp", "tx_count", "hashrate", "fees_usd"}
    assert len(df) == 2
    row = df.sort("timestamp").row(0, named=True)
    assert row["tx_count"] == 350000
    assert row["hashrate"] == 500_000_000.0
    assert row["fees_usd"] == 100000.0
