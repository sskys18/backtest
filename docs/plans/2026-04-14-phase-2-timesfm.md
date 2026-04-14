# Phase 2: TimesFM Forecasts as Feature Columns

**Spec:** `docs/specs/2026-04-13-backtest-system-design.md` section 12
**Scope:** TimesFM integration as a feature indicator + strategy that uses forecasts
**Depends on:** Phase 1 (engine + features + strategy framework)
**Testable output:** `bt backtest` with `timesfm_signal` strategy using forecast columns

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `pyproject.toml` | Modify | Add `timesfm` optional dependency |
| `src/bt/features/timesfm_forecast.py` | Create | TimesFM inference: prices → forecast columns |
| `src/bt/features/indicators.py` | Modify | Add `forecast` indicator entry |
| `src/bt/features/registry.py` | Modify | Register `forecast` indicator |
| `src/bt/strategy/timesfm_signal.py` | Create | Strategy that converts forecasts to signals |
| `src/bt/strategy/registry.py` | Modify | Register `timesfm_signal` strategy |
| `configs/strategies/timesfm_v1.yaml` | Create | Default config for TimesFM strategy |
| `tests/python/test_timesfm_strategy.py` | Create | Tests with mock forecasts |

---

## Task 1: Add timesfm dependency

File: `pyproject.toml` — add optional dependency group:

```toml
[project.optional-dependencies]
ml = ["timesfm>=1.0"]
```

Install: `uv pip install -e ".[ml]"`

**Verify:** `python -c "import timesfm; print(timesfm.__version__)"` prints version.

---

## Task 2: Implement TimesFM forecast module

File: `src/bt/features/timesfm_forecast.py`

```python
"""TimesFM forecast generation for feature DataFrames."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl


def generate_forecasts(
    df: pl.DataFrame,
    input_col: str = "close",
    context_length: int = 512,
    horizon: int = 24,
    model_path: str | None = None,
) -> pl.DataFrame:
    """Generate rolling TimesFM forecasts as feature columns.

    Produces columns:
    - forecast_mean: predicted price at horizon bars ahead
    - forecast_return: (forecast_mean - current_close) / current_close
    - forecast_direction: 1.0 if forecast_return > 0, else 0.0

    Uses rolling window: for each bar i, forecast from bars [i-context_length:i].
    First `context_length` bars have null forecasts (warmup).
    """
    try:
        import timesfm
    except ImportError:
        raise ImportError(
            "timesfm not installed. Install with: uv pip install -e '.[ml]'"
        )

    prices = df[input_col].to_numpy().astype(np.float64)
    n = len(prices)

    forecast_mean = np.full(n, np.nan)
    forecast_return = np.full(n, np.nan)
    forecast_direction = np.full(n, np.nan)

    # Load model once
    model = timesfm.TimesFm(
        hparams=timesfm.TimesFmHparams(
            per_core_batch_size=32,
            horizon_len=horizon,
            input_patch_len=32,
            output_patch_len=128,
        ),
        checkpoint=timesfm.TimesFmCheckpoint(
            huggingface_repo_id="google/timesfm-2.0-500m-pytorch",
        ),
    )

    # Batch all windows for efficiency
    contexts = []
    indices = []
    for i in range(context_length, n):
        ctx = prices[i - context_length : i]
        contexts.append(ctx)
        indices.append(i)

    if contexts:
        # TimesFM batch inference
        point_forecasts, _ = model.forecast(contexts)

        for idx, forecast in zip(indices, point_forecasts):
            # Use the mean of the horizon forecast
            fcast = float(np.mean(forecast[:horizon]))
            current = prices[idx - 1]  # last known price

            forecast_mean[idx] = fcast
            if current > 0:
                ret = (fcast - current) / current
                forecast_return[idx] = ret
                forecast_direction[idx] = 1.0 if ret > 0 else 0.0

    return df.with_columns([
        pl.Series("forecast_mean", forecast_mean),
        pl.Series("forecast_return", forecast_return),
        pl.Series("forecast_direction", forecast_direction),
    ])
```

**Verify:** Unit test in Task 5.

---

## Task 3: Register forecast as a feature indicator

File: `src/bt/features/indicators.py` — add at bottom:

```python
def forecast(
    df: pl.DataFrame, input_col: str, context_length: int, horizon: int,
    output_col: str | None = None,
) -> pl.DataFrame:
    """TimesFM forecast indicator — delegates to timesfm_forecast module."""
    from bt.features.timesfm_forecast import generate_forecasts
    return generate_forecasts(df, input_col, context_length, horizon)
```

File: `src/bt/features/registry.py` — add to INDICATOR_REGISTRY:

```python
"forecast": indicators.forecast,
```

And in `apply_indicator()`, add handler:

```python
elif name == "forecast":
    return fn(df, input_col=input_col, output_col=output_col, **params)
```

