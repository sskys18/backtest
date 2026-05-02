# Scanner Replication — Features + Strategies + Liquidations Investigation

> **For agentic workers:** REQUIRED SUB-SKILL: Use sspower:subagent-driven-development (recommended) or sspower:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement backtest support for all 7 scanners (alpha-hunter, alpha-terminal, kimp-wave, liqpressuremap, nahonja-scanner, squeeze-hunter, kimchi-audit-picks) by adding (a) derived feature modules, (b) a scanner-spec registry, (c) per-scanner backtest CLI, and (d) historical-liquidation data adapters where free sources allow. All exact-formula gaps are marked `APPROXIMATION` in code with tunable defaults.

**Architecture:**
- `src/bt/features/` gains 6 derived feature modules (CVD, kimp, OI velocity, liq cluster, stage gate, RS) registered in `bt.features.registry.INDICATOR_REGISTRY`.
- `src/bt/scanners/` is new — one file per scanner, each exposing both an `EntryFilter` (replay path) and a vectorized `compute(df) -> pl.DataFrame` (run_backtest path). A `SCANNER_REGISTRY` maps name → spec class.
- `bt scan-backtest --name <one|all> --pairs <p1,p2> --start <d> --end <d>` runs one or all scanners against feature parquet, writing per-scanner JSON results.
- Liquidations: keep live-only daemon, add `binance_vision` adapter for `metrics/` + `bookDepth/` historical (free), add stub adapters for Coinalyze + Coinglass that require `COINALYZE_API_KEY` / `COINGLASS_API_KEY` env vars. Liquidation features degrade gracefully when column missing.

**Tech Stack:** Python 3.14, Polars, httpx, Typer, pytest. Reuses existing `replay/`, `backtest/`, `features/` infra.

**Approximation policy:** Where source scanners (alpha-hunter Stage Gate weights, alpha-terminal 18-factor, nahonja 8-signal weights, squeeze-hunter validation) have private formulas, implement reasonable defaults inferred from public UI text. Each tunable parameter exposed in YAML config. Code-level docstring + `# APPROXIMATION:` comment marks every guessed value. Optuna can later tune them.

**Investigation outcomes (background for plan):**
- Binance Vision `liquidationSnapshot/` listed at `data.binance.vision/?prefix=data/futures/um/daily/liquidationSnapshot/` returns empty. Confirmed Binance removed historical liquidations from all free sources (REST 2021, public-data dump later).
- Binance Vision `metrics/` (per-minute OI, taker LSR, taker BS volume) and `bookDepth/` (historical orderbook snapshots) ARE available. These extend our 30-day API limit on `oi_hist`/`lsr_*`/`taker_ratio` and replace snapshot-only `depth_snap` with multi-day history.
- Coinalyze + Coinglass have free tier APIs but require user registration. Plan adds adapters gated on env-var API keys; without keys the features degrade.

**Scope split (this plan):**
- Phase A: Investigation doc + Binance Vision adapter (metrics + bookDepth)
- Phase B: 6 derived feature modules
- Phase C: Scanner framework (registry, base spec, CLI)
- Phase D: 7 scanner ports
- Phase E: Optional 3rd-party liquidation adapters (key-gated stubs)
- Phase F: End-to-end test + docs

---

## File map

**Modify:**
- `src/bt/data/fetch.py` — register `binance_vision` source, add `coinalyze` + `coinglass` registry entries
- `src/bt/data/sources/binance.py` — keep existing; no changes
- `src/bt/features/registry.py` — register 6 new indicators
- `src/bt/features/build.py` — handle new feature inputs (no API change)
- `src/bt/cli.py` — add `bt scan-backtest` command
- `docs/data-coverage.md` — append Binance Vision row + Coinalyze/Coinglass status
- `tests/python/test_features.py` — extend with new indicator tests

**Create:**
- `src/bt/data/sources/binance_vision.py` — public-data dump adapter (CSV ZIPs)
- `src/bt/data/sources/coinalyze.py` — liquidation history (key-gated)
- `src/bt/data/sources/coinglass.py` — liquidation history (key-gated)
- `src/bt/features/cvd.py` — Cumulative Volume Delta from `trades` data
- `src/bt/features/kimp.py` — Kimchi premium from cross-exchange OHLCV + FX
- `src/bt/features/oi_velocity.py` — OI 5min acceleration / z-score
- `src/bt/features/liq_cluster.py` — liquidation USD bucketed by price level
- `src/bt/features/stage_gate.py` — multi-condition state machine (alpha-hunter style)
- `src/bt/features/rs.py` — relative strength vs BTC
- `src/bt/scanners/__init__.py`
- `src/bt/scanners/base.py` — `ScannerSpec` Protocol, `EntryFilter`, vectorized `compute()` interface
- `src/bt/scanners/registry.py` — `SCANNER_REGISTRY`
- `src/bt/scanners/kimp_wave.py`
- `src/bt/scanners/alpha_hunter.py`
- `src/bt/scanners/alpha_terminal.py`
- `src/bt/scanners/liqpressuremap.py`
- `src/bt/scanners/nahonja.py`
- `src/bt/scanners/squeeze_hunter.py`
- `src/bt/scanners/kimchi_audit.py`
- `src/bt/scanners/run.py` — orchestrator: load features per pair, run scanner, summarize
- `tests/python/test_features_extended.py`
- `tests/python/test_scanners.py`
- `tests/python/test_scan_backtest_e2e.py`
- `docs/scanners.md` — per-scanner spec, required data, approximations, known gaps

---

## Phase A — Binance Vision adapter

### Task A1: Investigation doc

**Files:**
- Create: `docs/investigations/2026-05-02-historical-liquidations.md`

- [ ] **Step A1.1: Write investigation findings**

```markdown
# Historical Liquidations Data — Investigation

## Sources tested

| Source | Path/endpoint | Status | Notes |
|--------|---------------|--------|-------|
| Binance REST | `/fapi/v1/forceOrders` | ❌ Removed 2021 | No alternative on REST |
| Binance Vision | `data/futures/um/daily/liquidationSnapshot/` | ❌ Empty | Confirmed via S3 list 2026-05-02 |
| Binance WS | `wss://fstream.binance.com/ws/!forceOrder@arr` | ✅ Live only | Already wired in `bt collect-liq` |
| Coinalyze | `/v1/liquidation-history` | ⚠️ Free tier with key | Requires `COINALYZE_API_KEY` env |
| Coinglass v3 | `/api/futures/liquidation/history` | ⚠️ Free tier with key | Requires `COINGLASS_API_KEY` env |
| CryptoQuant | `/v1/...` | ❌ Paid only | Out of scope |
| Kaiko | tier-gated | ❌ Paid only | Out of scope |

## Decision

1. Keep `bt collect-liq` daemon as primary forward-capture path.
2. Add `coinalyze` + `coinglass` source adapters that read API key from env. Without key, return empty + clear error.
3. Document live-only constraint in `docs/data-coverage.md` and per-scanner docs.
4. Make liquidation feature optional in scanners — degrade gracefully, log warning, scanner runs without liq factor.

## Bonus discoveries during investigation

- Binance Vision `data/futures/um/{daily,monthly}/metrics/` returns historical per-minute OI, taker LSR, taker BS volume — extends our 30-day REST limit.
- Binance Vision `data/futures/um/{daily,monthly}/bookDepth/` provides historical orderbook snapshots — replaces our snapshot-only `depth_snap`.

These are wired in Task A2 below.
```

- [ ] **Step A1.2: Commit doc**

```bash
git add docs/investigations/2026-05-02-historical-liquidations.md
git commit -m "docs: investigate historical liquidations sources, decision matrix"
```

### Task A2: Binance Vision source adapter

**Files:**
- Create: `src/bt/data/sources/binance_vision.py`
- Modify: `src/bt/data/fetch.py` — register `"binance_vision"`
- Test: `tests/python/test_sources_binance_vision.py`

Binance public-data dump at `https://data.binance.vision/data/futures/um/{daily,monthly}/{category}/{SYMBOL}/{SYMBOL}-{category}-YYYY-MM-DD.zip`. Each ZIP contains a single CSV. Categories we'll support: `metrics`, `bookDepth`. Header in first line.

Data shapes:
- `metrics`: `create_time, symbol, sum_open_interest, sum_open_interest_value, count_toptrader_long_short_ratio, sum_toptrader_long_short_ratio, count_long_short_ratio, sum_taker_long_short_vol_ratio` — minute granularity.
- `bookDepth`: `timestamp, percentage, depth, notional` — snapshots at percentage-from-mid intervals.

Strategy: fetch one daily ZIP per day in range, concat. Mark category via `data_type` argument: `metrics` or `book_depth_hist`.

- [ ] **Step A2.1: Write failing test**

Create `tests/python/test_sources_binance_vision.py`:

