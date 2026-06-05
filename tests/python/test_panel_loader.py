"""P0 — Panel loader tests (long parquet → aligned wide [T,N] arrays).

TDD: written before implementation. Build long parquets via tmp_path + polars,
read back through ``load_panel``.
"""

from __future__ import annotations

import math

import numpy as np
import polars as pl
import pytest

from bt.panel import PanelSchemaError, load_panel


def _write(df: pl.DataFrame, tmp_path) -> str:
    p = tmp_path / "panel.parquet"
    df.write_parquet(p)
    return str(p)


def _base_rows(assets, timestamps, *, with_funding=True, with_fx=False, currency=None):
    """Build a dense long-format panel; caller can drop cells afterwards."""
    rows = []
    for ts in timestamps:
        for a in assets:
            row = {
                "timestamp": ts,
                "asset": a,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 10.0,
                "session": 2,
                "sector": "semis",
            }
            if with_funding:
                row["funding_rate"] = 0.0001
            if with_fx:
                row["fx_rate"] = 1.0
            if currency is not None:
                row["currency"] = currency[a]
            rows.append(row)
    return rows


def test_load_long_to_wide_shapes(tmp_path):
    """3 assets x 5 ts, one asset missing 1 ts → wide [5,3], missing cell NaN + mask False."""
    assets = ["A", "B", "C"]
    ts = [1000, 2000, 3000, 4000, 5000]
    rows = _base_rows(assets, ts)
    # Drop B at ts=3000
    rows = [r for r in rows if not (r["asset"] == "B" and r["timestamp"] == 3000)]
    path = _write(pl.DataFrame(rows), tmp_path)

    panel = load_panel(path, bar_freq="1d", session_aware=False, funding=True)

    assert panel.close.shape == (5, 3)
    assert panel.open.shape == (5, 3)
    assert list(panel.assets) == ["A", "B", "C"]  # sorted canonical
    # B is column index 1; ts=3000 is row index 2
    assert math.isnan(panel.close[2, 1])
    assert panel.active_mask[2, 1] == False  # noqa: E712
    assert panel.active_mask[2, 0] == True  # noqa: E712


def test_ohlcv_arrays_present(tmp_path):
    """Loaded Panel exposes open/high/low/close/volume all [T,N], values match source."""
    assets = ["A", "B"]
    ts = [1000, 2000]
    path = _write(pl.DataFrame(_base_rows(assets, ts)), tmp_path)
    panel = load_panel(path, bar_freq="1d", session_aware=False, funding=True)

    for name in ("open", "high", "low", "close", "volume"):
        arr = getattr(panel, name)
        assert arr.shape == (2, 2), name
    assert panel.high[0, 0] == 101.0
    assert panel.low[0, 0] == 99.0
    assert panel.volume[0, 0] == 10.0


def test_funding_absent_spot_zeros(tmp_path):
    """funding=False + no funding_rate col → funding array all 0, no error."""
    assets = ["A", "B"]
    ts = [1000, 2000]
    path = _write(pl.DataFrame(_base_rows(assets, ts, with_funding=False)), tmp_path)
    panel = load_panel(path, bar_freq="1d", session_aware=False, funding=False)
    assert panel.funding.shape == (2, 2)
    assert np.all(panel.funding == 0.0)


def test_funding_absent_perp_rejects(tmp_path):
    """funding=True + no funding_rate col → PanelSchemaError (missing perp funding)."""
    assets = ["A", "B"]
    ts = [1000, 2000]
    path = _write(pl.DataFrame(_base_rows(assets, ts, with_funding=False)), tmp_path)
    with pytest.raises(PanelSchemaError):
        load_panel(path, bar_freq="1d", session_aware=False, funding=True)


def test_missing_required_col_raises(tmp_path):
    """Drop close → PanelSchemaError."""
    assets = ["A", "B"]
    ts = [1000, 2000]
    df = pl.DataFrame(_base_rows(assets, ts)).drop("close")
    path = _write(df, tmp_path)
    with pytest.raises(PanelSchemaError):
        load_panel(path, bar_freq="1d", session_aware=False, funding=True)


def test_mixed_currency_requires_fx(tmp_path):
    """Two currencies, no fx_rate, numeraire set → raises."""
    assets = ["A", "B"]
    ts = [1000, 2000]
    rows = _base_rows(assets, ts, currency={"A": "USD", "B": "KRW"})
    path = _write(pl.DataFrame(rows), tmp_path)
    with pytest.raises(PanelSchemaError):
        load_panel(path, bar_freq="1d", session_aware=False, funding=True, numeraire="USD")


def test_calendar_policy_mix_rejected(tmp_path):
    """Rows mixing session 2 and 0 in trading_days mode → raises."""
    assets = ["A"]
    ts = [1000, 2000]
    rows = _base_rows(assets, ts)
    rows[1]["session"] = 0  # mix closed into a trading_days panel
    path = _write(pl.DataFrame(rows), tmp_path)
    with pytest.raises(PanelSchemaError):
        load_panel(
            path, bar_freq="1d", session_aware=False, funding=True, calendar="trading_days"
        )


def test_full_calendar_without_closed_rows_rejected(tmp_path):
    """calendar='full_calendar' but all rows session==2 (no 0=closed) → raises.

    A full-calendar panel must carry non-trading rows as 0=closed; an all-open
    panel is trading-days data mislabeled and must be rejected (C9, both directions).
    """
    assets = ["A"]
    ts = [1000, 2000]
    rows = _base_rows(assets, ts)  # every row session==2
    path = _write(pl.DataFrame(rows), tmp_path)
    with pytest.raises(PanelSchemaError):
        load_panel(
            path, bar_freq="1d", session_aware=False, funding=True, calendar="full_calendar"
        )


def test_full_calendar_with_closed_rows_accepted(tmp_path):
    """calendar='full_calendar' with at least one 0=closed row → loads."""
    assets = ["A"]
    ts = [1000, 2000, 3000]
    rows = _base_rows(assets, ts)
    rows[1]["session"] = 0  # a non-trading (closed) row present, as full-calendar requires
    path = _write(pl.DataFrame(rows), tmp_path)
    panel = load_panel(
        path, bar_freq="1d", session_aware=False, funding=True, calendar="full_calendar"
    )
    assert panel.session.shape == (3, 1)
    assert panel.session[1, 0] == 0
