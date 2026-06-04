"""P2.5 — panel metrics core + mode-gated annualization (spec §8, C6/C10).

Pure-Python, derived entirely from the existing ``run_panel`` result dict plus
the panel arrays the Rust engine does not echo back:

  * ``timestamps[T]`` — bar epochs (ms); used for Δt in the intraday
    time-neutral Sharpe and exposure coverage.
  * ``close[T,N]`` + ``fx[T,N]`` — final per-asset mark and numeraire rate,
    for the unrealized leg of long/short attribution.

No Rust change and no edit to P1/P2 files: P2.5 is additive Python only.

Annualization is **mode-gated** (spec §8, §3.3):

  * Daily / mid-term (``session_aware=False``): standard calendar
    annualization, ``μ/σ · sqrt(periods_per_year)``. Flat bars count as
    zero-return bars normally.
  * Intraday (``session_aware=True``): additionally reports the
    **time-neutral Sharpe** — exposure-hour-weighted moments with flat bars
    excluded by ``h_t = 0`` (C10) — plus **exposure coverage** so the
    exclusion is visible.

Long/short attribution convention: reconstructed from the **recorded fill
prices** in ``result["trades"]`` (not from re-deriving fills off ``open``) using
the same single joint numeraire cost basis the Rust engine carries
(``cost_basis_num = qty·px·fx``), so with funding/fees/slippage = 0 the buckets
tie out exactly for any **finite** fx path:
``long_pnl + short_pnl == equity[-1] − equity[0]``. The unrealized leg guards a
NaN *close* only (it does not skip a NaN *fx*), matching the engine's
``compute_unrealized`` close-only guard. With nonzero costs the identity holds
up to the engine's fee/funding (reported separately).
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np

MS_PER_HOUR = 3_600_000.0


def panel_metrics(
    result: dict,
    *,
    timestamps: np.ndarray,
    close: np.ndarray,
    fx: np.ndarray,
    assets: Sequence[str],
    session_aware: bool,
    periods_per_year: float,
    hours_per_year: float = 8760.0,
    mar: float = 0.0,
) -> dict:
    """Compute portfolio metrics from a ``run_panel`` result dict.

    Args:
        result: the dict returned by ``bt.bt_engine.run_panel`` (``equity``,
            ``gross_exposure``, ``net_exposure``, ``funding_pnl``,
            ``turnover``, ``realized_pnl``, ``trades``).
        timestamps: ``[T]`` bar epochs in milliseconds (for intraday Δt).
        close: ``[T,N]`` per-asset close (final row → unrealized attribution).
        fx: ``[T,N]`` numeraire conversion rate.
        assets: canonical column order (positional, len N).
        session_aware: ``False`` → daily annualization; ``True`` → additionally
            report the intraday time-neutral Sharpe + exposure coverage.
        periods_per_year: calendar annualization base (252 KR-cash, 365 crypto).
        hours_per_year: time-neutral annualization base (8760 for 24h perp;
            set to trading-hours/year for KR-cash intraday).
        mar: minimum acceptable return for Sortino downside (per-bar).

    Returns:
        Metric dict (see keys built below).
    """
    equity = np.asarray(result["equity"], dtype=np.float64)
    gross = np.asarray(result["gross_exposure"], dtype=np.float64)
    net = np.asarray(result["net_exposure"], dtype=np.float64)
    funding = np.asarray(result["funding_pnl"], dtype=np.float64)
    turnover = np.asarray(result["turnover"], dtype=np.float64)
    trades = result["trades"]
    close = np.asarray(close, dtype=np.float64)
    fx = np.asarray(fx, dtype=np.float64)
    ts = np.asarray(timestamps, dtype=np.float64)

    if equity.size < 2:
        raise ValueError("panel_metrics needs >= 2 equity points")

    # --- simple per-bar returns r_t = Δequity / equity_{t-1} (spec §8) ------
    r = np.diff(equity) / equity[:-1]

    # --- daily / calendar Sharpe + Sortino ---------------------------------
    sharpe = _annualized_sharpe(r, periods_per_year)
    sortino = _annualized_sortino(r, periods_per_year, mar)

    # --- max drawdown (peak-to-trough fraction, signed <= 0) ---------------
    max_dd = _max_drawdown(equity)

    # --- exposure / activity ----------------------------------------------
    hit_rate = float((r > 0).mean()) if r.size else 0.0
    gross_mean = float(gross.mean())
    net_mean = float(net.mean())

    # --- long/short attribution (recorded-fill reconstruction) ------------
    long_pnl, short_pnl = _long_short_attribution(trades, close, fx, len(assets))

    out: dict = {
        "sharpe": sharpe,
        "sortino": sortino,
        "max_dd": max_dd,
        "turnover_total": float(turnover.sum()),
        "funding_pnl_total": float(funding.sum()),
        "hit_rate": hit_rate,
        "gross_exposure_mean": gross_mean,
        "net_exposure_mean": net_mean,
        "long_pnl": long_pnl,
        "short_pnl": short_pnl,
        "total_pnl": float(equity[-1] - equity[0]),
        "total_return": float(equity[-1] / equity[0] - 1.0),
    }

    # --- intraday time-neutral Sharpe + coverage (C10) --------------------
    if session_aware:
        tn = _time_neutral(r, gross, ts, hours_per_year)
        out.update(tn)

    return out


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _annualized_sharpe(r: np.ndarray, periods_per_year: float) -> float:
    if r.size < 2:
        return 0.0
    sd = r.std(ddof=1)
    if sd == 0.0:
        return 0.0
    return float(r.mean() / sd * math.sqrt(periods_per_year))


def _annualized_sortino(r: np.ndarray, periods_per_year: float, mar: float) -> float:
    if r.size < 2:
        return 0.0
    downside = np.minimum(r - mar, 0.0)
    # downside deviation: RMS of negative excess returns over ALL bars (ddof=0
    # convention for downside risk).
    dd = math.sqrt(float((downside ** 2).mean()))
    if dd == 0.0:
        return 0.0
    return float((r.mean() - mar) / dd * math.sqrt(periods_per_year))


def _max_drawdown(equity: np.ndarray) -> float:
    """Largest peak-to-trough drop as a signed fraction (<= 0)."""
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    return float(dd.min())


def _time_neutral(
    r: np.ndarray, gross: np.ndarray, ts: np.ndarray, hours_per_year: float
) -> dict:
    """Exposure-hour-weighted Sharpe + exposure coverage (spec §8).

    ``h_t = Δt_bar(hours) · 1[gross_t > 0]``. Flat bars (gross==0) get
    ``h_t = 0`` and drop out of the weighted moments (C10) but remain in the
    raw series. ``gross`` is sampled at the return bar ``t`` (i.e. ``gross[1:]``,
    the exposure carried into the bar whose return is ``r_t``).
    """
    dt_h = np.diff(ts) / MS_PER_HOUR             # [T-1] hours per return bar
    gross_at_bar = gross[1:]                      # align gross to return bar
    h = dt_h * (gross_at_bar > 0).astype(np.float64)

    sumh = h.sum()
    wall = dt_h.sum()
    coverage = float(sumh / wall) if wall > 0 else 0.0

    exposed = h > 0
    if not exposed.any() or sumh == 0.0:
        return {"time_neutral_sharpe": 0.0, "exposure_coverage": coverage}

    mu_h = float((r * h).sum() / sumh)
    var_h = float((h * (r - mu_h) ** 2).sum() / sumh)
    sig_h = math.sqrt(var_h)
    if sig_h == 0.0:
        return {"time_neutral_sharpe": 0.0, "exposure_coverage": coverage}

    a = hours_per_year / float(h[exposed].mean())
    tn_sharpe = mu_h / sig_h * math.sqrt(a)
    return {
        "time_neutral_sharpe": float(tn_sharpe),
        "exposure_coverage": coverage,
    }


def _long_short_attribution(
    trades: Sequence[dict], close: np.ndarray, fx: np.ndarray, n: int
) -> tuple[float, float]:
    """Split total PnL into long-leg and short-leg buckets in numeraire.

    Replays the recorded fill prices, carrying the **same single joint
    numeraire cost basis the Rust engine uses** (``apply_fill``,
    ``cost_basis_num = qty·px·fx``) rather than two separately-averaged
    components. This matters whenever ``fx`` varies across entry fills:
    ``(avg px)·(avg fx) ≠ avg(px·fx)``, so the prior two-average form mis-stated
    the basis off the fx≡1 region. Realized PnL on a reducing/flipping fill is
    attributed to the bucket matching the sign of the position **before** the
    reduction; the residual after a flip opens fresh. Remaining open position at
    window end contributes unrealized PnL at the final mark, bucketed by its
    current sign. Funding/fees are excluded (the engine reports them
    separately); with zero costs the two buckets tie to
    ``equity[-1] − equity[0]`` exactly for any finite fx path. The unrealized
    leg guards a NaN *close* only (it does not skip a NaN *fx*), matching the
    engine's ``compute_unrealized`` close-only guard (portfolio_engine.rs L785).
    """
    pos = np.zeros(n)         # signed qty (mirrors engine `pos`)
    cb = np.zeros(n)          # joint numeraire cost basis = qty·px·fx (engine `cost_basis_num`)
    long_pnl = 0.0
    short_pnl = 0.0

    for tr in trades:
        a = int(tr["asset"])
        dq = float(tr["qty"])      # signed fill qty
        px = float(tr["price"])
        f = float(tr["fx"])
        if dq == 0.0:
            continue
        cur = pos[a]
        target = cur + dq
        px_num = px * f            # per-unit numeraire price at fill

        if cur == 0.0:
            # fresh open: no realized PnL.
            cb[a] = target * px_num
            pos[a] = target
            continue

        same_dir = (cur > 0.0 and target >= 0.0) or (cur < 0.0 and target <= 0.0)
        cb_unit = cb[a] / cur      # per-unit joint basis (numeraire), signed-consistent

        if same_dir:
            if abs(target) >= abs(cur):
                # adding in same direction: extend basis by the added qty.
                add = target - cur     # same sign as cur
                cb[a] += add * px_num
            else:
                # reducing same direction: realize on the closed portion.
                closed = cur - target  # qty removed, same sign as cur
                realized = closed * (px_num - cb_unit)   # already correctly signed
                if cur > 0.0:
                    long_pnl += realized
                else:
                    short_pnl += realized
                cb[a] = target * cb_unit   # remaining basis at original unit cost
        else:
            # flip through zero: realize the ENTIRE old position, open residual.
            realized = cur * (px_num - cb_unit)
            if cur > 0.0:
                long_pnl += realized
            else:
                short_pnl += realized
            cb[a] = target * px_num
        pos[a] = target

    # unrealized on open positions at final mark: q·close·fx − cb (engine convention).
    final_close = close[-1]
    final_fx = fx[-1]
    for a in range(n):
        q = pos[a]
        if q == 0.0:
            continue
        # Mirror the engine's compute_unrealized (portfolio_engine.rs L784-789)
        # EXACTLY: it guards the CLOSE only (`if c.is_nan() { continue; }`) and
        # then computes `q·c·fxv − cost_basis_num`, propagating a NaN fx straight
        # into the unrealized leg. We must NOT additionally skip a NaN fx — doing
        # so would suppress a NaN the engine propagates, diverging equity[-1] from
        # the reconstructed buckets in exactly the fx-NaN case. Guard close only.
        if math.isnan(final_close[a]):
            continue
        u = q * final_close[a] * final_fx[a] - cb[a]
        if q > 0.0:
            long_pnl += u
        else:
            short_pnl += u

    return float(long_pnl), float(short_pnl)
