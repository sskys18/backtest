"""End-to-end test for scan-backtest CLI."""

import json
import subprocess
import sys

import polars as pl


def test_scan_backtest_all_smoke(tmp_path):
    feature_path = tmp_path / "features.parquet"
    n = 100
    df = pl.DataFrame({
        "timestamp": list(range(n)),
        "close": [100.0 + i * 0.1 for i in range(n)],
        "volume": [1_000_000.0] * n,
        "vwap": [100.0] * n,
        "kimp": [1.0 + (i % 5) * 0.5 for i in range(n)],
        "rs_btc": [1.0 + (i % 3) * 0.2 for i in range(n)],
        "oi_z": [(i % 4) - 1.0 for i in range(n)],
        "oi_velocity": [(i % 3) - 1.0 for i in range(n)],
        "cvd": [float(i) for i in range(n)],
        "funding_rate": [0.0001 * ((i % 7) - 3) for i in range(n)],
        "liq_intensity": [100.0] * n,
        "liq_z": [0.0] * n,
        "liq_imbalance": [0.0] * n,
        "stage": [i // 25 for i in range(n)],
        "taker_ratio": [1.0 + (i % 3) * 0.1 for i in range(n)],
        "toptrader_lsr": [1.0 + (i % 5) * 0.1 for i in range(n)],
    })
    df.write_parquet(feature_path)

    out_dir = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "bt.cli",
            "scan-backtest",
            "--name",
            "all",
            "--features",
            str(feature_path),
            "--output",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    names = {row["name"] for row in payload}
    assert "kimp_wave" in names
    assert "alpha_hunter" in names
    kimchi = next(row for row in payload if row["name"] == "kimchi_audit")
    assert "pair" in kimchi["missing_columns"]
