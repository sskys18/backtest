"""Scanner registry."""

from __future__ import annotations

from bt.scanners.base import ScannerSpec


SCANNER_REGISTRY: dict[str, ScannerSpec] = {}

_ALIASES = {
    "alpha-hunter": "alpha_hunter",
    "alpha-terminal": "alpha_terminal",
    "kimp-wave": "kimp_wave",
    "nahonja-scanner": "nahonja",
    "squeeze-hunter": "squeeze_hunter",
    "kimchi-audit-picks": "kimchi_audit",
}


def register(spec: ScannerSpec) -> None:
    """Register a scanner spec instance by canonical name."""
    SCANNER_REGISTRY[spec.name] = spec


def get_scanner(name: str) -> ScannerSpec:
    """Lookup by canonical name, hyphenated scanner name, or alias."""
    key = _ALIASES.get(name, name.replace("-", "_"))
    spec = SCANNER_REGISTRY.get(key)
    if spec is None:
        raise KeyError(f"Unknown scanner: {name}. Known: {list(SCANNER_REGISTRY.keys())}")
    return spec
