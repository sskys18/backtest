"""Tests for FX source."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import polars as pl
import pytest


def test_fx_supported_types():
    from bt.data.sources.fx import FxSource
    assert FxSource().supported_types() == ["fx"]


def test_fx_current_uses_exchangerate_api():
    from bt.data.sources.fx import FxSource
    fake_resp = {"rates": {"KRW": 1350.5}, "time_last_updated": 1700000000}
    with patch("bt.data.sources.fx.HttpClient") as MockHttp:
        instance = MockHttp.return_value
        instance.get = AsyncMock(return_value=fake_resp)
        instance.close = AsyncMock()
        src = FxSource()
        df = asyncio.run(src.fetch(
            pair="USD-KRW", data_type="fx", timeframe="1d",
            start=datetime(2026, 5, 2, tzinfo=timezone.utc),
            end=datetime(2026, 5, 2, tzinfo=timezone.utc),
        ))
    assert df.columns == ["timestamp", "pair", "rate"]
    assert df["rate"][0] == pytest.approx(1350.5)
    assert df["pair"][0] == "USD-KRW"


def test_fx_historical_uses_upbit_usdt_proxy():
    from bt.data.sources.fx import FxSource
    fake_upbit_df = pl.DataFrame({
        "timestamp": [1700000000000, 1700086400000],
        "open": [1340.0, 1345.0], "high": [1352.0, 1355.0],
        "low": [1338.0, 1342.0], "close": [1350.0, 1352.0],
        "volume": [10.0, 20.0],
    })
    with patch("bt.data.sources.fx.UpbitSource") as MockUpbit:
        instance = MockUpbit.return_value
        instance.fetch = AsyncMock(return_value=fake_upbit_df)
        src = FxSource()
        df = asyncio.run(src.fetch(
            pair="USD-KRW", data_type="fx", timeframe="1d",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
        ))
    assert df.columns == ["timestamp", "pair", "rate"]
    assert len(df) == 2
    assert df["rate"][0] == 1350.0
