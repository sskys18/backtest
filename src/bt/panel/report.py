"""P4 — panel backtest orchestration + report presentation (spec §9).

Wires the existing layers together; adds no new engine logic:

    load_panel (P0) → strategy primitive (P2) → run_panel_strategy (P2)
    → panel_metrics (P2.5) → [optional] walk_forward + DSR (P3) → report dict

``run_panel_backtest(panel_path, config_path)`` is the single orchestration
entry the CLI calls; ``format_console(report)`` renders a console table. Both
are pure presentation/wiring — the metrics, OOS harness, and anti-overfit
statistics already exist in :mod:`bt.panel.metrics`, :mod:`bt.panel.walkforward`,
and :mod:`bt.panel.stats`.

Config schema is the committed P2 nested format (``panel`` / ``strategy`` /
``portfolio`` / ``metrics`` / optional ``walk_forward``), not the §9 flat
bullet list — working configs (``configs/panel/*.yaml``) are the spec.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

from bt.panel.loader import load_panel
from bt.panel.metrics import panel_metrics
from bt.panel.primitives import CrossSectionalStrategy, PairsStrategy
from bt.panel.run import run_panel_strategy

# run_cfg keys consumed by the Rust binding's cfg_from_dict (python.rs L249-331).
_RUN_CFG_KEYS = (
    "initial_collateral",
    "fee_rate",
    "slippage_bps",
    "im_rate",
    "mm_rate",
    "leverage_cap",
    "max_net",
    "max_per_asset",
    "rebalance_freq",
    "fill_model",
    "session_aware",
    "on_violation",
    "cap_kind",
    "cap_tolerance",
    "numeraire_is_unit_fx",
)


def _load_config(config_path: str | Path) -> dict:
    with open(config_path) as fh:
        return yaml.safe_load(fh)


def build_strategy(strategy_cfg: dict):
    """Construct a panel strategy primitive from the ``strategy`` config block.

    ``kind`` selects the primitive; ``params`` are passed verbatim as keyword
    arguments. Unknown kinds raise ``ValueError`` (no silent fallback).
    """
    kind = strategy_cfg["kind"]
    params = dict(strategy_cfg.get("params", {}))
    if kind == "cross_sectional":
        return CrossSectionalStrategy(**params)
    if kind == "pairs":
        return PairsStrategy(**params)
    raise ValueError(f"unknown strategy kind: {kind!r}")


def build_run_cfg(config: dict) -> dict:
    """Assemble the ``run_panel`` portfolio config dict from the YAML config.

    Pulls the ``portfolio`` block and overlays ``session_aware`` from ``panel``
    (the single source of truth for horizon mode) so the Rust engine's
    session-flat path matches the loader's tagging.
    """
    portfolio = dict(config.get("portfolio", {}))
    run_cfg = {k: portfolio[k] for k in _RUN_CFG_KEYS if k in portfolio}
    run_cfg["session_aware"] = bool(config.get("panel", {}).get("session_aware", False))
    return run_cfg


def run_panel_backtest(panel_path: str | Path, config_path: str | Path) -> dict:
    """Run a panel backtest end-to-end and return a report dict.

    Single-pass by default (load → strategy → run_panel → metrics). If the
    config carries a ``walk_forward`` block, additionally runs the OOS harness
    and attaches an OOS summary (per-fold sharpe, OOS-mean metrics, DSR over the
    per-fold sharpe trial set, PBO left ``None`` — the per-bar perf matrix CSCV
    needs is out of P4 presentation scope).

    Returns:
        ``{config, assets, equity, metrics, walk_forward}`` where ``metrics`` is
        the :func:`panel_metrics` block of the single full-sample pass and
        ``walk_forward`` is ``None`` unless a ``walk_forward`` block is present.
    """
    config = _load_config(config_path)
    panel_cfg = config.get("panel", {})
    metrics_cfg = config.get("metrics", {})

    session_aware = bool(panel_cfg.get("session_aware", False))
    numeraire = panel_cfg.get("numeraire", "USD")
    funding = bool(panel_cfg.get("funding", False))
    bar_freq = panel_cfg.get("bar_freq", "1d")
    calendar = panel_cfg.get("calendar", "trading_days")
    periods_per_year = float(metrics_cfg.get("periods_per_year", 252.0))

    panel = load_panel(
        str(panel_path),
        bar_freq=bar_freq,
        session_aware=session_aware,
        numeraire=numeraire,
        funding=funding,
        calendar=calendar,
    )

    strategy = build_strategy(config["strategy"])
    run_cfg = build_run_cfg(config)

    result = run_panel_strategy(panel, strategy, cfg=run_cfg)
    metrics = panel_metrics(
        result,
        timestamps=panel.timestamp,
        close=panel.close,
        fx=panel.fx,
        assets=panel.assets,
        session_aware=session_aware,
        periods_per_year=periods_per_year,
    )

    report: dict = {
        "config": config,
        "assets": list(panel.assets),
        "equity": [float(x) for x in result["equity"]],
        "metrics": metrics,
        "walk_forward": None,
    }

    wf_cfg = config.get("walk_forward")
    if wf_cfg:
        report["walk_forward"] = _walk_forward_summary(
            panel, config, run_cfg,
            periods_per_year=periods_per_year,
            session_aware=session_aware,
        )

    return report


def _walk_forward_summary(
    panel, config, run_cfg, *, periods_per_year, session_aware
) -> dict:
    """Run the P3 OOS harness on a fixed-param strategy and summarize OOS-only.

    The §9 config carries fixed ``strategy.params`` (no search space), so the
    walk-forward path runs a degenerate one-point tune (factory ignores tuned
    params, returns the configured strategy) — this exercises the genuine
    fold/purge/embargo + OOS-only aggregation without inventing a search space
    the config does not define. The OOS sharpe distribution is summarized with
    its expected-maximum-Sharpe deflation benchmark (the trial-dispersion term
    DSR uses); a full DSR needs the candidate's per-bar OOS return series, which
    the harness does not return, so it is left to a future phase. PBO is ``None``
    (its CSCV per-bar perf matrix is likewise not produced by the harness).
    """
    from bt.panel.stats import expected_max_sharpe
    from bt.panel.walkforward import walk_forward

    wf_cfg = config["walk_forward"]
    strategy_cfg = config["strategy"]

    def factory(_params: dict):
        # Fixed-param: tuning is a no-op. walk_forward calls the returned object
        # directly as ``fn(panel_view)``, so hand back the strategy's bound
        # ``compute`` (a PanelView -> WeightMatrix callable), not the instance.
        return build_strategy(strategy_cfg).compute

    wf = walk_forward(
        panel,
        strategy_factory=factory,
        search_space={},          # no tunable params in the §9 config
        run_cfg=run_cfg,
        scheme=wf_cfg.get("scheme", "rolling"),
        n_folds=int(wf_cfg.get("folds", 3)),
        embargo_bars=int(wf_cfg.get("embargo", 0)),
        purge_bars=int(wf_cfg.get("purge", 0)),
        budget=int(wf_cfg.get("budget", 1)),
        periods_per_year=periods_per_year,
        session_aware=session_aware,
    )

    oos_sharpe = wf["oos_sharpe"]
    # NOTE: this is the OOS-Sharpe deflation SUMMARY, NOT a Deflated Sharpe Ratio.
    # A true DSR is a probability in [0,1] and needs the candidate's per-bar OOS
    # return series + per-bar (de-annualized) trial sharpes — neither of which the
    # metrics-only harness returns. We surface the raw best OOS Sharpe and the
    # expected-maximum-Sharpe deflation benchmark (the trial-dispersion term DSR
    # would use) so the key name does not misrepresent a probability.
    oos_deflation = None
    if len(oos_sharpe) >= 2 and np.std(oos_sharpe) > 0:
        oos_deflation = {
            "best_oos_sharpe": float(max(oos_sharpe)),
            "expected_max_sharpe_benchmark": float(expected_max_sharpe(oos_sharpe)),
        }

    return {
        "scheme": wf_cfg.get("scheme", "rolling"),
        "n_folds": len(wf["folds"]),
        "oos_sharpe": [float(x) for x in oos_sharpe],
        "oos_metrics_mean": wf["oos_metrics_mean"],
        "oos_deflation": oos_deflation,
        "pbo": None,
    }


def format_console(report: dict) -> str:
    """Render a report dict as a plain-text console table."""
    m = report["metrics"]
    lines: list[str] = []
    lines.append("=" * 52)
    lines.append("  PANEL BACKTEST REPORT")
    lines.append("=" * 52)
    lines.append(f"  assets        : {', '.join(report['assets'])}")
    lines.append(f"  bars          : {len(report['equity'])}")
    lines.append("-" * 52)
    _row = "  {:<22}: {:>22}"
    lines.append(_row.format("Sharpe", _fmt(m.get("sharpe"))))
    lines.append(_row.format("Sortino", _fmt(m.get("sortino"))))
    lines.append(_row.format("Max drawdown", _fmt(m.get("max_dd"))))
    lines.append(_row.format("Total return", _fmt(m.get("total_return"))))
    lines.append(_row.format("Total PnL", _fmt(m.get("total_pnl"))))
    lines.append(_row.format("Long PnL", _fmt(m.get("long_pnl"))))
    lines.append(_row.format("Short PnL", _fmt(m.get("short_pnl"))))
    lines.append(_row.format("Hit rate", _fmt(m.get("hit_rate"))))
    lines.append(_row.format("Turnover (total)", _fmt(m.get("turnover_total"))))
    lines.append(_row.format("Funding PnL (total)", _fmt(m.get("funding_pnl_total"))))
    lines.append(_row.format("Gross exposure (mean)", _fmt(m.get("gross_exposure_mean"))))
    lines.append(_row.format("Net exposure (mean)", _fmt(m.get("net_exposure_mean"))))
    if "time_neutral_sharpe" in m:
        lines.append(_row.format("Time-neutral Sharpe", _fmt(m.get("time_neutral_sharpe"))))
        lines.append(_row.format("Exposure coverage", _fmt(m.get("exposure_coverage"))))

    wf = report.get("walk_forward")
    if wf:
        lines.append("-" * 52)
        lines.append(f"  WALK-FORWARD ({wf['scheme']}, {wf['n_folds']} folds, OOS-only)")
        sh = wf["oos_sharpe"]
        lines.append(_row.format("OOS Sharpe (mean)", _fmt(float(np.mean(sh)) if sh else None)))
        lines.append(_row.format("OOS Sharpe (per fold)",
                                 ", ".join(_fmt(x) for x in sh)))
        if wf.get("oos_deflation"):
            lines.append(_row.format("Best OOS Sharpe", _fmt(wf["oos_deflation"]["best_oos_sharpe"])))
            lines.append(_row.format("Expected-max benchmark",
                                     _fmt(wf["oos_deflation"]["expected_max_sharpe_benchmark"])))
        lines.append(_row.format("PBO", str(wf.get("pbo"))))
    lines.append("=" * 52)
    return "\n".join(lines)


def _fmt(x) -> str:
    if x is None:
        return "n/a"
    try:
        return f"{float(x):.6g}"
    except (TypeError, ValueError):
        return str(x)