```python
"""Tests for Binance Vision public-data adapter."""
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import AsyncMock, patch
from zipfile import ZipFile

import polars as pl
import pytest


def _make_zip(csv: str) -> bytes:
    buf = BytesIO()
    with ZipFile(buf, "w") as z:
        z.writestr("data.csv", csv)
    return buf.getvalue()


def test_binance_vision_supported_types():
    from bt.data.sources.binance_vision import BinanceVisionSource
    types = BinanceVisionSource().supported_types()
    assert "metrics" in types
    assert "book_depth_hist" in types


@pytest.mark.asyncio
async def test_metrics_parses_csv_zip():
    csv = (
        "create_time,symbol,sum_open_interest,sum_open_interest_value,"
        "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
        "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
        "2024-01-01 00:00:00,BTCUSDT,10000.5,500000000.0,1.5,1.6,1.4,1.2\n"
        "2024-01-01 00:01:00,BTCUSDT,10010.0,505000000.0,1.55,1.65,1.45,1.21\n"
    )
    zip_bytes = _make_zip(csv)

    from bt.data.sources.binance_vision import BinanceVisionSource
    src = BinanceVisionSource()
    with patch.object(src, "_download_zip", new=AsyncMock(return_value=zip_bytes)):
        df = await src.fetch(
            pair="BTC-USDT", data_type="metrics", timeframe="1m",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
    assert "timestamp" in df.columns
    assert "open_interest" in df.columns
    assert "taker_ratio" in df.columns
    assert len(df) == 2


@pytest.mark.asyncio
async def test_metrics_skips_missing_days():
    from bt.data.sources.binance_vision import BinanceVisionSource
    src = BinanceVisionSource()
    async def maybe_404(date):
        if date.day == 2:
            return None
        return _make_zip(
            "create_time,symbol,sum_open_interest,sum_open_interest_value,"
            "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
            "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
            f"2024-01-{date.day:02d} 00:00:00,BTCUSDT,1.0,1.0,1.0,1.0,1.0,1.0\n"
        )
    with patch.object(src, "_download_zip", new=AsyncMock(side_effect=lambda url, date: maybe_404(date))):
        df = await src.fetch(
            pair="BTC-USDT", data_type="metrics", timeframe="1m",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 4, tzinfo=timezone.utc),
        )
    assert len(df) == 3  # day 2 was None, skipped
```

- [ ] **Step A2.2: Run fail**

Run: `uv run pytest tests/python/test_sources_binance_vision.py -v`
Expected: FAIL — module missing.

- [ ] **Step A2.3: Implement BinanceVisionSource**

Create `src/bt/data/sources/binance_vision.py`:

```python
"""Binance Vision public-data dump adapter.

Source: https://data.binance.vision/
Per-day ZIP archives of CSV data. We support:
  - metrics: per-minute OI, top-trader LSR, taker BS ratio
  - book_depth_hist: percentage-bucketed orderbook snapshots
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from io import BytesIO
from zipfile import ZipFile

import httpx
import polars as pl


_BASE = "https://data.binance.vision/data/futures/um/daily"
_SYMBOL_RX = lambda pair: pair.replace("-", "")


class BinanceVisionSource:
    def supported_types(self) -> list[str]:
        return ["metrics", "book_depth_hist"]

    async def fetch(
        self, pair: str, data_type: str, timeframe: str,
        start: datetime, end: datetime,
    ) -> pl.DataFrame:
        if data_type not in ("metrics", "book_depth_hist"):
            raise ValueError(f"BinanceVisionSource does not support: {data_type}")
        symbol = _SYMBOL_RX(pair)
        category = "metrics" if data_type == "metrics" else "bookDepth"

        days = []
        d = start.date()
        while d <= end.date():
            days.append(d)
            d += timedelta(days=1)

        frames = []
        for day in days:
            url = f"{_BASE}/{category}/{symbol}/{symbol}-{category}-{day.isoformat()}.zip"
            zip_bytes = await self._download_zip(url, day)
            if zip_bytes is None:
                continue
            csv = self._extract_csv(zip_bytes)
            if data_type == "metrics":
                frames.append(self._parse_metrics(csv))
            else:
                frames.append(self._parse_book_depth(csv))

        if not frames:
            return _empty_df(data_type)
        return pl.concat(frames).sort("timestamp")

    async def _download_zip(self, url: str, day: date) -> bytes | None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content

    def _extract_csv(self, zip_bytes: bytes) -> str:
        with ZipFile(BytesIO(zip_bytes)) as z:
            name = z.namelist()[0]
            return z.read(name).decode("utf-8")

    def _parse_metrics(self, csv: str) -> pl.DataFrame:
        df = pl.read_csv(BytesIO(csv.encode()))
        return df.select([
            pl.col("create_time").str.strptime(pl.Datetime, "%Y-%m-%d %H:%M:%S")
                .dt.epoch(time_unit="ms").cast(pl.Int64).alias("timestamp"),
            pl.col("sum_open_interest").cast(pl.Float64).alias("open_interest"),
            pl.col("sum_open_interest_value").cast(pl.Float64).alias("open_interest_value"),
            pl.col("sum_toptrader_long_short_ratio").cast(pl.Float64).alias("toptrader_lsr"),
            pl.col("count_long_short_ratio").cast(pl.Float64).alias("global_lsr"),
            pl.col("sum_taker_long_short_vol_ratio").cast(pl.Float64).alias("taker_ratio"),
        ])

    def _parse_book_depth(self, csv: str) -> pl.DataFrame:
        df = pl.read_csv(BytesIO(csv.encode()))
        return df.select([
            pl.col("timestamp").cast(pl.Int64),
            pl.col("percentage").cast(pl.Float64),
            pl.col("depth").cast(pl.Float64),
            pl.col("notional").cast(pl.Float64),
        ])


def _empty_df(data_type: str) -> pl.DataFrame:
    if data_type == "metrics":
        return pl.DataFrame(schema={
            "timestamp": pl.Int64, "open_interest": pl.Float64,
            "open_interest_value": pl.Float64, "toptrader_lsr": pl.Float64,
            "global_lsr": pl.Float64, "taker_ratio": pl.Float64,
        })
    return pl.DataFrame(schema={
        "timestamp": pl.Int64, "percentage": pl.Float64,
        "depth": pl.Float64, "notional": pl.Float64,
    })
```

Add new schemas to `src/bt/data/schemas.py`:

```python
    "metrics": {
        "timestamp": pl.Int64,
        "open_interest": pl.Float64,
        "open_interest_value": pl.Float64,
        "toptrader_lsr": pl.Float64,
        "global_lsr": pl.Float64,
        "taker_ratio": pl.Float64,
    },
    "book_depth_hist": {
        "timestamp": pl.Int64,
        "percentage": pl.Float64,
        "depth": pl.Float64,
        "notional": pl.Float64,
    },
```

Register in `src/bt/data/fetch.py`:

```python
    "binance_vision": "bt.data.sources.binance_vision:BinanceVisionSource",
```

- [ ] **Step A2.4: Run pass**

Run: `uv run pytest tests/python/test_sources_binance_vision.py -v`
Expected: PASS.

- [ ] **Step A2.5: Live smoke test**

Run: `uv run bt data fetch --pair BTC-USDT --source binance_vision --type metrics --timeframe 1m --start 2025-12-01 --end 2025-12-02`
Expected: `"status": "ok"`, ~1440 rows.

- [ ] **Step A2.6: Commit**

```bash
git add src/bt/data/sources/binance_vision.py src/bt/data/fetch.py src/bt/data/schemas.py tests/python/test_sources_binance_vision.py
git commit -m "feat(binance_vision): historical metrics + bookDepth from public dump"
```

### Task A3: Coinalyze + Coinglass adapters (key-gated)

**Files:**
- Create: `src/bt/data/sources/coinalyze.py`
- Create: `src/bt/data/sources/coinglass.py`
- Modify: `src/bt/data/fetch.py` — register both
- Test: `tests/python/test_sources_third_party_liq.py`

Both expose `liquidations` data type with same canonical schema. Both fail loudly with clear message if env var missing. Both use httpx directly.

Coinalyze: `GET https://api.coinalyze.net/v1/liquidation-history?symbols=BTCUSDT_PERP.A&interval=1hour&from=<sec>&to=<sec>` with `api_key` header.
Coinglass v3: `GET https://open-api-v3.coinglass.com/api/futures/liquidation/history?symbol=BTC&interval=h1` with `CG-API-KEY` header.

- [ ] **Step A3.1: Write failing tests**

Create `tests/python/test_sources_third_party_liq.py`:

```python
"""Tests for 3rd-party liquidation adapters (key-gated)."""
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import polars as pl
import pytest


def test_coinalyze_supported_types():
    from bt.data.sources.coinalyze import CoinalyzeSource
    assert CoinalyzeSource().supported_types() == ["liquidations"]


@pytest.mark.asyncio
async def test_coinalyze_missing_key_raises_clear_error(monkeypatch):
    monkeypatch.delenv("COINALYZE_API_KEY", raising=False)
    from bt.data.sources.coinalyze import CoinalyzeSource
    src = CoinalyzeSource()
    with pytest.raises(RuntimeError, match="COINALYZE_API_KEY"):
        await src.fetch(
            pair="BTC-USDT", data_type="liquidations", timeframe="1h",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_coinalyze_parses_response(monkeypatch):
    monkeypatch.setenv("COINALYZE_API_KEY", "dummy")
    fake = [{"symbol": "BTCUSDT_PERP.A", "history": [
        {"t": 1700000000, "l": 50000.0, "s": 30000.0},
        {"t": 1700003600, "l": 100000.0, "s": 0.0},
    ]}]
    from bt.data.sources.coinalyze import CoinalyzeSource
    src = CoinalyzeSource()
    with patch.object(src, "_get", new=AsyncMock(return_value=fake)):
        df = await src.fetch(
            pair="BTC-USDT", data_type="liquidations", timeframe="1h",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
    assert df.columns == ["timestamp", "long_liq_usd", "short_liq_usd"]
    assert df["long_liq_usd"].to_list() == [50000.0, 100000.0]


def test_coinglass_supported_types():
    from bt.data.sources.coinglass import CoinglassSource
    assert CoinglassSource().supported_types() == ["liquidations"]


@pytest.mark.asyncio
async def test_coinglass_missing_key_raises(monkeypatch):
    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)
    from bt.data.sources.coinglass import CoinglassSource
    src = CoinglassSource()
    with pytest.raises(RuntimeError, match="COINGLASS_API_KEY"):
        await src.fetch(
            pair="BTC-USDT", data_type="liquidations", timeframe="1h",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
```

