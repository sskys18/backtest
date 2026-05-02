"""Tests for derived feature modules."""

import polars as pl


def test_cvd_running_sum():
    from bt.features.cvd import compute_cvd

    df = pl.DataFrame({
        "timestamp": [1, 2, 3, 4],
        "buy_volume": [10.0, 20.0, 5.0, 15.0],
        "sell_volume": [5.0, 25.0, 10.0, 5.0],
    })

    out = compute_cvd(df, params={})

    assert out["cvd"].to_list() == [5.0, 0.0, -5.0, 5.0]


def test_cvd_handles_empty():
    from bt.features.cvd import compute_cvd

    df = pl.DataFrame(
        {"timestamp": [], "buy_volume": [], "sell_volume": []},
        schema={"timestamp": pl.Int64, "buy_volume": pl.Float64, "sell_volume": pl.Float64},
    )

    out = compute_cvd(df, params={})

    assert "cvd" in out.columns
    assert len(out) == 0


def test_kimp_percent():
    from bt.features.kimp import compute_kimp

    df = pl.DataFrame({
        "timestamp": [1, 2, 3],
        "upbit_close": [55_000_000.0, 55_500_000.0, 54_900_000.0],
        "binance_close": [40_000.0, 40_300.0, 39_800.0],
        "fx_rate": [1350.0, 1350.0, 1350.0],
    })

    out = compute_kimp(df, params={
        "upbit_col": "upbit_close",
        "binance_col": "binance_close",
        "fx_col": "fx_rate",
    })

    assert abs(out["kimp"][0] - ((55_000_000 / 1350 / 40_000 - 1) * 100)) < 1e-6


def test_oi_velocity_and_zscore():
    from bt.features.oi_velocity import compute_oi_velocity

    df = pl.DataFrame({
        "timestamp": list(range(10)),
        "open_interest": [100.0, 110.0, 115.0, 120.0, 125.0, 130.0, 132.0, 135.0, 200.0, 210.0],
    })

    out = compute_oi_velocity(df, params={"window": 5})

    assert "oi_velocity" in out.columns
    assert "oi_accel" in out.columns
    assert "oi_z" in out.columns
    assert out["oi_velocity"].drop_nulls().to_list()[0] == 10.0
    assert out["oi_z"].drop_nulls().max() > 1.0


def test_liq_cluster_handles_missing_columns():
    from bt.features.liq_cluster import compute_liq_cluster

    df = pl.DataFrame({"timestamp": [1, 2, 3], "close": [100.0, 101.0, 102.0]})

    out = compute_liq_cluster(df, params={})

    assert "liq_intensity" in out.columns
    assert (out["liq_intensity"] == 0.0).all()


def test_liq_cluster_computes_imbalance():
    from bt.features.liq_cluster import compute_liq_cluster

    df = pl.DataFrame({
        "timestamp": list(range(5)),
        "long_liq_usd": [100.0, 0.0, 50.0, 200.0, 0.0],
        "short_liq_usd": [0.0, 50.0, 50.0, 0.0, 100.0],
    })

    out = compute_liq_cluster(df, params={"window": 3})

    assert out["liq_intensity"].to_list() == [100.0, 50.0, 100.0, 200.0, 100.0]
    assert out["liq_imbalance"][0] == 1.0
    assert out["liq_imbalance"][1] == -1.0


def test_stage_gate_progression():
    from bt.features.stage_gate import compute_stage_gate

    df = pl.DataFrame({
        "timestamp": list(range(10)),
        "oi_z": [0.0, 0.0, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0, 2.1, 2.2],
        "cvd": [0, 100, 200, 300, 400, 500, 600, 700, 800, 900],
        "funding_rate": [0.0001, 0.0002, 0.0003, 0.0005, 0.0010, 0.0015, 0.0020, 0.0025, 0.0030, 0.0035],
    })

    out = compute_stage_gate(df, params={
        "oi_z_threshold": 1.5,
        "cvd_slope_min": 50.0,
        "funding_extreme": 0.001,
        "window_bars": 4,
    })

    assert "stage" in out.columns
    assert out["stage"].max() >= 2


def test_rs_outperforms_btc():
    from bt.features.rs import compute_rs

    df = pl.DataFrame({
        "timestamp": list(range(5)),
        "close": [100.0, 110.0, 120.0, 125.0, 130.0],
        "btc_close": [50_000.0, 50_500.0, 51_000.0, 51_500.0, 52_000.0],
    })

    out = compute_rs(df, params={"window": 4})

    assert out["rs_btc"].drop_nulls()[0] > 1.0
