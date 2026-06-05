"""P3 — walk-forward / OOS harness (spec §7).

Rolling / anchored train-test folds by date, purge + embargo (horizon-scaled),
per-fold tune-on-train → freeze → eval-on-test, aggregate **OOS only**.

The fold representation (:class:`Fold`) is the single source of truth for the
train/test windows; :mod:`bt.panel.bias` consumes a ``Fold`` for its
fold-locality + embargo checks (do not re-derive windows there).

Tuning reuses the existing Optuna TPE optimizer (``bt.optimize.bayesian``); this
module owns only the split mechanics and the OOS aggregation, never the search.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from bt.optimize.bayesian import BayesianOptimizer
from bt.panel.loader import Panel
from bt.panel.metrics import panel_metrics
from bt.panel.run import _run_weights
from bt.panel.strategy import PanelView


@dataclass
class Fold:
    """One walk-forward fold over row indices into the panel's time axis.

    ``train_idx`` / ``test_idx`` are row indices (into ``panel.timestamp``);
    after purge+embargo they are disjoint with a gap. ``scheme`` is ``rolling``
    or ``anchored``; ``index`` is the 0-based fold position.
    """

    train_idx: np.ndarray
    test_idx: np.ndarray
    scheme: str
    index: int


def make_folds(
    timestamps: np.ndarray,
    *,
    scheme: str = "rolling",
    n_folds: int = 3,
    embargo_bars: int = 0,
    purge_bars: int = 0,
) -> list[Fold]:
    """Build ``n_folds`` train/test folds over the time axis.

    The time axis is partitioned into ``n_folds`` consecutive equal **test**
    blocks (the first block is reserved as pure train so fold 0 has a non-empty
    train window). For each test block:

      * **anchored** — train always starts at index 0 and *grows* to the bar
        before the test block (expanding window).
      * **rolling** — train is a **fixed-length sliding window** ending just
        before the test block; its length is the first fold's train size, so old
        bars drop off the back as the window advances (de Prado sliding CV). This
        is genuinely distinct from anchored: rolling train size stays ~constant
        across folds while anchored's grows.

    Purge removes ``purge_bars`` train rows immediately *before* the test block
    (overlapping-label leakage). Embargo removes a *further* ``embargo_bars``
    train rows before the test block, widening the train→test gap so a
    horizon-``N`` strategy keeps an ``≥N``-bar separation between the last train
    bar and the first test bar. In a strict forward walk-forward all train rows
    precede the test block, so the only train bars adjacent to (and able to leak
    into) the test segment are the ones just before it — embargo therefore drops
    rows on the *purge side*, not after the test block. The combined gap is thus
    ``purge_bars + embargo_bars`` bars, and ``embargo_bars`` scales with holding
    period via the caller (spec §7 — not a fixed constant).
    """
    if scheme not in ("rolling", "anchored"):
        raise ValueError(f"unknown scheme: {scheme!r}")
    T = int(np.asarray(timestamps).size)
    if n_folds < 1:
        raise ValueError("n_folds must be >= 1")

    block_bounds = np.array_split(np.arange(T), n_folds + 1)
    test_blocks = [b for b in block_bounds[1:] if b.size > 0]

    # Combined train→test gap: purge (overlapping-label) + embargo (horizon).
    # In a forward walk-forward train always precedes the test block, so both
    # gaps are carved out of the bars immediately BEFORE the test block.
    gap = purge_bars + embargo_bars

    # rolling uses a fixed train length = the first fold's available train span
    # (bars before the first test block, after the full gap), so subsequent folds
    # slide rather than grow.
    roll_len = None
    if test_blocks:
        first_lo = int(test_blocks[0][0])
        roll_len = max(0, first_lo - gap)
        # Degenerate rolling window: the purge+embargo gap swallows the entire
        # first train span, so there are zero usable train bars. Silently falling
        # through to anchored would mislabel the scheme; raise instead.
        if scheme == "rolling" and roll_len == 0:
            raise ValueError(
                f"rolling scheme yields an empty fixed train window "
                f"(first test block at index {first_lo}, purge+embargo gap {gap}): "
                "reduce n_folds, purge_bars, or embargo_bars, or use 'anchored'"
            )

    folds: list[Fold] = []
    for i, te in enumerate(test_blocks):
        lo, hi = int(te[0]), int(te[-1])
        train_end_excl = lo - gap
        if scheme == "rolling" and roll_len:
            # fixed-length window ending at train_end_excl, sliding forward
            train_start = max(0, train_end_excl - roll_len)
        else:  # anchored: expanding window from index 0
            train_start = 0
        train = np.arange(train_start, max(train_start, train_end_excl))
        folds.append(Fold(train_idx=train, test_idx=te, scheme=scheme, index=i))
    return folds


def _slice_panel(panel: Panel, idx: np.ndarray) -> Panel:
    """Return a row-sliced copy of ``panel`` over ``idx`` (preserves columns)."""
    from dataclasses import replace

    idx = np.asarray(idx, dtype=np.int64)
    return replace(
        panel,
        timestamp=panel.timestamp[idx],
        open=panel.open[idx],
        high=panel.high[idx],
        low=panel.low[idx],
        close=panel.close[idx],
        volume=panel.volume[idx],
        funding=panel.funding[idx],
        fx=panel.fx[idx],
        session=panel.session[idx],
        sector=panel.sector[idx],
        active_mask=panel.active_mask[idx],
    )


def _score_segment(
    panel: Panel, strategy_fn, run_cfg, *, periods_per_year, session_aware
) -> dict:
    """Run a strategy over a (sub)panel and compute its metrics."""
    pv = PanelView(panel)
    wm = strategy_fn(pv)
    res = _run_weights(panel, wm.W, run_cfg)
    return panel_metrics(
        res,
        timestamps=panel.timestamp,
        close=panel.close,
        fx=panel.fx,
        assets=panel.assets,
        session_aware=session_aware,
        periods_per_year=periods_per_year,
    )


def _segment_returns(panel: Panel, strategy_fn, run_cfg) -> np.ndarray:
    """Per-bar simple returns ``r = Δequity / equity_{t-1}`` for a strategy over a
    (sub)panel. Returns are computed WITHIN the segment (the equity curve starts
    fresh at ``initial_collateral``), so concatenating across folds never injects
    a spurious fold-boundary return (each fold's curve is a separate run)."""
    pv = PanelView(panel)
    wm = strategy_fn(pv)
    res = _run_weights(panel, wm.W, run_cfg)
    eq = np.asarray(res["equity"], dtype=np.float64)
    if eq.size < 2:
        return np.empty(0, dtype=np.float64)
    return np.diff(eq) / eq[:-1]


def walk_forward(
    panel: Panel,
    *,
    strategy_factory,
    search_space: dict,
    run_cfg: dict,
    scheme: str = "rolling",
    n_folds: int = 3,
    embargo_bars: int = 0,
    purge_bars: int = 0,
    budget: int = 10,
    periods_per_year: float = 252.0,
    session_aware: bool = False,
    objective_metric: str = "sharpe",
    seed: int = 42,
) -> dict:
    """Per-fold tune-on-train → freeze → eval-on-test; aggregate OOS only.

    Args:
        strategy_factory: ``params -> (PanelView -> WeightMatrix)``. The factory
            instantiates a strategy from a tuned parameter dict.
        search_space: Optuna-style space (see ``bt.optimize.bayesian``).
        run_cfg: ``run_panel`` portfolio config.
        budget: Optuna trial budget per fold.
        objective_metric: metric key (from :func:`panel_metrics`) to maximize on
            the train segment.

    Returns:
        ``{folds: [...], oos_sharpe: [...], oos_metrics_mean: {...}}`` — headline
        metrics are computed ONLY from each fold's test (OOS) segment; in-sample
        train metrics never enter the aggregate.
    """
    folds = make_folds(
        panel.timestamp, scheme=scheme, n_folds=n_folds,
        embargo_bars=embargo_bars, purge_bars=purge_bars,
    )
    opt = BayesianOptimizer(seed=seed)

    fold_results = []
    candidate_oos_returns: list[np.ndarray] = []  # per-fold frozen-candidate returns
    test_panels: list[Panel] = []                  # for the per-config OOS perf matrix
    first_history: list[dict] = []                 # one fold's tuning trial set
    for f in folds:
        train_panel = _slice_panel(panel, f.train_idx)
        test_panel = _slice_panel(panel, f.test_idx)
        test_panels.append(test_panel)

        def objective(params: dict, _tp=train_panel) -> float:
            fn = strategy_factory(params)
            try:
                m = _score_segment(_tp, fn, run_cfg,
                                   periods_per_year=periods_per_year,
                                   session_aware=session_aware)
            except Exception:
                return -1e18
            v = m.get(objective_metric, 0.0)
            return float(v) if np.isfinite(v) else -1e18

        tuned = opt.optimize(objective, search_space, budget)
        best_params = tuned["best_config"]
        if not first_history:
            first_history = tuned.get("history", [])

        # FREEZE best params; evaluate on the OOS test segment.
        frozen_fn = strategy_factory(best_params)
        oos = _score_segment(test_panel, frozen_fn, run_cfg,
                            periods_per_year=periods_per_year,
                            session_aware=session_aware)
        candidate_oos_returns.append(
            _segment_returns(test_panel, frozen_fn, run_cfg)
        )

        fold_results.append({
            "index": f.index,
            "best_params": best_params,
            "train_sharpe": tuned["best_score"],
            "oos_metrics": oos,
        })

    oos_sharpe = [fr["oos_metrics"]["sharpe"] for fr in fold_results]
    keys = list(fold_results[0]["oos_metrics"].keys()) if fold_results else []
    oos_mean = {
        k: float(np.mean([fr["oos_metrics"][k] for fr in fold_results]))
        for k in keys
        if isinstance(fold_results[0]["oos_metrics"][k], (int, float))
    }

    # --- DSR/PBO inputs (FU1) -------------------------------------------------
    # (a) candidate's CONCATENATED per-bar OOS returns (per-fold returns joined,
    #     NOT equity curves — each fold runs fresh from initial_collateral).
    oos_returns = (
        np.concatenate(candidate_oos_returns)
        if candidate_oos_returns else np.empty(0, dtype=np.float64)
    )

    # (b) the multiple-testing trial set = the SEARCH cardinality (distinct
    #     configs explored), NOT folds x budget — pooling identical configs across
    #     folds would fabricate dispersion and a phantom overfit penalty. De-
    #     annualize the train Sharpes so SR0 matches the per-bar candidate Sharpe
    #     PSR/DSR consumes.
    sqrt_ppy = math.sqrt(periods_per_year) if periods_per_year > 0 else 1.0
    distinct: dict[tuple, float] = {}
    for entry in first_history:
        if entry.get("score") is None:
            continue
        key = tuple(sorted(entry["config"].items()))
        # keep the best (max) train score seen for a config
        s = float(entry["score"]) / sqrt_ppy
        if key not in distinct or s > distinct[key]:
            distinct[key] = s
    distinct_configs = [dict(k) for k in distinct]
    trial_sharpes = [distinct[k] for k in distinct]

    # (c) per-trial x per-bar OOS performance matrix for CSCV/PBO: one column per
    #     distinct config, re-run frozen across ALL test folds and concatenated
    #     (same fold order as the candidate so rows align).
    oos_perf_matrix = np.empty((oos_returns.size, 0), dtype=np.float64)
    if distinct_configs and oos_returns.size:
        cols = []
        for cfg_params in distinct_configs:
            fn = strategy_factory(cfg_params)
            per_fold = [_segment_returns(tp, fn, run_cfg) for tp in test_panels]
            cols.append(np.concatenate(per_fold))
        oos_perf_matrix = np.column_stack(cols)

    return {
        "folds": fold_results,
        "oos_sharpe": oos_sharpe,
        "oos_metrics_mean": oos_mean,
        "oos_returns": oos_returns,
        "trial_sharpes": trial_sharpes,
        "oos_perf_matrix": oos_perf_matrix,
    }
