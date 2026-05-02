"""Tests for scanner specs and runner."""

import polars as pl


def test_scanner_base_protocol():
    from bt.scanners.base import ScannerSpec

    assert hasattr(ScannerSpec, "compute")
    assert hasattr(ScannerSpec, "required_columns")


def test_registry_lookup():
    from bt.scanners.registry import SCANNER_REGISTRY, get_scanner

    assert isinstance(SCANNER_REGISTRY, dict)
    assert get_scanner("kimp-wave").name == "kimp_wave"


def test_params_for_accepts_hyphen_alias():
    from bt.scanners.run import params_for

    params = {"alpha-terminal": {"score_threshold": 70}}

    assert params_for(params, "alpha_terminal") == {"score_threshold": 70}


def test_runner_reports_missing_columns(tmp_path):
    from bt.scanners.base import ScannerSpec
    from bt.scanners.registry import SCANNER_REGISTRY
    from bt.scanners.run import run_scanner

    class FakeSpec:
        name = "fake"
        required_columns = ["nonexistent_col"]
        is_approximation = False

        def default_exit_rules(self):
            from bt.replay.simulator import ExitRules

            return ExitRules()

        def compute(self, df, params):
            return df.with_columns(pl.lit(0).alias("signal"))

        def entry_filter(self, params):
            return None

    fake: ScannerSpec = FakeSpec()
    SCANNER_REGISTRY["fake"] = fake
    feature_path = tmp_path / "features.parquet"
    pl.DataFrame({"timestamp": [1], "close": [1.0]}).write_parquet(feature_path)

    result = run_scanner("fake", str(feature_path), {}, str(tmp_path))

    assert result.missing_columns == ["nonexistent_col"]
    assert result.error is not None
    SCANNER_REGISTRY.pop("fake")


def test_kimp_wave_signals_above_threshold():
    from bt.scanners.kimp_wave import KimpWaveSpec

    spec = KimpWaveSpec()
    df = pl.DataFrame({
        "timestamp": [1, 2, 3, 4, 5],
        "kimp": [0.5, 1.0, 1.6, 2.0, 0.8],
        "volume": [100.0, 200.0, 500.0, 800.0, 150.0],
        "close": [50_000.0] * 5,
    })

    out = spec.compute(df, params={"kimp_threshold": 1.5, "vol_min": 100.0})

    assert out["signal"].to_list() == [0, 0, 1, 1, 0]


def test_liqpressuremap_3sigma_ignition():
    from bt.scanners.liqpressuremap import LiqPressureMapSpec

    spec = LiqPressureMapSpec()
    df = pl.DataFrame({
        "timestamp": list(range(5)),
        "liq_intensity": [100.0, 100.0, 100.0, 5000.0, 100.0],
        "liq_z": [0.0, 0.0, 0.0, 4.5, 0.0],
        "liq_imbalance": [0.1, 0.0, -0.1, 0.8, 0.2],
    })

    out = spec.compute(df, params={})

    assert out["signal"].to_list() == [0, 0, 0, 1, 0]


def test_alpha_hunter_stage_gate_signals():
    from bt.scanners.alpha_hunter import AlphaHunterSpec

    spec = AlphaHunterSpec()
    df = pl.DataFrame({
        "timestamp": list(range(5)),
        "oi_z": [0.0, 1.6, 1.7, 1.8, 0.5],
        "cvd": [0.0, 100.0, 200.0, 300.0, 50.0],
        "funding_rate": [0.0001, 0.001, 0.0015, 0.002, 0.0001],
        "stage": [0, 1, 2, 3, 0],
    })

    out = spec.compute(df, params={})

    assert out["signal"].to_list() == [0, 0, 1, 1, 0]


def test_alpha_terminal_score_threshold():
    from bt.scanners.alpha_terminal import AlphaTerminalSpec

    spec = AlphaTerminalSpec()
    df = pl.DataFrame({
        "timestamp": list(range(3)),
        "close": [100.0, 110.0, 120.0],
        "vwap": [99.0, 100.0, 105.0],
        "rs_btc": [1.0, 1.5, 2.0],
        "liq_z": [0.0, 2.0, 3.5],
        "oi_z": [0.0, 1.0, 2.5],
    })

    out = spec.compute(df, params={"score_threshold": 55})

    assert out["signal"][2] == 1


def test_nahonja_score_threshold():
    from bt.scanners.nahonja import NahonjaSpec

    spec = NahonjaSpec()
    df = pl.DataFrame({
        "timestamp": list(range(4)),
        "oi_z": [0.0, 0.1, 1.8, 2.4],
        "taker_ratio": [1.0, 1.1, 1.3, 1.5],
        "funding_rate": [0.0001, 0.0002, 0.0015, 0.002],
        "liq_z": [0.0, 1.0, 2.5, 3.0],
        "cvd": [100.0, 120.0, 110.0, 100.0],
        "rs_btc": [1.0, 1.0, 1.2, 1.3],
    })

    out = spec.compute(df, params={"score_threshold": 50})

    assert out["signal"][2] == 1


def test_squeeze_hunter_requires_all_layers():
    from bt.scanners.squeeze_hunter import SqueezeHunterSpec

    spec = SqueezeHunterSpec()
    df = pl.DataFrame({
        "timestamp": list(range(4)),
        "oi_velocity": [-1.0, 1.0, 2.0, 3.0],
        "cvd": [100.0, 100.0, 110.0, 130.0],
        "liq_z": [0.0, 1.0, 2.0, 2.5],
        "toptrader_lsr": [1.0, 1.2, 1.6, 1.8],
    })

    out = spec.compute(df, params={})

    assert out["signal"].to_list() == [0, 0, 1, 1]


def test_kimchi_audit_picks_filter():
    from bt.scanners.kimchi_audit import KimchiAuditSpec

    spec = KimchiAuditSpec()
    df = pl.DataFrame({
        "pair": ["BTC-KRW", "ETH-KRW", "DOGE-KRW", "SHIB-KRW"],
        "score": [70.0, 50.0, 60.0, 40.0],
        "bithumb_concentration": [0.6, 0.4, 0.7, 0.3],
        "whale_pct": [0.4, 0.2, 0.5, 0.6],
    })

    out = spec.compute(df, params={"score_min": 55, "concentration_min": 0.5})
    picked = out.filter(pl.col("signal") == 1)["pair"].to_list()

    assert picked == ["BTC-KRW", "DOGE-KRW"]
