"""Scanner runner."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from bt.scanners.base import ScannerResult
from bt.scanners.registry import SCANNER_REGISTRY, get_scanner

_PARAM_ALIASES = {
    "alpha_hunter": "alpha-hunter",
    "alpha_terminal": "alpha-terminal",
    "kimp_wave": "kimp-wave",
    "nahonja": "nahonja-scanner",
    "squeeze_hunter": "squeeze-hunter",
    "kimchi_audit": "kimchi-audit-picks",
}


def run_scanner(
    name: str,
    feature_path: str,
    params: dict,
    output_dir: str,
) -> ScannerResult:
    """Load feature parquet, run one scanner, and write signal parquet."""
    spec = get_scanner(name)
    df = pl.read_parquet(feature_path)
    missing = [column for column in spec.required_columns if column not in df.columns]
    if missing:
        return ScannerResult(
            name=spec.name,
            is_approximation=spec.is_approximation,
            required_columns=spec.required_columns,
            missing_columns=missing,
            error=f"Missing required columns: {missing}",
        )

    try:
        out = spec.compute(df, params)
    except Exception as exc:  # pragma: no cover - defensive result surface
        return ScannerResult(
            name=spec.name,
            is_approximation=spec.is_approximation,
            required_columns=spec.required_columns,
            missing_columns=[],
            error=str(exc),
        )

    if "signal" not in out.columns:
        return ScannerResult(
            name=spec.name,
            is_approximation=spec.is_approximation,
            required_columns=spec.required_columns,
            missing_columns=[],
            error="Scanner compute() did not produce 'signal' column",
        )

    n_signals = int((out["signal"] > 0).sum())
    output_path = Path(output_dir) / f"{spec.name}_signals.parquet"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(output_path)

    return ScannerResult(
        name=spec.name,
        is_approximation=spec.is_approximation,
        required_columns=spec.required_columns,
        missing_columns=[],
        summary={
            "n_bars": len(out),
            "n_signals": n_signals,
            "signal_rate": n_signals / max(len(out), 1),
            "output_path": str(output_path),
        },
    )


def run_all(feature_path: str, params: dict, output_dir: str) -> list[ScannerResult]:
    """Run every registered scanner."""
    return [
        run_scanner(name, feature_path, params_for(params, name), output_dir)
        for name in SCANNER_REGISTRY
    ]


def params_for(params: dict, scanner_name: str) -> dict:
    """Return params by canonical scanner key or public hyphenated alias."""
    return params.get(scanner_name) or params.get(_PARAM_ALIASES.get(scanner_name, "")) or {}
