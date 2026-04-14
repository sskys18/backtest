"""Feature builder — joins sources + computes indicators from config."""

from __future__ import annotations

from pathlib import Path

import yaml
import polars as pl

from bt.config import resolve_path
from bt.features.join import join_sources, load_source
from bt.features.registry import apply_indicator


def build_features(config_path: Path) -> dict:
    """Build a feature DataFrame from a features.yaml config.

    Returns a result dict with output path and column count.
    """
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    output_path = resolve_path(cfg["output"])

    # Load OHLCV backbone (first source with type: ohlcv)
    sources = cfg.get("sources", [])
    ohlcv_source = next((s for s in sources if s["type"] == "ohlcv"), None)
    if ohlcv_source is None:
        raise ValueError("features.yaml must have at least one source with type: ohlcv")

    backbone = load_source(resolve_path(ohlcv_source["path"]))

    # Join additional sources
    other_sources = [s for s in sources if s["type"] != "ohlcv"]
    resolved_sources = []
    for s in other_sources:
        resolved = dict(s)
        resolved["path"] = str(resolve_path(s["path"]))
        resolved_sources.append(resolved)

    if resolved_sources:
        df = join_sources(backbone, resolved_sources)
    else:
        df = backbone

    # Apply indicators
    for ind_cfg in cfg.get("indicators", []):
        name = ind_cfg["name"]
        input_col = ind_cfg.get("input", "close")
        params = ind_cfg.get("params", {})
        output_col = ind_cfg.get("column")
        output_cols = ind_cfg.get("columns")

        df = apply_indicator(df, name, input_col, params, output_col, output_cols)

    # Save
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(output_path)

    return {
        "status": "ok",
        "path": str(output_path),
        "rows": len(df),
        "columns": df.columns,
    }
