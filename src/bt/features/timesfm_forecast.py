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
