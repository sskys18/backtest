"""P4 — CLI ``bt backtest-panel`` + panel report tests (TDD, written first).

Spec §9 / plan P4: a SEPARATE ``bt backtest-panel`` command wires
loader → strategy → run_panel → metrics → report; the existing ``bt backtest``
command must remain unchanged. The report module owns the orchestration entry
``run_panel_backtest`` (so the smoke test can assert a clean dict) plus a
console formatter.
"""

from __future__ import annotations

import json
import math

import polars as pl
import pytest
import yaml
from typer.testing import CliRunner

from bt.cli import app
from bt.panel.report import format_console, run_panel_backtest


def _write_long_panel(tmp_path) -> str:
    """Synthetic long-format panel: 4 assets x 14 daily bars, two sectors.

    Sized so the CS rotation produces nonzero weights: T >> lookback (3),
    N (=4) >= top_k+bottom_k (1+1). Each asset has a distinct linear drift so
    ranks are well-defined and equity is non-degenerate.
    """
    assets = ["AAA", "BBB", "CCC", "DDD"]
    sectors = {"AAA": "tech", "BBB": "tech", "CCC": "fin", "DDD": "fin"}
    drifts = {"AAA": 1.0, "BBB": 0.4, "CCC": -0.3, "DDD": -0.8}
    rows = []
    for i in range(14):
        ts = 1_000_000_000_000 + i * 86_400_000
        for a in assets:
            base = 100.0 + drifts[a] * i
            rows.append({
                "timestamp": ts,
                "asset": a,
                "open": base,
                "high": base + 1.0,
                "low": base - 1.0,
                "close": base + 0.5,
                "volume": 10.0,
                "session": 2,
                "sector": sectors[a],
                "funding_rate": 0.0,
            })
    p = tmp_path / "panel.parquet"
    pl.DataFrame(rows).write_parquet(p)
    return str(p)