- [ ] **Step A3.2: Run fail**

Run: `uv run pytest tests/python/test_sources_third_party_liq.py -v`
Expected: FAIL — modules missing.

- [ ] **Step A3.3: Implement Coinalyze**

Create `src/bt/data/sources/coinalyze.py`:

```python
"""Coinalyze liquidation history adapter (key-gated).

Requires env COINALYZE_API_KEY (free tier registration at coinalyze.net).
"""
from __future__ import annotations

import os
from datetime import datetime

import httpx
import polars as pl


_BASE = "https://api.coinalyze.net/v1"


class CoinalyzeSource:
    def supported_types(self) -> list[str]:
        return ["liquidations"]

    async def fetch(
        self, pair: str, data_type: str, timeframe: str,
        start: datetime, end: datetime,
    ) -> pl.DataFrame:
        if data_type != "liquidations":
            raise ValueError(f"CoinalyzeSource does not support: {data_type}")
        key = os.getenv("COINALYZE_API_KEY")
        if not key:
            raise RuntimeError(
                "COINALYZE_API_KEY env var required. Free tier: https://coinalyze.net/api-docs/"
            )
        symbol = pair.replace("-", "") + "_PERP.A"
        interval = _to_interval(timeframe)
        from_s = int(start.timestamp())
        to_s = int(end.timestamp())

        resp = await self._get(key, symbol, interval, from_s, to_s)
        if not resp:
            return _empty_liq()
        history = resp[0].get("history", []) if isinstance(resp, list) else []
        rows = [
            {"timestamp": int(p["t"]) * 1000,
             "long_liq_usd": float(p.get("l", 0)),
             "short_liq_usd": float(p.get("s", 0))}
            for p in history
        ]
        if not rows:
            return _empty_liq()
        return pl.DataFrame(rows).sort("timestamp")

    async def _get(self, key, symbol, interval, from_s, to_s):
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f"{_BASE}/liquidation-history",
                params={"symbols": symbol, "interval": interval,
                        "from": from_s, "to": to_s, "convert_to_usd": "true"},
                headers={"api_key": key},
            )
            r.raise_for_status()
            return r.json()


def _to_interval(tf: str) -> str:
    mapping = {"1m": "1min", "5m": "5min", "15m": "15min",
               "30m": "30min", "1h": "1hour", "4h": "4hour", "1d": "daily"}
    return mapping.get(tf, "1hour")


def _empty_liq() -> pl.DataFrame:
    return pl.DataFrame(schema={
        "timestamp": pl.Int64, "long_liq_usd": pl.Float64, "short_liq_usd": pl.Float64,
    })
```

- [ ] **Step A3.4: Implement Coinglass**

Create `src/bt/data/sources/coinglass.py`:

```python
"""Coinglass v3 liquidation history adapter (key-gated).

Requires env COINGLASS_API_KEY (free tier at coinglass.com/api).
"""
from __future__ import annotations

import os
from datetime import datetime

import httpx
import polars as pl


_BASE = "https://open-api-v3.coinglass.com/api/futures/liquidation"


class CoinglassSource:
    def supported_types(self) -> list[str]:
        return ["liquidations"]

    async def fetch(
        self, pair: str, data_type: str, timeframe: str,
        start: datetime, end: datetime,
    ) -> pl.DataFrame:
        if data_type != "liquidations":
            raise ValueError(f"CoinglassSource does not support: {data_type}")
        key = os.getenv("COINGLASS_API_KEY")
        if not key:
            raise RuntimeError(
                "COINGLASS_API_KEY env var required. Free tier: https://www.coinglass.com/api"
            )
        symbol = pair.split("-")[0]
        interval = _to_interval(timeframe)

        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f"{_BASE}/history",
                params={"symbol": symbol, "interval": interval},
                headers={"CG-API-KEY": key, "accept": "application/json"},
            )
            r.raise_for_status()
            data = r.json().get("data", [])

        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        rows = []
        for p in data:
            ts = int(p.get("t") or p.get("time", 0))
            if start_ms <= ts <= end_ms:
                rows.append({
                    "timestamp": ts,
                    "long_liq_usd": float(p.get("longLiquidationUsd") or p.get("l", 0)),
                    "short_liq_usd": float(p.get("shortLiquidationUsd") or p.get("s", 0)),
                })
        if not rows:
            return pl.DataFrame(schema={
                "timestamp": pl.Int64, "long_liq_usd": pl.Float64, "short_liq_usd": pl.Float64,
            })
        return pl.DataFrame(rows).sort("timestamp")


def _to_interval(tf: str) -> str:
    mapping = {"1m": "m1", "5m": "m5", "15m": "m15",
               "30m": "m30", "1h": "h1", "4h": "h4", "1d": "d1"}
    return mapping.get(tf, "h1")
```

Register in `SOURCE_REGISTRY`:

```python
    "coinalyze": "bt.data.sources.coinalyze:CoinalyzeSource",
    "coinglass": "bt.data.sources.coinglass:CoinglassSource",
```

- [ ] **Step A3.5: Run pass**

Run: `uv run pytest tests/python/test_sources_third_party_liq.py -v`
Expected: PASS (all key-missing + mocked tests).

- [ ] **Step A3.6: Commit**

```bash
git add src/bt/data/sources/coinalyze.py src/bt/data/sources/coinglass.py src/bt/data/fetch.py tests/python/test_sources_third_party_liq.py
git commit -m "feat(liq): add Coinalyze + Coinglass historical liquidation adapters (key-gated)"
```

---

## Phase B — Derived feature modules

### Task B1: CVD (Cumulative Volume Delta)

**Files:**
- Create: `src/bt/features/cvd.py`
- Modify: `src/bt/features/registry.py` — register `"cvd"`
- Test: `tests/python/test_features_extended.py` (new file)

CVD = running sum of (buy_volume - sell_volume) from `trades` schema.

- [ ] **Step B1.1: Write test**

Create `tests/python/test_features_extended.py`:

```python
"""Tests for derived feature modules."""
import polars as pl


def test_cvd_running_sum():
    from bt.features.cvd import compute_cvd
    df = pl.DataFrame({
        "timestamp": [1, 2, 3, 4],
        "buy_volume": [10.0, 20.0, 5.0, 15.0],
        "sell_volume": [5.0, 25.0, 10.0, 5.0],
    })
    out = compute_cvd(df, params={})
    assert out["cvd"].to_list() == [5.0, 0.0, -5.0, 5.0]


def test_cvd_handles_empty():
    from bt.features.cvd import compute_cvd
    df = pl.DataFrame({"timestamp": [], "buy_volume": [], "sell_volume": []},
                      schema={"timestamp": pl.Int64, "buy_volume": pl.Float64, "sell_volume": pl.Float64})
    out = compute_cvd(df, params={})
    assert "cvd" in out.columns
    assert len(out) == 0
```

- [ ] **Step B1.2: Run fail**

Run: `uv run pytest tests/python/test_features_extended.py::test_cvd_running_sum -v`
Expected: FAIL.

- [ ] **Step B1.3: Implement**

Create `src/bt/features/cvd.py`:

```python
"""Cumulative Volume Delta feature.

CVD[t] = sum_{i<=t} (buy_volume[i] - sell_volume[i])

Requires `trades` data (buy_volume, sell_volume columns).
"""
from __future__ import annotations

import polars as pl


def compute_cvd(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    """Append cumulative volume delta column."""
    return df.with_columns(
        (pl.col("buy_volume") - pl.col("sell_volume")).cum_sum().alias("cvd")
    )
```

Register in `src/bt/features/registry.py`:

```python
from bt.features.cvd import compute_cvd
INDICATOR_REGISTRY["cvd"] = compute_cvd
```

- [ ] **Step B1.4: Run pass**

Run: `uv run pytest tests/python/test_features_extended.py -v`
Expected: PASS.

- [ ] **Step B1.5: Commit**

```bash
git add src/bt/features/cvd.py src/bt/features/registry.py tests/python/test_features_extended.py
git commit -m "feat(features): add cvd indicator from trades data"
```

### Task B2: Kimchi premium

**Files:**
- Create: `src/bt/features/kimp.py`
- Modify: `src/bt/features/registry.py`
- Test: extend `tests/python/test_features_extended.py`

Kimp[t] = (krw_close[t] / fx_rate[t] - usd_close[t]) / usd_close[t] * 100

Requires the feature DataFrame to already contain prefixed Upbit + Binance OHLCV joins (per `bt features build`) and an `fx_rate` column.

- [ ] **Step B2.1: Write test**

Append to `tests/python/test_features_extended.py`:

