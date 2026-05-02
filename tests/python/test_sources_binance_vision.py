"""Tests for Binance Vision public-data adapter."""

import asyncio
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import AsyncMock, patch
from zipfile import ZipFile


def _make_zip(csv: str) -> bytes:
    buf = BytesIO()
    with ZipFile(buf, "w") as archive:
        archive.writestr("data.csv", csv)
    return buf.getvalue()


def test_binance_vision_supported_types():
    from bt.data.sources.binance_vision import BinanceVisionSource

    types = BinanceVisionSource().supported_types()

    assert "metrics" in types
    assert "book_depth_hist" in types


def test_metrics_parses_csv_zip():
    csv = (
        "create_time,symbol,sum_open_interest,sum_open_interest_value,"
        "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
        "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
        "2024-01-01 00:00:00,BTCUSDT,10000.5,500000000.0,1.5,1.6,1.4,1.2\n"
        "2024-01-01 00:01:00,BTCUSDT,10010.0,505000000.0,1.55,1.65,1.45,1.21\n"
    )
    zip_bytes = _make_zip(csv)

    from bt.data.sources.binance_vision import BinanceVisionSource

    src = BinanceVisionSource()
    with patch.object(src, "_download_zip", new=AsyncMock(return_value=zip_bytes)):
        df = asyncio.run(src.fetch(
            pair="BTC-USDT",
            data_type="metrics",
            timeframe="1m",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        ))

    assert "timestamp" in df.columns
    assert "open_interest" in df.columns
    assert "taker_ratio" in df.columns
    assert len(df) == 2


def test_metrics_skips_missing_days():
    from bt.data.sources.binance_vision import BinanceVisionSource

    src = BinanceVisionSource()

    async def maybe_404(url, day):
        if day.day == 2:
            return None
        return _make_zip(
            "create_time,symbol,sum_open_interest,sum_open_interest_value,"
            "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
            "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
            f"2024-01-{day.day:02d} 00:00:00,BTCUSDT,1.0,1.0,1.0,1.0,1.0,1.0\n"
        )

    with patch.object(src, "_download_zip", new=AsyncMock(side_effect=maybe_404)):
        df = asyncio.run(src.fetch(
            pair="BTC-USDT",
            data_type="metrics",
            timeframe="1m",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 4, tzinfo=timezone.utc),
        ))

    assert len(df) == 3