def _write_config(tmp_path) -> str:
    cfg = {
        "panel": {
            "universe": ["AAA", "BBB", "CCC", "DDD"],
            "bar_freq": "1d",
            "session_aware": False,
            "numeraire": "USD",
            "funding": False,
            "calendar": "trading_days",
        },
        "strategy": {
            "kind": "cross_sectional",
            "params": {
                "lookback": 3,
                "top_k": 1,
                "bottom_k": 1,
                "weighting": "equal",
                "sector_neutral": False,
                "leverage_cap": 1.0,
            },
        },
        "portfolio": {
            "initial_collateral": 100000.0,
            "fee_rate": 0.0,
            "slippage_bps": 0.0,
            "im_rate": 0.2,
            "mm_rate": 0.1,
            "leverage_cap": 1.0,
            "max_net": 1.0,
            "max_per_asset": 1.0,
            "rebalance_freq": 1,
            "fill_model": "next_bar",
            "on_violation": "reject",
            "cap_kind": "hard",
            "numeraire_is_unit_fx": True,
        },
        "metrics": {"periods_per_year": 252},
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return str(p)


def test_cli_backtest_panel_smoke(tmp_path):
    """Synthetic panel + config → end-to-end run emits a report dict."""
    panel_path = _write_long_panel(tmp_path)
    config_path = _write_config(tmp_path)

    report = run_panel_backtest(panel_path, config_path)

    # report dict shape
    assert isinstance(report, dict)
    assert "metrics" in report
    assert "config" in report
    m = report["metrics"]
    # core metrics emitted by panel_metrics
    for key in ("sharpe", "max_dd", "turnover_total", "long_pnl", "short_pnl"):
        assert key in m
    # equity curve length matches the panel bar count (14)
    assert len(report["equity"]) == 14
    # single-pass run (no walk_forward section) → walk_forward block absent/None
    assert report.get("walk_forward") is None

    # console formatter returns a non-empty string mentioning a headline metric
    text = format_console(report)
    assert isinstance(text, str) and "sharpe" in text.lower()


def test_panel_backtest_walk_forward_branch(tmp_path):
    """A ``walk_forward`` config block triggers the OOS harness branch.

    Pins the strategy_factory contract (walk_forward calls ``fn(panel_view)``
    directly, so the factory must return the bound ``.compute`` callable, not the
    strategy instance) and the OOS-summary report shape. Needs a longer panel so
    each fold has enough bars for a Sharpe.
    """
    assets = ["AAA", "BBB", "CCC", "DDD"]
    sectors = {"AAA": "tech", "BBB": "tech", "CCC": "fin", "DDD": "fin"}
    drifts = {"AAA": 1.0, "BBB": 0.4, "CCC": -0.3, "DDD": -0.8}
    rows = []
    for i in range(40):
        ts = 1_000_000_000_000 + i * 86_400_000
        for a in assets:
            base = 100.0 + drifts[a] * i
            rows.append({
                "timestamp": ts, "asset": a, "open": base, "high": base + 1.0,
                "low": base - 1.0, "close": base + 0.5, "volume": 10.0,
                "session": 2, "sector": sectors[a], "funding_rate": 0.0,
            })
    panel_path = tmp_path / "panel_wf.parquet"
    pl.DataFrame(rows).write_parquet(panel_path)

    cfg = {
        "panel": {"universe": assets, "bar_freq": "1d", "session_aware": False,
                  "numeraire": "USD", "funding": False, "calendar": "trading_days"},
        "strategy": {"kind": "cross_sectional",
                     "params": {"lookback": 3, "top_k": 1, "bottom_k": 1,
                                "weighting": "equal", "sector_neutral": False,
                                "leverage_cap": 1.0}},
        "portfolio": {"initial_collateral": 100000.0, "fee_rate": 0.0,
                      "slippage_bps": 0.0, "im_rate": 0.2, "mm_rate": 0.1,
                      "leverage_cap": 1.0, "max_net": 1.0, "max_per_asset": 1.0,
                      "rebalance_freq": 1, "fill_model": "next_bar",
                      "on_violation": "reject", "cap_kind": "hard",
                      "numeraire_is_unit_fx": True},
        "metrics": {"periods_per_year": 252},
        "walk_forward": {"scheme": "rolling", "folds": 3, "embargo": 1,
                         "purge": 1, "budget": 1},
    }
    config_path = tmp_path / "config_wf.yaml"
    config_path.write_text(yaml.safe_dump(cfg))

    report = run_panel_backtest(str(panel_path), str(config_path))
    wf = report["walk_forward"]
    assert wf is not None
    assert wf["n_folds"] == 3
    assert len(wf["oos_sharpe"]) == 3
    assert "oos_metrics_mean" in wf
    assert wf["pbo"] is None                 # C=1 fixed config → no CSCV matrix
    # the deflation summary is NOT a DSR probability (raw Sharpes / benchmark)
    if wf["oos_deflation"] is not None:
        assert "best_oos_sharpe" in wf["oos_deflation"]


def _write_intraday_panel(tmp_path) -> str:
    """Synthetic intraday (session_aware) panel: 4 assets x 24 irregular-Δt bars.

    Δt alternates 6h/18h (overnight asymmetry, handoff's 27.7h-vs-6.0h motivation)
    so the time-neutral annualization constant ``A`` depends on the exposed-bar
    mean Δt — and therefore on ``hours_per_year``. Sessions are intraday-open
    (1=pre/regular) so the loader takes the session_aware path.
    """
    assets = ["AAA", "BBB", "CCC", "DDD"]
    sectors = {"AAA": "tech", "BBB": "tech", "CCC": "fin", "DDD": "fin"}
    drifts = {"AAA": 1.0, "BBB": 0.4, "CCC": -0.3, "DDD": -0.8}
    rows = []
    t_ms = 1_000_000_000_000
    for i in range(24):
        ts = t_ms
        t_ms += (6 if i % 2 == 0 else 18) * 3_600_000  # alternate 6h / 18h
        for a in assets:
            base = 100.0 + drifts[a] * i
            rows.append({
                "timestamp": ts, "asset": a, "open": base, "high": base + 1.0,
                "low": base - 1.0, "close": base + 0.5, "volume": 10.0,
                "session": 1, "sector": sectors[a], "funding_rate": 0.0,
            })
    p = tmp_path / "panel_intraday.parquet"
    pl.DataFrame(rows).write_parquet(p)
    return str(p)


def _intraday_cfg(hours_per_year):
    return {
        "panel": {"universe": ["AAA", "BBB", "CCC", "DDD"], "bar_freq": "1h",
                  "session_aware": True, "numeraire": "USD", "funding": False,
                  "calendar": "trading_days"},
        "strategy": {"kind": "cross_sectional",
                     "params": {"lookback": 3, "top_k": 1, "bottom_k": 1,
                                "weighting": "equal", "sector_neutral": False,
                                "leverage_cap": 1.0}},
        "portfolio": {"initial_collateral": 100000.0, "fee_rate": 0.0,
                      "slippage_bps": 0.0, "im_rate": 0.2, "mm_rate": 0.1,
                      "leverage_cap": 1.0, "max_net": 1.0, "max_per_asset": 1.0,
                      "rebalance_freq": 1, "fill_model": "next_bar",
                      "session_aware": True, "on_violation": "reject",
                      "cap_kind": "hard", "numeraire_is_unit_fx": True},
        "metrics": {"periods_per_year": 252, "hours_per_year": hours_per_year},
    }


def test_panel_backtest_hours_per_year_config_driven(tmp_path):
    """FU2 — time-neutral annualization constant A reflects config hours_per_year.

    Run the SAME intraday panel/strategy twice, changing ONLY
    ``metrics.hours_per_year`` (KR-cash intraday 6.5h x 252 ≈ 1638 vs the 8760
    perp default). The time-neutral Sharpe scales as ``sqrt(A) = sqrt(hpy/mean_h)``
    with the exposed-bar mean Δt identical across both runs, so the ratio of the
    two reported time-neutral Sharpes must equal ``sqrt(1638/8760)`` — proving
    report.py sources A from the config, not the hardcoded 8760.
    """
    panel_path = _write_intraday_panel(tmp_path)

    cfg_kr = tmp_path / "cfg_kr.yaml"
    cfg_kr.write_text(yaml.safe_dump(_intraday_cfg(1638.0)))
    cfg_perp = tmp_path / "cfg_perp.yaml"
    cfg_perp.write_text(yaml.safe_dump(_intraday_cfg(8760.0)))

    rep_kr = run_panel_backtest(panel_path, str(cfg_kr))
    rep_perp = run_panel_backtest(panel_path, str(cfg_perp))

    tn_kr = rep_kr["metrics"]["time_neutral_sharpe"]
    tn_perp = rep_perp["metrics"]["time_neutral_sharpe"]
    # both must be nonzero (strategy is exposed) and the default must NOT bleed in
    assert abs(tn_kr) > 1e-9 and abs(tn_perp) > 1e-9
    # A ∝ hours_per_year, Sharpe ∝ sqrt(A) → ratio = sqrt(1638/8760)
    assert tn_kr / tn_perp == pytest.approx(math.sqrt(1638.0 / 8760.0), rel=1e-9)


def test_panel_backtest_emits_real_dsr_and_honest_pbo(tmp_path, caplog):
    """FU1 — report.dsr is a real Deflated-Sharpe probability; pbo honest-None.

    The §9 config carries a single FIXED strategy (no search space) → C=1, so:
      * DSR must reduce to PSR(0) on the candidate's CONCATENATED per-bar OOS
        returns (one trial ⇒ zero benchmark dispersion ⇒ DSR == PSR(SR0=0)).
        We recompute the concat OOS returns INDEPENDENTLY (run the frozen config
        on each test fold, concat per-fold returns) and assert
        ``dsr == probabilistic_sharpe_ratio(concat, 0.0)`` — proving it is a real
        probability in [0,1], not a renamed raw Sharpe.
      * PBO must be honest ``None`` (CSCV needs >=2 configs) AND the report must
        LOG that PBO needs >=2 trials — no fabricated value.
    """
    import logging

    import numpy as np

    from bt.panel.loader import load_panel
    from bt.panel.run import _run_weights
    from bt.panel.report import build_run_cfg, build_strategy
    from bt.panel.strategy import PanelView
    from bt.panel.stats import probabilistic_sharpe_ratio
    from bt.panel.walkforward import _slice_panel, make_folds

    assets = ["AAA", "BBB", "CCC", "DDD"]
    sectors = {"AAA": "tech", "BBB": "tech", "CCC": "fin", "DDD": "fin"}
    drifts = {"AAA": 1.0, "BBB": 0.4, "CCC": -0.3, "DDD": -0.8}
    rows = []
    for i in range(40):
        ts = 1_000_000_000_000 + i * 86_400_000
        for a in assets:
            base = 100.0 + drifts[a] * i
            rows.append({
                "timestamp": ts, "asset": a, "open": base, "high": base + 1.0,
                "low": base - 1.0, "close": base + 0.5, "volume": 10.0,
                "session": 2, "sector": sectors[a], "funding_rate": 0.0,
            })
    panel_path = tmp_path / "panel_dsr.parquet"
    pl.DataFrame(rows).write_parquet(panel_path)

    cfg = {
        "panel": {"universe": assets, "bar_freq": "1d", "session_aware": False,
                  "numeraire": "USD", "funding": False, "calendar": "trading_days"},
        "strategy": {"kind": "cross_sectional",
                     "params": {"lookback": 3, "top_k": 1, "bottom_k": 1,
                                "weighting": "equal", "sector_neutral": False,
                                "leverage_cap": 1.0}},
        "portfolio": {"initial_collateral": 100000.0, "fee_rate": 0.0,
                      "slippage_bps": 0.0, "im_rate": 0.2, "mm_rate": 0.1,
                      "leverage_cap": 1.0, "max_net": 1.0, "max_per_asset": 1.0,
                      "rebalance_freq": 1, "fill_model": "next_bar",
                      "on_violation": "reject", "cap_kind": "hard",
                      "numeraire_is_unit_fx": True},
        "metrics": {"periods_per_year": 252},
        "walk_forward": {"scheme": "rolling", "folds": 3, "embargo": 1,
                         "purge": 1, "budget": 1},
    }
    config_path = tmp_path / "config_dsr.yaml"
    config_path.write_text(yaml.safe_dump(cfg))

    with caplog.at_level(logging.INFO):
        report = run_panel_backtest(str(panel_path), str(config_path))
    wf = report["walk_forward"]

    # dsr is a real probability
    assert "dsr" in wf
    assert wf["dsr"] is not None and 0.0 <= wf["dsr"] <= 1.0

    # independently recompute the concatenated per-bar OOS returns (frozen config
    # per fold, per-fold returns, concatenated — NOT equity-stitched).
    panel = load_panel(str(panel_path), bar_freq="1d", session_aware=False,
                       numeraire="USD", funding=False, calendar="trading_days")
    run_cfg = build_run_cfg(cfg)
    folds = make_folds(panel.timestamp, scheme="rolling", n_folds=3,
                       embargo_bars=1, purge_bars=1)
    concat = []
    for f in folds:
        tp = _slice_panel(panel, f.test_idx)
        fn = build_strategy(cfg["strategy"]).compute
        out = _run_weights(tp, fn(PanelView(tp)).W, run_cfg)
        eq = np.asarray(out["equity"], dtype=np.float64)
        concat.append(np.diff(eq) / eq[:-1])
    concat = np.concatenate(concat)
    expected_dsr = probabilistic_sharpe_ratio(concat, 0.0)  # C=1 ⇒ DSR == PSR(0)
    assert wf["dsr"] == pytest.approx(expected_dsr, abs=1e-9)

    # pbo is honest None for a single config, and the report logged why
    assert wf["pbo"] is None
    assert any("PBO" in r.message and "2" in r.message for r in caplog.records), \
        "report must log that PBO needs >=2 trials/configs"


def test_cli_backtest_panel_cli_smoke(tmp_path):
    """The actual ``bt backtest-panel`` subcommand runs and prints JSON."""
    panel_path = _write_long_panel(tmp_path)
    config_path = _write_config(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        app, ["backtest-panel", "--panel", panel_path, "--config", config_path]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "metrics" in payload and "sharpe" in payload["metrics"]


def test_cli_backtest_unchanged():
    """The existing ``bt backtest`` command still parses (regression guard)."""
    runner = CliRunner()
    # --help exits 0 and the command is still registered & documented.
    result = runner.invoke(app, ["backtest", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.output

    # the new command did not displace it: both are listed at top level.
    top = runner.invoke(app, ["--help"])
    assert top.exit_code == 0
    assert "backtest" in top.output
    assert "backtest-panel" in top.output