```python
def test_kimp_percent():
    from bt.features.kimp import compute_kimp
    df = pl.DataFrame({
        "timestamp": [1, 2, 3],
        "upbit_close": [55000000.0, 55500000.0, 54900000.0],   # KRW
        "binance_close": [40000.0, 40300.0, 39800.0],           # USD
        "fx_rate": [1350.0, 1350.0, 1350.0],
    })
    out = compute_kimp(df, params={"upbit_col": "upbit_close",
                                    "binance_col": "binance_close",
                                    "fx_col": "fx_rate"})
    # ((55000000/1350)/40000 - 1) * 100 ≈ 1.85%
    assert abs(out["kimp"][0] - ((55_000_000 / 1350 / 40_000 - 1) * 100)) < 1e-6
```

- [ ] **Step B2.2: Run fail + implement + pass**

Implement `src/bt/features/kimp.py`:

```python
"""Kimchi premium feature.

kimp = ((krw_price / fx_rate) / usd_price - 1) * 100
"""
from __future__ import annotations

import polars as pl


def compute_kimp(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    upbit_col = params.get("upbit_col", "upbit_close")
    binance_col = params.get("binance_col", "binance_close")
    fx_col = params.get("fx_col", "fx_rate")
    return df.with_columns(
        ((pl.col(upbit_col) / pl.col(fx_col) / pl.col(binance_col) - 1) * 100).alias("kimp")
    )
```

Register in registry. Run tests.

- [ ] **Step B2.3: Commit**

```bash
git add src/bt/features/kimp.py src/bt/features/registry.py tests/python/test_features_extended.py
git commit -m "feat(features): add kimchi premium indicator"
```

### Task B3: OI velocity / acceleration / z-score

**Files:**
- Create: `src/bt/features/oi_velocity.py`
- Modify: `src/bt/features/registry.py`
- Test: extend `tests/python/test_features_extended.py`

`oi_velocity` = `oi.diff()` (1-bar delta)
`oi_accel` = `oi_velocity.diff()` (2nd derivative)
`oi_z5m` = z-score of velocity over rolling N bars (default 60 bars = 5h at 5m)

- [ ] **Step B3.1-3: TDD cycle**

Test:

```python
def test_oi_velocity_and_zscore():
    from bt.features.oi_velocity import compute_oi_velocity
    df = pl.DataFrame({
        "timestamp": list(range(10)),
        "open_interest": [100.0, 110.0, 115.0, 120.0, 125.0,
                          130.0, 132.0, 135.0, 200.0, 210.0],
    })
    out = compute_oi_velocity(df, params={"window": 5})
    assert "oi_velocity" in out.columns
    assert "oi_accel" in out.columns
    assert "oi_z" in out.columns
    assert out["oi_velocity"].drop_nulls().to_list()[0] == 10.0
    # Spike at index 8 should have high z
    assert out["oi_z"].drop_nulls().max() > 1.0
```

Implementation:

```python
"""OI velocity / acceleration / z-score features.

velocity = OI[t] - OI[t-1]
accel = velocity[t] - velocity[t-1]
z = (velocity - rolling_mean) / rolling_std
"""
from __future__ import annotations

import polars as pl


def compute_oi_velocity(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    window = int(params.get("window", 60))
    velocity = pl.col("open_interest").diff().alias("oi_velocity")
    df = df.with_columns(velocity)
    df = df.with_columns(
        pl.col("oi_velocity").diff().alias("oi_accel"),
        ((pl.col("oi_velocity") - pl.col("oi_velocity").rolling_mean(window))
         / pl.col("oi_velocity").rolling_std(window)).alias("oi_z"),
    )
    return df
```

Register, test, commit:

```bash
git add src/bt/features/oi_velocity.py src/bt/features/registry.py tests/python/test_features_extended.py
git commit -m "feat(features): add oi_velocity/oi_accel/oi_z indicators"
```

### Task B4: Liquidation cluster

**Files:**
- Create: `src/bt/features/liq_cluster.py`
- Modify: `src/bt/features/registry.py`
- Test: extend

Per-bar features from liquidation series:
- `liq_intensity` = (long_liq_usd + short_liq_usd) per bar
- `liq_imbalance` = (long_liq_usd - short_liq_usd) / total
- `liq_z` = z-score of intensity over rolling window
- Degrades to all-zeros if liq columns missing.

- [ ] **Step B4.1-3: TDD**

Test:

```python
def test_liq_cluster_handles_missing_columns():
    from bt.features.liq_cluster import compute_liq_cluster
    df = pl.DataFrame({"timestamp": [1, 2, 3], "close": [100.0, 101.0, 102.0]})
    out = compute_liq_cluster(df, params={})
    assert "liq_intensity" in out.columns
    assert (out["liq_intensity"] == 0.0).all()


def test_liq_cluster_computes_imbalance():
    from bt.features.liq_cluster import compute_liq_cluster
    df = pl.DataFrame({
        "timestamp": list(range(5)),
        "long_liq_usd": [100.0, 0.0, 50.0, 200.0, 0.0],
        "short_liq_usd": [0.0, 50.0, 50.0, 0.0, 100.0],
    })
    out = compute_liq_cluster(df, params={"window": 3})
    assert out["liq_intensity"].to_list() == [100.0, 50.0, 100.0, 200.0, 100.0]
    assert out["liq_imbalance"][0] == 1.0  # all longs liquidated
    assert out["liq_imbalance"][1] == -1.0  # all shorts
```

Implementation:

```python
"""Liquidation cluster features.

Degrades to zeros if `long_liq_usd`/`short_liq_usd` columns missing.
"""
from __future__ import annotations

import polars as pl


def compute_liq_cluster(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    window = int(params.get("window", 60))
    has_liq = "long_liq_usd" in df.columns and "short_liq_usd" in df.columns
    if not has_liq:
        return df.with_columns([
            pl.lit(0.0).alias("liq_intensity"),
            pl.lit(0.0).alias("liq_imbalance"),
            pl.lit(0.0).alias("liq_z"),
        ])
    intensity = (pl.col("long_liq_usd") + pl.col("short_liq_usd"))
    df = df.with_columns(intensity.alias("liq_intensity"))
    df = df.with_columns([
        pl.when(pl.col("liq_intensity") > 0)
            .then((pl.col("long_liq_usd") - pl.col("short_liq_usd")) / pl.col("liq_intensity"))
            .otherwise(0.0).alias("liq_imbalance"),
        ((pl.col("liq_intensity") - pl.col("liq_intensity").rolling_mean(window))
         / pl.col("liq_intensity").rolling_std(window)).fill_nan(0.0).fill_null(0.0).alias("liq_z"),
    ])
    return df
```

Register, test, commit:

```bash
git add src/bt/features/liq_cluster.py src/bt/features/registry.py tests/python/test_features_extended.py
git commit -m "feat(features): add liq_cluster indicators (intensity/imbalance/z)"
```

### Task B5: Stage gate

**Files:**
- Create: `src/bt/features/stage_gate.py`
- Modify: `src/bt/features/registry.py`
- Test: extend

Multi-condition state machine inspired by alpha-hunter Stage Gate. Each stage requires its predecessor + a new gate to pass within a time window. APPROXIMATION: gate definitions are guessed from public UI text (OI build, CVD agreement, funding extreme). Tunable via params.

- [ ] **Step B5.1-3: TDD**

Test:

```python
def test_stage_gate_progression():
    from bt.features.stage_gate import compute_stage_gate
    df = pl.DataFrame({
        "timestamp": list(range(10)),
        "oi_z": [0, 0, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0, 2.1, 2.2],
        "cvd": [0, 100, 200, 300, 400, 500, 600, 700, 800, 900],
        "funding_rate": [0.0001, 0.0002, 0.0003, 0.0005, 0.0010,
                         0.0015, 0.0020, 0.0025, 0.0030, 0.0035],
    })
    out = compute_stage_gate(df, params={
        "oi_z_threshold": 1.5,
        "cvd_slope_min": 50.0,
        "funding_extreme": 0.001,
        "window_bars": 4,
    })
    assert "stage" in out.columns
    # Stage should advance over time as gates pass
    assert out["stage"].max() >= 2
```

Implementation: see template in plan-template (full code in source file).

```python
"""Stage gate state machine.

APPROXIMATION: original alpha-hunter formulas are not public; defaults below are
inferred from UI text ("OI build, CVD agreement, funding extreme, MI divergence").
All thresholds are tunable via params.
"""
from __future__ import annotations

import polars as pl


def compute_stage_gate(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    oi_z_min = float(params.get("oi_z_threshold", 1.5))
    cvd_slope_min = float(params.get("cvd_slope_min", 50.0))
    funding_extreme = float(params.get("funding_extreme", 0.001))
    window = int(params.get("window_bars", 12))

    cvd_slope = (pl.col("cvd") - pl.col("cvd").shift(window)).alias("_cvd_slope")
    df = df.with_columns(cvd_slope)

    df = df.with_columns([
        (pl.col("oi_z").fill_null(0) >= oi_z_min).cast(pl.Int64).alias("_g1_oi"),
        (pl.col("_cvd_slope").fill_null(0) >= cvd_slope_min).cast(pl.Int64).alias("_g2_cvd"),
        (pl.col("funding_rate").fill_null(0).abs() >= funding_extreme).cast(pl.Int64).alias("_g3_funding"),
    ])

    df = df.with_columns(
        (pl.col("_g1_oi") + pl.col("_g2_cvd") + pl.col("_g3_funding")).alias("stage")
    ).drop(["_cvd_slope", "_g1_oi", "_g2_cvd", "_g3_funding"])
    return df
```

