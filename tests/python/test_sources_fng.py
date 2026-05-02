"""Tests for Fear & Greed Index source."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch


def test_fng_supported_types():
    from bt.data.sources.fng import FngSource
    assert FngSource().supported_types() == ["fng"]


def test_fng_fetches_and_filters():
    from bt.data.sources.fng import FngSource
    fake = {"data": [
        {"value": "55", "value_classification": "Greed", "timestamp": "1704067200"},
        {"value": "30", "value_classification": "Fear", "timestamp": "1703980800"},
        {"value": "70", "value_classification": "Greed", "timestamp": "1700000000"},
    ]}
    with patch("bt.data.sources.fng.HttpClient") as MockHttp:
        instance = MockHttp.return_value
        instance.get = AsyncMock(return_value=fake)
        instance.close = AsyncMock()
        src = FngSource()
        df = asyncio.run(src.fetch(
            pair="BTC", data_type="fng", timeframe="1d",
            start=datetime(2023, 12, 31, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        ))
    assert df.columns == ["timestamp", "value", "classification"]
    assert len(df) == 2
    assert df.sort("timestamp")["value"].to_list() == [30, 55]
    assert df["timestamp"].dtype.is_integer()
    assert df["timestamp"][0] >= 1_700_000_000_000
