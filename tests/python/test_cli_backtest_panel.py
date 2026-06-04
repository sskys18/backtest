"""P4 — CLI ``bt backtest-panel`` + panel report tests (TDD, written first).

Spec §9 / plan P4: a SEPARATE ``bt backtest-panel`` command wires
loader → strategy → run_panel → metrics → report; the existing ``bt backtest``
command must remain unchanged. The report module owns the orchestration entry
``run_panel_backtest`` (so the smoke test can assert a clean dict) plus a
console formatter.
"""

from __future__ import annotations

import json

import polars as pl
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