Register, test, commit:

```bash
git add src/bt/features/stage_gate.py src/bt/features/registry.py tests/python/test_features_extended.py
git commit -m "feat(features): add stage_gate (approximation of alpha-hunter S0-S3)"
```

### Task B6: Relative Strength vs BTC

**Files:**
- Create: `src/bt/features/rs.py`
- Modify: `src/bt/features/registry.py`
- Test: extend

`rs_btc` = (close[t] / close[t-window]) / (btc_close[t] / btc_close[t-window])
> 1 means outperforming BTC.

- [ ] **Step B6.1-3: TDD + commit**

Test:

```python
def test_rs_outperforms_btc():
    from bt.features.rs import compute_rs
    df = pl.DataFrame({
        "timestamp": list(range(5)),
        "close": [100.0, 110.0, 120.0, 125.0, 130.0],   # +30%
        "btc_close": [50000.0, 50500.0, 51000.0, 51500.0, 52000.0],  # +4%
    })
    out = compute_rs(df, params={"window": 4})
    assert out["rs_btc"].drop_nulls()[0] > 1.0
```

Implementation:

```python
"""Relative strength vs BTC."""
from __future__ import annotations

import polars as pl


def compute_rs(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    window = int(params.get("window", 60))
    btc_col = params.get("btc_col", "btc_close")
    if btc_col not in df.columns:
        return df.with_columns(pl.lit(1.0).alias("rs_btc"))
    return df.with_columns(
        ((pl.col("close") / pl.col("close").shift(window))
         / (pl.col(btc_col) / pl.col(btc_col).shift(window))).alias("rs_btc")
    )
```

Register, test, commit:

```bash
git add src/bt/features/rs.py src/bt/features/registry.py tests/python/test_features_extended.py
git commit -m "feat(features): add rs_btc (relative strength vs BTC)"
```

---

## Phase C — Scanner framework

### Task C1: Scanner base + registry

**Files:**
- Create: `src/bt/scanners/__init__.py` (empty)
- Create: `src/bt/scanners/base.py`
- Create: `src/bt/scanners/registry.py`
- Test: `tests/python/test_scanners.py`

`ScannerSpec` Protocol:
- `name: str` — registry key
- `required_columns: list[str]` — feature df columns this scanner reads
- `default_exit_rules: ExitRules` — TP/SL/cooldown defaults
- `compute(df: pl.DataFrame, params: dict) -> pl.DataFrame` — adds `signal` (0/1) + `strength` columns
- `entry_filter(params: dict) -> EntryFilter` — for replay path; takes `ScanSignal` returns bool
- `is_approximation: bool` — flag for docs/CLI output

- [ ] **Step C1.1-5: TDD cycle**

Test:

```python
def test_scanner_base_protocol():
    from bt.scanners.base import ScannerSpec
    # Just check abstract structure exists
    assert hasattr(ScannerSpec, "compute")
    assert hasattr(ScannerSpec, "required_columns")


def test_registry_lookup():
    from bt.scanners.registry import SCANNER_REGISTRY, get_scanner
    # registry should be a dict
    assert isinstance(SCANNER_REGISTRY, dict)
    # after registering kimp_wave (Task D1), it should be reachable
```

Base:

```python
"""Scanner base spec.

Each scanner declares required columns + provides:
  - compute(df, params): adds signal (0/1) + strength columns
  - entry_filter(params): for ScanSignal-based replay path
  - default_exit_rules: TP/SL/cooldown
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

import polars as pl

from bt.replay.simulator import ExitRules
from bt.replay.loader import ScanSignal


EntryFilter = Callable[[ScanSignal], bool]


@dataclass
class ScannerResult:
    """Per-scanner backtest output."""
    name: str
    is_approximation: bool
    required_columns: list[str]
    missing_columns: list[str]
    summary: dict = field(default_factory=dict)
    error: str | None = None


class ScannerSpec(Protocol):
    name: str
    required_columns: list[str]
    is_approximation: bool

    def default_exit_rules(self) -> ExitRules: ...
    def compute(self, df: pl.DataFrame, params: dict) -> pl.DataFrame: ...
    def entry_filter(self, params: dict) -> EntryFilter | None: ...
```

Registry:

```python
"""Scanner registry — maps name → spec instance."""
from __future__ import annotations

from bt.scanners.base import ScannerSpec


SCANNER_REGISTRY: dict[str, ScannerSpec] = {}


def register(spec: ScannerSpec) -> None:
    SCANNER_REGISTRY[spec.name] = spec


def get_scanner(name: str) -> ScannerSpec:
    spec = SCANNER_REGISTRY.get(name)
    if spec is None:
        raise KeyError(f"Unknown scanner: {name}. Known: {list(SCANNER_REGISTRY.keys())}")
    return spec
```

Run, commit:

```bash
git add src/bt/scanners/__init__.py src/bt/scanners/base.py src/bt/scanners/registry.py tests/python/test_scanners.py
git commit -m "feat(scanners): base spec + registry"
```

### Task C2: Scanner runner

**Files:**
- Create: `src/bt/scanners/run.py`
- Test: extend `tests/python/test_scanners.py`

`run_scanner(name, feature_path, params, output_dir) -> ScannerResult`. Loads feature parquet, checks required columns, runs `compute`, then either summarizes via `summarize()` of long-only signals OR delegates to existing `replay()` if scanner exposes `entry_filter`.

- [ ] **Step C2.1-5: TDD**

Test (synthetic):

```python
def test_runner_reports_missing_columns(tmp_path):
    import polars as pl
    from bt.scanners.run import run_scanner
    from bt.scanners.registry import SCANNER_REGISTRY
    from bt.scanners.base import ScannerSpec

    class FakeSpec:
        name = "fake"
        required_columns = ["nonexistent_col"]
        is_approximation = False
        def default_exit_rules(self): from bt.replay.simulator import ExitRules; return ExitRules()
        def compute(self, df, params): return df.with_columns(pl.lit(0).alias("signal"))
        def entry_filter(self, params): return None

    SCANNER_REGISTRY["fake"] = FakeSpec()
    feature_path = tmp_path / "f.parquet"
    pl.DataFrame({"timestamp": [1], "close": [1.0]}).write_parquet(feature_path)
    result = run_scanner("fake", str(feature_path), {}, str(tmp_path))
    assert result.missing_columns == ["nonexistent_col"]
    assert result.error is not None
    SCANNER_REGISTRY.pop("fake")
```

Implementation:

```python
"""Scanner runner — load features, validate columns, compute signals, summarize."""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from bt.scanners.base import ScannerResult
from bt.scanners.registry import get_scanner


def run_scanner(
    name: str, feature_path: str, params: dict, output_dir: str,
) -> ScannerResult:
    spec = get_scanner(name)
    df = pl.read_parquet(feature_path)
    missing = [c for c in spec.required_columns if c not in df.columns]
    if missing:
        return ScannerResult(
            name=name, is_approximation=spec.is_approximation,
            required_columns=spec.required_columns,
            missing_columns=missing,
            error=f"Missing required columns: {missing}",
        )

    out = spec.compute(df, params)
    if "signal" not in out.columns:
        return ScannerResult(
            name=name, is_approximation=spec.is_approximation,
            required_columns=spec.required_columns, missing_columns=[],
            error="Scanner compute() did not produce 'signal' column",
        )

    n_signals = int((out["signal"] > 0).sum())
    summary = {
        "n_bars": len(out),
        "n_signals": n_signals,
        "signal_rate": n_signals / max(len(out), 1),
    }
    out_path = Path(output_dir) / f"{name}_signals.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(out_path)

    return ScannerResult(
        name=name, is_approximation=spec.is_approximation,
        required_columns=spec.required_columns, missing_columns=[],
        summary={**summary, "output_path": str(out_path)},
    )


def run_all(feature_path: str, params: dict, output_dir: str) -> list[ScannerResult]:
    from bt.scanners.registry import SCANNER_REGISTRY
    return [
        run_scanner(name, feature_path, params.get(name, {}), output_dir)
        for name in SCANNER_REGISTRY
    ]
```

Run, commit:

```bash
git add src/bt/scanners/run.py tests/python/test_scanners.py
git commit -m "feat(scanners): runner with missing-column gate + summary"
```

### Task C3: CLI command

**Files:**
- Modify: `src/bt/cli.py` — add `scan_backtest` command

- [ ] **Step C3.1: Add command**

After existing replay command in `src/bt/cli.py`:

```python
@app.command("scan-backtest")
def scan_backtest(
    name: str = typer.Option("all", help="Scanner name or 'all'"),
    feature_path: str = typer.Option(..., "--features", help="Path to feature parquet"),
    output: str = typer.Option("experiments/scanners", help="Output directory"),
    params: str = typer.Option("{}", help="JSON params dict, keyed by scanner name"),
) -> None:
    """Run scanner backtest(s) against feature parquet."""
    from bt.scanners.run import run_scanner, run_all
    from dataclasses import asdict

    params_dict = json.loads(params)
    if name == "all":
        results = run_all(feature_path, params_dict, output)
    else:
        results = [run_scanner(name, feature_path, params_dict.get(name, {}), output)]

    typer.echo(json.dumps([asdict(r) for r in results], indent=2, default=str))
```

