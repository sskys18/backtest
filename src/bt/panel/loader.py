"""P0 — Panel loader: long-format parquet → aligned wide (T,N) arrays.

Import-only. No network, no fetch. Reads an already-imported long parquet
(one row per ``(timestamp, asset)``) and pivots to dense wide ``[T,N]`` NumPy
matrices keyed by sorted unique timestamp (rows) × sorted canonical assets
(cols). Missing cells become NaN and are flagged inactive in ``active_mask``.

See spec §3.1 (panel schema), §3.3 (sessions / calendar policy C9),
§3.4 (currency / numeraire).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

# Required long-format columns (fx_rate / currency / funding_rate are optional).
_REQUIRED_COLS = ("timestamp", "asset", "open", "high", "low", "close", "volume")
_OHLCV = ("open", "high", "low", "close", "volume")


class PanelSchemaError(Exception):
    """Raised when a panel parquet violates the schema / alignment contract."""


@dataclass
class Panel:
    """Aligned wide panel. Every matrix is ``[T,N]`` with identical column order.

    Columns map positionally to ``assets`` (the canonical sorted order);
    ``timestamp`` is the canonical sorted row order.
    """

    assets: list[str]
    timestamp: np.ndarray  # [T] int64 UTC ms
    open: np.ndarray       # [T,N] f64
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    funding: np.ndarray    # [T,N] f64
    fx: np.ndarray         # [T,N] f64
    session: np.ndarray    # [T,N] u8
    sector: np.ndarray     # [T,N] object (str or None)
    active_mask: np.ndarray  # [T,N] bool
    numeraire: str
    numeraire_is_unit_fx: bool


def load_panel(
    path: str,
    *,
    bar_freq: str,
    session_aware: bool,
    funding: bool = False,
    numeraire: str = "USD",
    calendar: str = "trading_days",
) -> Panel:
    """Load a long parquet into an aligned wide :class:`Panel`.

    Args:
        path: parquet file path (already imported; no fetch here).
        bar_freq: bar frequency string (e.g. ``"1d"``, ``"1m"``) — recorded,
            not parsed in P0.
        session_aware: intraday session semantics on/off.
        funding: True → perp-enabled panel; a missing ``funding_rate`` column is
            rejected (silent-zero would mask missing perp data). False → spot;
            missing column yields a zero funding matrix.
        numeraire: equity-curve currency (default ``USD``).
        calendar: ``"trading_days"`` (C9 trading-days-only) or
            ``"full_calendar"``. Mixed session policy is rejected.

    Raises:
        PanelSchemaError: missing required col; perp-enabled with no funding
            column; mixed currency without ``fx_rate``; calendar-policy mix.
    """
    df = pl.read_parquet(path)
    cols = set(df.columns)

    missing = [c for c in _REQUIRED_COLS if c not in cols]
    if missing:
        raise PanelSchemaError(f"missing required column(s): {missing}")

    has_funding_col = "funding_rate" in cols
    if funding and not has_funding_col:
        raise PanelSchemaError(
            "perp-enabled panel (funding=True) has no 'funding_rate' column; "
            "refusing to silently zero missing perp funding"
        )

    has_fx = "fx_rate" in cols
    if "currency" in cols:
        n_ccy = df.select(pl.col("currency").n_unique()).item()
        if n_ccy > 1 and not has_fx:
            raise PanelSchemaError(
                f"mixed-currency panel ({n_ccy} currencies) lacks an 'fx_rate' "
                f"column for numeraire={numeraire!r}"
            )

    # Calendar policy (C9): reject calendar/data mismatch in BOTH directions.
    # Only enforced on the daily path (session_aware=False); intraday panels
    # legitimately carry sessions {1,3,4,5}.
    if calendar not in {"trading_days", "full_calendar"}:
        raise PanelSchemaError(
            f"unknown calendar policy {calendar!r}; "
            f"expected 'trading_days' or 'full_calendar'"
        )
    if calendar == "full_calendar" and not session_aware and "session" not in cols:
        # full_calendar promises 0=closed non-trading rows; without a session
        # column that promise is unverifiable — reject rather than pass silently.
        raise PanelSchemaError(
            "full_calendar calendar requires a 'session' column with ≥1 row session==0 (closed)"
        )
    if "session" in cols and not session_aware:
        distinct = set(df.select(pl.col("session").unique()).to_series().to_list())
        if calendar == "trading_days":
            # trading-days-only panels must be entirely session==2.
            if distinct - {2}:
                raise PanelSchemaError(
                    f"trading_days calendar requires every row session==2; "
                    f"found sessions {sorted(distinct)} (mixed calendar policy)"
                )
        elif calendar == "full_calendar":
            # full-calendar panels must include non-trading rows as 0=closed;
            # an all-open panel is trading-days data mislabeled.
            if 0 not in distinct:
                raise PanelSchemaError(
                    f"full_calendar calendar requires ≥1 row session==0 (closed); "
                    f"found sessions {sorted(distinct)} (trading-days data mislabeled)"
                )

    # Canonical order: sorted unique timestamps (rows), sorted unique assets (cols).
    timestamps = sorted(df.select(pl.col("timestamp").unique()).to_series().to_list())
    assets = sorted(df.select(pl.col("asset").unique()).to_series().to_list())
    t_index = {ts: i for i, ts in enumerate(timestamps)}
    a_index = {a: j for j, a in enumerate(assets)}
    T, N = len(timestamps), len(assets)

    out: dict[str, np.ndarray] = {}
    for name in _OHLCV:
        out[name] = np.full((T, N), np.nan, dtype=np.float64)
    funding_arr = np.zeros((T, N), dtype=np.float64)
    fx_arr = np.ones((T, N), dtype=np.float64)
    session_arr = np.zeros((T, N), dtype=np.uint8)
    sector_arr = np.empty((T, N), dtype=object)
    active = np.zeros((T, N), dtype=bool)

    has_session = "session" in cols
    has_sector = "sector" in cols

    records = df.to_dicts()
    for r in records:
        i = t_index[r["timestamp"]]
        j = a_index[r["asset"]]
        active[i, j] = True
        for name in _OHLCV:
            out[name][i, j] = r[name]
        if has_funding_col:
            funding_arr[i, j] = r["funding_rate"] if r["funding_rate"] is not None else 0.0
        if has_fx:
            fx_arr[i, j] = r["fx_rate"] if r["fx_rate"] is not None else 1.0
        if has_session:
            session_arr[i, j] = r["session"] if r["session"] is not None else 0
        else:
            session_arr[i, j] = 2
        if has_sector:
            sector_arr[i, j] = r["sector"]

    return Panel(
        assets=assets,
        timestamp=np.asarray(timestamps, dtype=np.int64),
        open=out["open"],
        high=out["high"],
        low=out["low"],
        close=out["close"],
        volume=out["volume"],
        funding=funding_arr,
        fx=fx_arr,
        session=session_arr,
        sector=sector_arr,
        active_mask=active,
        numeraire=numeraire,
        numeraire_is_unit_fx=not has_fx,
    )