**Verify:** `from bt.features.registry import INDICATOR_REGISTRY; assert "forecast" in INDICATOR_REGISTRY`

---

## Task 4: Implement TimesFM signal strategy

File: `src/bt/strategy/timesfm_signal.py`

```python
"""TimesFM forecast-based strategy."""

from __future__ import annotations

import polars as pl


class TimesFMStrategy:
    """Uses TimesFM forecast columns to generate trading signals.

    Goes long when forecast_return > entry_threshold (model predicts price increase).
    Exits when forecast_return < exit_threshold.
    """

    def __init__(
        self,
        entry_threshold: float = 0.005,
        exit_threshold: float = 0.0,
        position_size: float = 0.15,
        forecast_col: str = "forecast_return",
    ):
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.position_size = position_size
        self.forecast_col = forecast_col

    def compute(self, df: pl.DataFrame) -> pl.DataFrame:
        if self.forecast_col not in df.columns:
            raise ValueError(
                f"Column '{self.forecast_col}' not found. "
                "Run bt features build with forecast indicator first."
            )

        forecast = df[self.forecast_col].fill_null(0.0).fill_nan(0.0)

        # Signal: 1.0 when forecast return > entry threshold
        strength = (forecast > self.entry_threshold).cast(pl.Float64).fill_null(0.0)

        return pl.DataFrame({
            "strength": strength,
            "target_size": pl.Series("target_size", [self.position_size] * len(df)),
        })
```

File: `src/bt/strategy/registry.py` — add:

```python
from bt.strategy.timesfm_signal import TimesFMStrategy
# In STRATEGY_REGISTRY:
"timesfm_signal": TimesFMStrategy,
```

**Verify:** `bt strategies` lists `timesfm_signal`.

---

## Task 5: Config + test

File: `configs/strategies/timesfm_v1.yaml`

```yaml
strategy:
  name: timesfm_signal
  params:
    entry_threshold: 0.005
    exit_threshold: 0.0
    position_size: 0.15
    forecast_col: forecast_return

backtest:
  pair: "BTC-USDT"
  timeframe: "1h"
  initial_capital: 10000.0
  data_path: "data/features/BTC-USDT-1h-forecast.parquet"

broker:
  slippage_bps: 5.0
  fee_rate: 0.001
  fill_model: "next_bar"

risk:
  max_position_size: 0.20
  max_drawdown_halt: 0.15
  signal_threshold: 0.5
```

File: `tests/python/test_timesfm_strategy.py`

```python
"""Test TimesFM strategy with mock forecast data."""

import polars as pl
import pytest

from bt.strategy.timesfm_signal import TimesFMStrategy


def _make_df_with_forecasts(n: int = 100) -> pl.DataFrame:
    """Create OHLCV + forecast columns with synthetic data."""
    return pl.DataFrame({
        "timestamp": list(range(n)),
        "open": [100.0 + i * 0.1 for i in range(n)],
        "high": [101.0 + i * 0.1 for i in range(n)],
        "low": [99.0 + i * 0.1 for i in range(n)],
        "close": [100.0 + i * 0.1 for i in range(n)],
        "volume": [1000.0] * n,
        "forecast_mean": [100.0 + (i + 24) * 0.1 for i in range(n)],
        "forecast_return": [0.024 if i % 3 != 0 else -0.01 for i in range(n)],
        "forecast_direction": [1.0 if i % 3 != 0 else 0.0 for i in range(n)],
    })


class TestTimesFMStrategy:
    def test_output_shape(self):
        df = _make_df_with_forecasts()
        strategy = TimesFMStrategy(entry_threshold=0.005)
        signals = strategy.compute(df)
        assert len(signals) == len(df)
        assert "strength" in signals.columns
        assert "target_size" in signals.columns

    def test_signals_follow_forecast(self):
        df = _make_df_with_forecasts()
        strategy = TimesFMStrategy(entry_threshold=0.005)
        signals = strategy.compute(df)
        # forecast_return=0.024 > 0.005 → strength=1.0 for 2/3 of bars
        strengths = signals["strength"].to_list()
        assert sum(s == 1.0 for s in strengths) > len(strengths) * 0.5

    def test_missing_column_raises(self):
        df = pl.DataFrame({"timestamp": [1, 2], "close": [100.0, 101.0]})
        strategy = TimesFMStrategy()
        with pytest.raises(ValueError, match="not found"):
            strategy.compute(df)

    def test_no_nulls_in_output(self):
        df = _make_df_with_forecasts()
        strategy = TimesFMStrategy()
        signals = strategy.compute(df)
        assert signals["strength"].null_count() == 0
        assert signals["target_size"].null_count() == 0
```

**Verify:** `uv run pytest tests/python/test_timesfm_strategy.py -v` — 4 tests pass.

---

## Task 6: Commit

```bash
git add -A && git commit -m "Phase 2: TimesFM forecast integration — feature indicator + signal strategy"
```