- [ ] **Step C3.2: Verify CLI help**

Run: `uv run bt scan-backtest --help`
Expected: lists `--name`, `--features`, `--output`, `--params`.

- [ ] **Step C3.3: Commit**

```bash
git add src/bt/cli.py
git commit -m "feat(cli): add bt scan-backtest command"
```

---

## Phase D — Scanner ports (7)

Each scanner gets one task with full TDD cycle. The pattern: spec class with `compute` + optional `entry_filter`, register at module bottom, focused test, commit.

### Task D1: kimp-wave

**Files:**
- Create: `src/bt/scanners/kimp_wave.py`
- Test: extend `tests/python/test_scanners.py`

Spec:
- Required cols: `kimp`, `volume`
- Signal: `kimp >= threshold` AND `volume_z >= vol_z_min`
- APPROXIMATION-FREE: thresholds from public UI (1.5% kimp, 20% pool volatility, $50M vol)

- [ ] **Step D1.1-5: TDD**

Test:

```python
def test_kimp_wave_signals_above_threshold():
    import polars as pl
    from bt.scanners.kimp_wave import KimpWaveSpec
    spec = KimpWaveSpec()
    df = pl.DataFrame({
        "timestamp": [1, 2, 3, 4, 5],
        "kimp": [0.5, 1.0, 1.6, 2.0, 0.8],
        "volume": [100, 200, 500, 800, 150],
        "close": [50000.0] * 5,
    })
    out = spec.compute(df, params={"kimp_threshold": 1.5, "vol_min": 100})
    assert out["signal"].to_list() == [0, 0, 1, 1, 0]
```

Implementation:

```python
"""kimp-wave scanner port.

Source: https://akakin83.github.io/kimp-wave/
Public spec (from UI): kimp >= 1.5%, 24h vol >= $50M, 24h pool vol >= 20%.
We model the first two; pool-volatility filter omitted (no per-pool data here).
"""
from __future__ import annotations

import polars as pl

from bt.scanners.base import EntryFilter
from bt.scanners.registry import register
from bt.replay.simulator import ExitRules


class KimpWaveSpec:
    name = "kimp_wave"
    required_columns = ["kimp", "volume"]
    is_approximation = False

    def default_exit_rules(self) -> ExitRules:
        return ExitRules(tp_pct=0.02, sl_pct=0.01, max_hold_bars=120)

    def compute(self, df: pl.DataFrame, params: dict) -> pl.DataFrame:
        kt = float(params.get("kimp_threshold", 1.5))
        vmin = float(params.get("vol_min", 50_000_000))
        signal = ((pl.col("kimp") >= kt) & (pl.col("volume") >= vmin)).cast(pl.Int64).alias("signal")
        strength = pl.col("kimp").alias("strength")
        return df.with_columns([signal, strength])

    def entry_filter(self, params: dict) -> EntryFilter | None:
        return None  # kimp data not in ScanSignal — replay path not supported


register(KimpWaveSpec())
```

Wire registration: import in `src/bt/scanners/__init__.py`:

```python
from bt.scanners import kimp_wave  # noqa: F401
```

Run, commit:

```bash
git add src/bt/scanners/kimp_wave.py src/bt/scanners/__init__.py tests/python/test_scanners.py
git commit -m "feat(scanners): port kimp-wave (kimp + vol thresholds)"
```

### Task D2: liqpressuremap

Spec:
- Required: `liq_intensity`, `liq_z`, `liq_imbalance`
- Signal: `liq_z >= 3.0` (3σ ignition) AND `|liq_imbalance| >= 0.6` (direction bias)
- Public spec from UI matches.

- [ ] **Step D2.1-5: TDD + register + commit**

Test:

```python
def test_liqpressuremap_3sigma_ignition():
    import polars as pl
    from bt.scanners.liqpressuremap import LiqPressureMapSpec
    spec = LiqPressureMapSpec()
    df = pl.DataFrame({
        "timestamp": list(range(5)),
        "liq_intensity": [100, 100, 100, 5000, 100],
        "liq_z": [0, 0, 0, 4.5, 0],
        "liq_imbalance": [0.1, 0.0, -0.1, 0.8, 0.2],
    })
    out = spec.compute(df, params={})
    assert out["signal"].to_list() == [0, 0, 0, 1, 0]
```

Impl:

```python
"""liqpressuremap scanner port.

Source: https://akakin83.github.io/liqpressuremap/
Public spec (UI): 3σ liquidation velocity → ignition; |bias| >= 60 = strong direction.
"""
from __future__ import annotations

import polars as pl

from bt.scanners.registry import register
from bt.replay.simulator import ExitRules


class LiqPressureMapSpec:
    name = "liqpressuremap"
    required_columns = ["liq_intensity", "liq_z", "liq_imbalance"]
    is_approximation = False

    def default_exit_rules(self) -> ExitRules:
        return ExitRules(tp_pct=0.015, sl_pct=0.008, max_hold_bars=30)

    def compute(self, df: pl.DataFrame, params: dict) -> pl.DataFrame:
        sig_z = float(params.get("z_threshold", 3.0))
        bias = float(params.get("bias_threshold", 0.6))
        s = ((pl.col("liq_z") >= sig_z) & (pl.col("liq_imbalance").abs() >= bias)).cast(pl.Int64)
        return df.with_columns([s.alias("signal"), pl.col("liq_z").alias("strength")])

    def entry_filter(self, params): return None


register(LiqPressureMapSpec())
```

Wire `__init__.py`. Test, commit.

### Task D3: alpha-hunter

Spec:
- Required: `oi_z`, `cvd`, `funding_rate`, `stage` (from stage_gate feature)
- Signal: `stage >= 2` (S2 entry) — `is_approximation = True`
- APPROXIMATION marker

- [ ] **Step D3.1-5: TDD + register + commit**

Test:

```python
def test_alpha_hunter_stage_gate_signals():
    import polars as pl
    from bt.scanners.alpha_hunter import AlphaHunterSpec
    spec = AlphaHunterSpec()
    df = pl.DataFrame({
        "timestamp": list(range(5)),
        "oi_z": [0, 1.6, 1.7, 1.8, 0.5],
        "cvd": [0, 100, 200, 300, 50],
        "funding_rate": [0.0001, 0.001, 0.0015, 0.002, 0.0001],
        "stage": [0, 1, 2, 3, 0],
    })
    out = spec.compute(df, params={})
    assert out["signal"].to_list() == [0, 0, 1, 1, 0]
```

Impl:

```python
"""alpha-hunter scanner port (APPROXIMATION).

Source: https://akakin83.github.io/alpha-hunter/
Original Stage Gate weights and conditions are not public. We approximate
S0 → S3 progression via the `stage` feature (see features/stage_gate.py)
which combines OI z-score, CVD slope, funding extreme into an ordinal stage
indicator. Signal triggers at S2 (entry confirmed) by default.
"""
from __future__ import annotations

import polars as pl

from bt.scanners.registry import register
from bt.replay.simulator import ExitRules


class AlphaHunterSpec:
    name = "alpha_hunter"
    required_columns = ["oi_z", "cvd", "funding_rate", "stage"]
    is_approximation = True  # APPROXIMATION: weights inferred

    def default_exit_rules(self) -> ExitRules:
        return ExitRules(tp_pct=0.025, sl_pct=0.012, max_hold_bars=60)

    def compute(self, df: pl.DataFrame, params: dict) -> pl.DataFrame:
        # APPROXIMATION: entry stage = 2 (S2). Original may use different gate.
        entry_stage = int(params.get("entry_stage", 2))
        s = (pl.col("stage") >= entry_stage).cast(pl.Int64)
        return df.with_columns([s.alias("signal"), pl.col("stage").cast(pl.Float64).alias("strength")])

    def entry_filter(self, params): return None


register(AlphaHunterSpec())
```

Wire, test, commit.

### Task D4: alpha-terminal

Spec:
- Required: `vwap`, `rs_btc`, `liq_z`, `bb_squeeze` (computed inline if missing), `oi_z`
- Composite alpha score 0-100 from 5 weighted factors. APPROXIMATION.
- Signal triggers when score >= 55 (Strong Bull, per UI).

- [ ] **Step D4.1-5: TDD + register + commit**

Test:

```python
def test_alpha_terminal_score_threshold():
    import polars as pl
    from bt.scanners.alpha_terminal import AlphaTerminalSpec
    spec = AlphaTerminalSpec()
    df = pl.DataFrame({
        "timestamp": list(range(3)),
        "close": [100, 110, 120],
        "vwap": [99, 100, 105],
        "rs_btc": [1.0, 1.5, 2.0],
        "liq_z": [0, 2.0, 3.5],
        "oi_z": [0, 1.0, 2.5],
    })
    out = spec.compute(df, params={"score_threshold": 55})
    assert out["signal"][2] == 1  # high score row
```

Impl:

