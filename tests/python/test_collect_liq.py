"""Tests for liquidations WS collector."""

import polars as pl


def test_aggregate_liquidation_messages():
    from bt.data.collect_liq import aggregate_messages

    base = (1700000010000 // 60_000) * 60_000
    msgs = [
        {
            "e": "forceOrder", "E": base + 10_000,
            "o": {"s": "BTCUSDT", "S": "SELL", "q": "0.5", "ap": "40000.0",
                  "T": base + 10_000},
        },
        {
            "e": "forceOrder", "E": base + 20_000,
            "o": {"s": "BTCUSDT", "S": "BUY", "q": "0.75", "ap": "40000.0",
                  "T": base + 20_000},
        },
        {
            "e": "forceOrder", "E": base + 50_000,
            "o": {"s": "BTCUSDT", "S": "SELL", "q": "0.25", "ap": "40000.0",
                  "T": base + 50_000},
        },
        {
            "e": "forceOrder", "E": base + 70_000,
            "o": {"s": "ETHUSDT", "S": "SELL", "q": "10", "ap": "2000.0",
                  "T": base + 70_000},
        },
    ]
    df = aggregate_messages(msgs)
    btc_min1 = df.filter(
        (pl.col("symbol") == "BTCUSDT") & (pl.col("timestamp") == base)
    ).row(0, named=True)
    assert btc_min1["long_liq_usd"] == 30000.0
    assert btc_min1["short_liq_usd"] == 30000.0
    eth_min2 = df.filter(pl.col("symbol") == "ETHUSDT").row(0, named=True)
    assert eth_min2["long_liq_usd"] == 20000.0


def test_partition_path_per_day(tmp_path):
    from bt.data.collect_liq import partition_path
    p = partition_path(tmp_path, "BTCUSDT", 1700000000000)
    assert p.parent.name == "1m"
    assert p.parent.parent.name == "liquidations"
    assert "BTC-USDT" in str(p) or "BTCUSDT" in str(p)
    assert p.name.endswith(".parquet")
