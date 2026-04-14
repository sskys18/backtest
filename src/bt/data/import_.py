"""Import CSV/Parquet data into the data store."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from bt.config import find_project_root
from bt.data.store import normalize_and_save
from bt.data.validate import validate_dataframe


def import_file(
    path: Path,
    data_type: str,
    output: Path | None = None,
) -> dict:
    """Import a CSV or Parquet file into the data store.

    Returns a result dict with status and path.
    """
    if not path.exists():
        return {"status": "error", "message": f"File not found: {path}"}

    # Read the file
    if path.suffix == ".csv":
        df = pl.read_csv(path, try_parse_dates=True)
    elif path.suffix == ".parquet":
        df = pl.read_parquet(path)
    else:
        return {"status": "error", "message": f"Unsupported file format: {path.suffix}"}

    # Validate
    report = validate_dataframe(df, data_type)
    if not report["valid"]:
        return {"status": "error", "message": "Validation failed", "errors": report["errors"]}

    # Determine output path
    if output is None:
        root = find_project_root()
        if data_type == "records":
            output = root / "data" / "records" / path.with_suffix(".parquet").name
        else:
            output = root / "data" / "raw" / "imported" / data_type / path.with_suffix(".parquet").name

    saved = normalize_and_save(df, data_type, output)
    return {"status": "ok", "path": str(saved), "rows": len(df)}