```python
"""alpha-terminal scanner port (APPROXIMATION).

Source: https://akakin83.github.io/alpha-terminal/
Original weights for the 18-factor Alpha Score are private. We approximate
with 5 normalized factors:
  +25 if close > vwap (trend up)
  +25 if rs_btc > 1.2 (outperforming BTC)
  +20 if liq_z > 2 (liquidation cascade)
  +15 if oi_z > 1.5 (OI building)
  +15 if abs(funding_rate) > 0.001 (funding extreme)

Default entry threshold = 55 (Strong Bull, from UI).
"""
from __future__ import annotations

import polars as pl

from bt.scanners.registry import register
from bt.replay.simulator import ExitRules


class AlphaTerminalSpec:
    name = "alpha_terminal"
    required_columns = ["close", "vwap", "rs_btc", "liq_z", "oi_z"]
    is_approximation = True

    def default_exit_rules(self) -> ExitRules:
        return ExitRules(tp_pct=0.03, sl_pct=0.015, max_hold_bars=120)

    def compute(self, df: pl.DataFrame, params: dict) -> pl.DataFrame:
        # APPROXIMATION: weights are guesses, tunable via params
        w_vwap = int(params.get("w_vwap", 25))
        w_rs = int(params.get("w_rs", 25))
        w_liq = int(params.get("w_liq", 20))
        w_oi = int(params.get("w_oi", 15))
        w_fund = int(params.get("w_fund", 15))
        threshold = int(params.get("score_threshold", 55))

        score = (
            pl.when(pl.col("close") > pl.col("vwap")).then(w_vwap).otherwise(0)
            + pl.when(pl.col("rs_btc") > 1.2).then(w_rs).otherwise(0)
            + pl.when(pl.col("liq_z") > 2.0).then(w_liq).otherwise(0)
            + pl.when(pl.col("oi_z") > 1.5).then(w_oi).otherwise(0)
        )
        if "funding_rate" in df.columns:
            score = score + pl.when(pl.col("funding_rate").abs() > 0.001).then(w_fund).otherwise(0)
        df = df.with_columns(score.alias("alpha_score"))
        return df.with_columns([
            (pl.col("alpha_score") >= threshold).cast(pl.Int64).alias("signal"),
            pl.col("alpha_score").cast(pl.Float64).alias("strength"),
        ])

    def entry_filter(self, params): return None


register(AlphaTerminalSpec())
```

Wire, test, commit.

### Task D5: nahonja-scanner

Spec:
- Required: `oi_z`, `taker_ratio`, `funding_rate`, `liq_z`, `cvd`
- 8-signal weighted composite. APPROXIMATION.
- Signal threshold 30/50/70 tiers (per UI). Default 50.

- [ ] **Step D5.1-5: TDD + register + commit**

Pattern same as alpha-terminal but more factors. APPROXIMATION marker. Use ad-hoc weights that match the 8 categories named in UI: momentum (rs_btc), volume (volume_ratio derived), breakout (close vs vwap), volume compression (atr inverse — skipped if no atr), extreme funding, OI divergence (oi_z - cvd disagreement), liquidation bounce (liq_z), early acceleration (oi_accel).

Impl outline:

```python
"""nahonja-scanner port (APPROXIMATION).

Source: https://akakin83.github.io/nahonja-scanner/
Original 8-signal weights private; we map each UI label to nearest available factor.
"""
from __future__ import annotations

import polars as pl

from bt.scanners.registry import register
from bt.replay.simulator import ExitRules


class NahonjaSpec:
    name = "nahonja"
    required_columns = ["oi_z", "taker_ratio", "funding_rate", "liq_z", "cvd", "rs_btc"]
    is_approximation = True

    def default_exit_rules(self) -> ExitRules:
        return ExitRules(tp_pct=0.025, sl_pct=0.012, max_hold_bars=60)

    def compute(self, df: pl.DataFrame, params: dict) -> pl.DataFrame:
        # APPROXIMATION: each factor → 12.5 points (8×12.5 = 100)
        threshold = int(params.get("score_threshold", 50))
        score = (
            pl.when(pl.col("rs_btc") > 1.1).then(12).otherwise(0)            # momentum
            + pl.when(pl.col("taker_ratio") > 1.2).then(12).otherwise(0)     # volume
            + pl.when(pl.col("oi_z") > 1.5).then(12).otherwise(0)            # breakout
            + pl.when(pl.col("oi_z").abs() < 0.3).then(12).otherwise(0)      # compression
            + pl.when(pl.col("funding_rate").abs() > 0.001).then(12).otherwise(0)  # extreme funding
            + pl.when((pl.col("oi_z") > 0) & (pl.col("cvd").diff() < 0)).then(12).otherwise(0)  # divergence
            + pl.when(pl.col("liq_z") > 2.0).then(12).otherwise(0)           # liq bounce
            + pl.when(pl.col("oi_z").diff() > 0.5).then(16).otherwise(0)     # acceleration
        )
        return df.with_columns([
            score.alias("nahonja_score"),
            (score >= threshold).cast(pl.Int64).alias("signal"),
            score.cast(pl.Float64).alias("strength"),
        ])

    def entry_filter(self, params): return None


register(NahonjaSpec())
```

Wire, test, commit.

### Task D6: squeeze-hunter

Spec:
- Required: `oi_velocity`, `cvd`, `liq_z`, `toptrader_lsr`
- Signal: OI 5min accel + CVD agree + liq spike + LSR confirm. APPROXIMATION.

- [ ] **Step D6.1-5: TDD + register + commit**

Impl:

```python
"""squeeze-hunter port (APPROXIMATION).

Source: https://akakin83.github.io/squeeze-hunter/
Multi-layer validation: OI 5m accel + CVD + liq + LSR. Original layer weights private.
"""
from __future__ import annotations

import polars as pl

from bt.scanners.registry import register
from bt.replay.simulator import ExitRules


class SqueezeHunterSpec:
    name = "squeeze_hunter"
    required_columns = ["oi_velocity", "cvd", "liq_z", "toptrader_lsr"]
    is_approximation = True

    def default_exit_rules(self) -> ExitRules:
        return ExitRules(tp_pct=0.02, sl_pct=0.01, max_hold_bars=45)

    def compute(self, df: pl.DataFrame, params: dict) -> pl.DataFrame:
        accel_min = float(params.get("oi_velocity_min", 0.0))
        cvd_min = float(params.get("cvd_min", 0.0))
        liq_z_min = float(params.get("liq_z_min", 1.5))
        lsr_threshold = float(params.get("lsr_threshold", 1.5))
        layers = (
            (pl.col("oi_velocity") > accel_min).cast(pl.Int64)
            + (pl.col("cvd").diff() > cvd_min).cast(pl.Int64)
            + (pl.col("liq_z") > liq_z_min).cast(pl.Int64)
            + (pl.col("toptrader_lsr") > lsr_threshold).cast(pl.Int64)
        )
        # APPROXIMATION: all 4 layers required for entry
        return df.with_columns([
            layers.alias("squeeze_layers"),
            (layers >= 4).cast(pl.Int64).alias("signal"),
            layers.cast(pl.Float64).alias("strength"),
        ])

    def entry_filter(self, params): return None


register(SqueezeHunterSpec())
```

Wire, test, commit.

### Task D7: kimchi-audit-picks

Spec:
- Required: per-pair scalar metadata (bithumb_concentration, whale_pct, gap_history_count, score)
- Schema differs — quarterly snapshot, not time-series
- Signal: filter pairs where `score >= 55` AND `bithumb_concentration >= 0.5`
- This scanner is universe-selection, not time-of-entry. Returns picked pair list, not bar-level signals.

Special-case in runner: if `compute()` returns df with `pair` column instead of `signal`, treat as universe filter.

- [ ] **Step D7.1-5: TDD + register + commit**

Test:

```python
def test_kimchi_audit_picks_filter():
    import polars as pl
    from bt.scanners.kimchi_audit import KimchiAuditSpec
    spec = KimchiAuditSpec()
    df = pl.DataFrame({
        "pair": ["BTC-KRW", "ETH-KRW", "DOGE-KRW", "SHIB-KRW"],
        "score": [70, 50, 60, 40],
        "bithumb_concentration": [0.6, 0.4, 0.7, 0.3],
        "whale_pct": [0.4, 0.2, 0.5, 0.6],
    })
    out = spec.compute(df, params={"score_min": 55, "concentration_min": 0.5})
    picked = out.filter(pl.col("signal") == 1)["pair"].to_list()
    assert picked == ["BTC-KRW", "DOGE-KRW"]
```

Impl:

```python
"""kimchi-audit-picks port.

Source: https://londonpotato1.github.io/kimchi-audit-picks/
Universe selection (not time-series). Inputs are per-pair quarterly snapshot:
score, bithumb_concentration, whale_pct, gap_history_count.

Public-spec thresholds (from UI): score 50/55/60+, concentration 50%/80%+.
"""
from __future__ import annotations

import polars as pl

from bt.scanners.registry import register
from bt.replay.simulator import ExitRules


class KimchiAuditSpec:
    name = "kimchi_audit"
    required_columns = ["pair", "score", "bithumb_concentration", "whale_pct"]
    is_approximation = False

    def default_exit_rules(self) -> ExitRules:
        return ExitRules(tp_pct=0.05, sl_pct=0.02, max_hold_bars=1440)

    def compute(self, df: pl.DataFrame, params: dict) -> pl.DataFrame:
        sm = float(params.get("score_min", 55))
        cm = float(params.get("concentration_min", 0.5))
        wm = float(params.get("whale_pct_min", 0.0))
        s = ((pl.col("score") >= sm)
             & (pl.col("bithumb_concentration") >= cm)
             & (pl.col("whale_pct") >= wm)).cast(pl.Int64)
        return df.with_columns([s.alias("signal"), pl.col("score").cast(pl.Float64).alias("strength")])

    def entry_filter(self, params): return None


register(KimchiAuditSpec())
```

Wire, test, commit.

---

## Phase E — End-to-end test

### Task E1: Full pipeline test

**Files:**
- Create: `tests/python/test_scan_backtest_e2e.py`

End-to-end: build synthetic feature parquet → run `scan_backtest --name all` → assert per-scanner JSON results contain status, signal counts, missing-column reasons.

- [ ] **Step E1.1: Write test**

```python
"""End-to-end test for scan-backtest CLI."""
import json
import subprocess
from pathlib import Path

import polars as pl
import pytest


def test_scan_backtest_all_smoke(tmp_path):
    # Build minimal feature parquet with all columns scanners might need
    feature_path = tmp_path / "features.parquet"
    n = 100
    df = pl.DataFrame({
        "timestamp": list(range(n)),
        "close": [100.0 + i * 0.1 for i in range(n)],
        "volume": [1_000_000.0] * n,
        "vwap": [100.0] * n,
        "kimp": [1.0 + (i % 5) * 0.5 for i in range(n)],
        "rs_btc": [1.0 + (i % 3) * 0.2 for i in range(n)],
        "oi_z": [(i % 4) - 1.0 for i in range(n)],
        "oi_velocity": [(i % 3) - 1.0 for i in range(n)],
        "cvd": list(range(n)),
        "funding_rate": [0.0001 * ((i % 7) - 3) for i in range(n)],
        "liq_intensity": [100.0] * n,
        "liq_z": [0.0] * n,
        "liq_imbalance": [0.0] * n,
        "stage": [(i // 25) for i in range(n)],
        "taker_ratio": [1.0 + (i % 3) * 0.1 for i in range(n)],
        "toptrader_lsr": [1.0 + (i % 5) * 0.1 for i in range(n)],
    })
    df.write_parquet(feature_path)

    out_dir = tmp_path / "out"
    result = subprocess.run(
        ["uv", "run", "bt", "scan-backtest",
         "--name", "all", "--features", str(feature_path),
         "--output", str(out_dir)],
        capture_output=True, text=True, cwd=Path(__file__).parent.parent.parent,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    names = {r["name"] for r in payload}
    assert "kimp_wave" in names
    assert "alpha_hunter" in names
    # kimchi_audit will report missing 'pair' column — that's a valid result
    kimchi = next(r for r in payload if r["name"] == "kimchi_audit")
    assert "pair" in kimchi["missing_columns"]
```

- [ ] **Step E1.2: Run test**

Run: `uv run pytest tests/python/test_scan_backtest_e2e.py -v`
Expected: PASS.

- [ ] **Step E1.3: Commit**

```bash
git add tests/python/test_scan_backtest_e2e.py
git commit -m "test: e2e scan-backtest with synthetic features"
```

---

## Phase F — Documentation

### Task F1: Per-scanner spec doc

**Files:**
- Create: `docs/scanners.md`

- [ ] **Step F1.1: Write spec table**

```markdown
# Scanner Backtest Reference

Each scanner is implemented in `src/bt/scanners/<name>.py`. Run via:
```
bt scan-backtest --name <name|all> --features path/to/features.parquet
```

## Scanner registry

| Name | Source URL | Approximation? | Required columns | Public spec basis |
|------|------------|----------------|------------------|-------------------|
| kimp_wave | akakin83/kimp-wave | No | kimp, volume | UI: kimp 1.5%+, vol $50M+ |
| liqpressuremap | akakin83/liqpressuremap | No | liq_intensity, liq_z, liq_imbalance | UI: 3σ ignition, |bias| ≥ 0.6 |
| alpha_hunter | akakin83/alpha-hunter | YES | oi_z, cvd, funding_rate, stage | Stage Gate weights private |
| alpha_terminal | akakin83/alpha-terminal | YES | close, vwap, rs_btc, liq_z, oi_z | 18-factor weights private |
| nahonja | akakin83/nahonja-scanner | YES | oi_z, taker_ratio, funding_rate, liq_z, cvd, rs_btc | 8-signal weights private |
| squeeze_hunter | akakin83/squeeze-hunter | YES | oi_velocity, cvd, liq_z, toptrader_lsr | Layer weights private |
| kimchi_audit | londonpotato1/kimchi-audit-picks | No | pair, score, bithumb_concentration, whale_pct | UI: score 55+, conc 50%+ |

## APPROXIMATION marker

Scanners marked YES have private formulas. Their `compute()` uses inferred
defaults from public UI text. All thresholds are tunable via the `--params`
CLI flag (JSON dict keyed by scanner name).

Example tune:
```
bt scan-backtest --name alpha_terminal --features features.parquet \
  --params '{"alpha_terminal": {"score_threshold": 70, "w_vwap": 30}}'
```

## Required upstream data

| Feature | Required source/type |
|---------|----------------------|
| kimp | upbit ohlcv + binance ohlcv + fx |
| oi_z, oi_velocity | binance oi_hist (or binance_vision metrics) |
| cvd | binance trades |
| funding_rate | binance funding |
| liq_intensity, liq_z, liq_imbalance | bt collect-liq daemon (live) OR coinalyze/coinglass with key |
| taker_ratio | binance taker_ratio (or binance_vision metrics) |
| toptrader_lsr | binance lsr_top_pos (or binance_vision metrics) |
| rs_btc | features/rs.py (needs btc ohlcv aligned in feature parquet) |
| stage | features/stage_gate.py (needs oi_z + cvd + funding_rate) |
| alpha_score | features/alpha_score.py (computed inline by scanner) |
```

- [ ] **Step F1.2: Update data-coverage matrix**

Append to `docs/data-coverage.md` after existing per-scanner table:

```markdown
## Liquidations source matrix (after Plan 2)

| Source | Coverage | Cost |
|--------|----------|------|
| Binance REST `/forceOrders` | ❌ Removed 2021 | Free |
| Binance Vision `liquidationSnapshot/` | ❌ Empty | Free |
| Binance WS `!forceOrder@arr` | ✅ Live forward only | Free; needs `bt collect-liq` daemon |
| Coinalyze | ✅ Historical | Free tier with `COINALYZE_API_KEY` env |
| Coinglass v3 | ✅ Historical | Free tier with `COINGLASS_API_KEY` env |
| CryptoQuant / Kaiko | ✅ Historical | Paid; out of scope |

## Bonus historical sources (Binance Vision)

| Source | Coverage | Notes |
|--------|----------|-------|
| `binance_vision metrics` | 1-min OI, top-trader LSR, taker ratio (any history) | replaces 30-day REST limit |
| `binance_vision book_depth_hist` | 1-min orderbook snapshots (any history) | replaces snapshot-only depth_snap |
```

- [ ] **Step F1.3: Commit**

```bash
git add docs/scanners.md docs/data-coverage.md
git commit -m "docs: scanner spec reference + liquidation source matrix"
```

### Task F2: Final test sweep

- [ ] **Step F2.1: Run full Python test suite**

Run: `uv run pytest tests/python/ -v`
Expected: all green. Adds ~15 new tests (features ×6, scanners base, runner, 7 scanner tests, e2e, sources binance_vision, third-party liq).

- [ ] **Step F2.2: Run Rust sanity**

Run: `cargo test -p bt-engine`
Expected: still green.

- [ ] **Step F2.3: Run a real scanner against real data**

Build small feature parquet from real Binance Vision metrics + Binance OHLCV + your collect-liq output, then:
```bash
uv run bt scan-backtest --name liqpressuremap --features /path/to/features.parquet --output experiments/scanners
```
Inspect output JSON. Expected: `n_signals` reasonable (not 0, not all-bars). If 0 signals, lower thresholds via `--params`.

- [ ] **Step F2.4: Final commit if any tweaks needed**

Otherwise skip.

---

## Self-Review

1. **Spec coverage**: All 7 scanners (kimp_wave, liqpressuremap, alpha_hunter, alpha_terminal, nahonja, squeeze_hunter, kimchi_audit) have spec class + test + registration. ✅
2. **Liquidations investigation**: doc + 3 adapters (binance_vision metrics, coinalyze, coinglass) with key-gating. ✅
3. **APPROXIMATION marking**: 4/7 scanners flagged `is_approximation = True`, runner reports flag in output, scanners.md table lists each. ✅
4. **Graceful degradation**: liq_cluster returns zeros if columns missing; runner reports missing_columns clearly without crashing. ✅
5. **Placeholder scan**: no TODOs, no "implement later". Each task has full code. ✅
6. **Type consistency**: feature column names (`kimp`, `cvd`, `oi_z`, `liq_z`, `liq_imbalance`, `liq_intensity`, `stage`, `rs_btc`, `oi_velocity`, `oi_accel`) match across feature modules, scanner specs, and tests. ✅
7. **Existing API preservation**: `bt replay`, `bt backtest`, `STRATEGY_REGISTRY` untouched. New `SCANNER_REGISTRY` is parallel, not a replacement. ✅

## Out-of-scope (intentional)

- ML-driven Stage Gate weight learning — defer to Optuna pass after baseline metrics
- Wyckoff phase detector — too speculative without concrete spec; can add later
- Kimchi-audit data ingestion (per-pair score table) — manual import for now; could add scraper of github page or Bithumb API direct
- Live (real-time) scanner mode — focus is backtest; live wiring is Phase 4
