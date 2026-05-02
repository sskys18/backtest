# Scanner Replication — Data Ingestion Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use sspower:subagent-driven-development (recommended) or sspower:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `bt data fetch` so it can pull every data type used by the six akakin83 scanner dashboards (alpha-hunter, alpha-terminal, kimp-wave, liqpressuremap, nahonja-scanner, squeeze-hunter) plus londonpotato1/kimchi-audit-picks, excluding only what Binance no longer exposes via REST.

**Architecture:** Add new methods to `BinanceSource` for `oi_hist`, `lsr_top_pos`, `lsr_top_acct`, `lsr_global`, `taker_ratio`, `depth_snap`. Add three new sources: `fx` (USD/KRW via ExchangeRate-API + Upbit USDT-KRW fallback), `fng` (alternative.me Fear & Greed), `onchain` (blockchain.info BTC stats). Add a separate `bt collect-liq` daemon command that streams `!forceOrder@arr` Binance Futures WebSocket and appends to a daily Parquet partition (live-only — Binance removed historical liquidations REST in 2021). Fix `data/raw/{source}/{pair}/{type}/{timeframe}/` path inconsistency. Extend canonical `SCHEMAS` with five new types.

**Tech Stack:** Python 3.14, Polars, httpx (via `core.http.client`), websockets (for liq daemon), Typer, pytest, exchange-adapters editable dep.

**Scope exclusions (documented, not implemented):**
- Historical liquidations (Binance API removed 2021 — only WS live capture in this plan)
- Full historical depth (snapshots only — no LOB reconstruction)
- 3rd-party paid APIs (Coinglass, Coinalyze) — out of scope; live WS is the free path

**Follow-up plans (outlined, not in this plan):**
- `2026-05-02-scanner-replication-features.md` — derived feature modules (CVD, kimp, OI velocity, BB squeeze, VWAP, RS, Wyckoff, liq cluster, stage gate, alpha score)
- `2026-05-02-scanner-replication-strategies.md` — 6 strategy ports + replay validation

---

## File map

**Modify:**
- `src/bt/data/schemas.py` — add 5 schemas
- `src/bt/data/sources/binance.py` — add 6 fetch methods, extend `supported_types()` and `fetch()` dispatch
- `src/bt/data/fetch.py` — add `fx`/`fng`/`onchain` to registry, fix output path for timeframed types
- `src/bt/cli.py` — extend `--type` help; add `bt collect-liq` command
- `tests/python/test_data_validate.py` — extend with new schema cases

**Create:**
- `src/bt/data/sources/fx.py` — FX rates source
- `src/bt/data/sources/fng.py` — Fear & Greed source
- `src/bt/data/sources/onchain.py` — On-chain BTC source
- `src/bt/data/collect_liq.py` — Binance forceOrder WS collector daemon
- `tests/python/test_sources_binance_extensions.py` — mocked tests for new Binance methods
- `tests/python/test_sources_fx.py` — FX adapter tests
- `tests/python/test_sources_fng.py` — F&G adapter tests
- `tests/python/test_sources_onchain.py` — On-chain adapter tests
- `tests/python/test_fetch_paths.py` — path layout consistency tests
- `docs/data-coverage.md` — matrix: which scanner needs which data type, status per type

---

## Task 1: Schema extensions

**Files:**
- Modify: `src/bt/data/schemas.py:8-67` — add 5 new schema entries
- Test: `tests/python/test_data_validate.py` — append new test class

- [ ] **Step 1.1: Write failing schema tests**

Append to `tests/python/test_data_validate.py`:

```python
class TestExtendedSchemas:
    def test_oi_hist_schema(self):
        from bt.data.schemas import SCHEMAS, get_required_columns
        assert "oi_hist" in SCHEMAS
        assert get_required_columns("oi_hist") == [
            "timestamp", "open_interest", "open_interest_value",
        ]

    def test_lsr_schema(self):
        from bt.data.schemas import SCHEMAS, get_required_columns
        for t in ("lsr_top_pos", "lsr_top_acct", "lsr_global"):
            assert t in SCHEMAS
            cols = get_required_columns(t)
            assert cols == ["timestamp", "long_short_ratio", "long_account", "short_account"]

    def test_taker_ratio_schema(self):
        from bt.data.schemas import SCHEMAS, get_required_columns
        assert "taker_ratio" in SCHEMAS
        assert get_required_columns("taker_ratio") == [
            "timestamp", "buy_sell_ratio", "buy_vol", "sell_vol",
        ]

    def test_depth_snap_schema(self):
        from bt.data.schemas import SCHEMAS, get_required_columns
        assert "depth_snap" in SCHEMAS
        cols = get_required_columns("depth_snap")
        assert cols[0] == "timestamp"
        assert "bid_total_qty" in cols
        assert "ask_total_qty" in cols
        assert "imbalance" in cols

    def test_fng_schema(self):
        from bt.data.schemas import SCHEMAS, get_required_columns
        assert "fng" in SCHEMAS
        assert get_required_columns("fng") == [
            "timestamp", "value", "classification",
        ]

    def test_onchain_schema(self):
        from bt.data.schemas import SCHEMAS, get_required_columns
        assert "onchain" in SCHEMAS
        cols = get_required_columns("onchain")
        assert "timestamp" in cols
        assert "tx_count" in cols
        assert "hashrate" in cols
        assert "fees_usd" in cols
```

- [ ] **Step 1.2: Run tests to verify failure**

Run: `uv run pytest tests/python/test_data_validate.py::TestExtendedSchemas -v`
Expected: FAIL — `KeyError` on schemas, `assert ... in SCHEMAS` failures.

- [ ] **Step 1.3: Implement schema additions**

Append to `src/bt/data/schemas.py` `SCHEMAS` dict (before closing brace):

```python
    "oi_hist": {
        "timestamp": pl.Int64,
        "open_interest": pl.Float64,
        "open_interest_value": pl.Float64,
    },
    "lsr_top_pos": {
        "timestamp": pl.Int64,
        "long_short_ratio": pl.Float64,
        "long_account": pl.Float64,
        "short_account": pl.Float64,
    },
    "lsr_top_acct": {
        "timestamp": pl.Int64,
        "long_short_ratio": pl.Float64,
        "long_account": pl.Float64,
        "short_account": pl.Float64,
    },
    "lsr_global": {
        "timestamp": pl.Int64,
        "long_short_ratio": pl.Float64,
        "long_account": pl.Float64,
        "short_account": pl.Float64,
    },
    "taker_ratio": {
        "timestamp": pl.Int64,
        "buy_sell_ratio": pl.Float64,
        "buy_vol": pl.Float64,
        "sell_vol": pl.Float64,
    },
    "depth_snap": {
        "timestamp": pl.Int64,
        "bid_total_qty": pl.Float64,
        "ask_total_qty": pl.Float64,
        "bid_top10_qty": pl.Float64,
        "ask_top10_qty": pl.Float64,
        "imbalance": pl.Float64,
        "spread_bps": pl.Float64,
    },
    "fng": {
        "timestamp": pl.Int64,
        "value": pl.Int64,
        "classification": pl.Utf8,
    },
    "onchain": {
        "timestamp": pl.Int64,
        "tx_count": pl.Int64,
        "hashrate": pl.Float64,
        "fees_usd": pl.Float64,
    },
```

- [ ] **Step 1.4: Run tests to verify pass**

Run: `uv run pytest tests/python/test_data_validate.py::TestExtendedSchemas -v`
Expected: PASS — all 6 tests green.

- [ ] **Step 1.5: Commit**

```bash
git add src/bt/data/schemas.py tests/python/test_data_validate.py
git commit -m "feat(schemas): add oi_hist, lsr×3, taker_ratio, depth_snap, fng, onchain"
```

---

## Task 2: Binance OI history

**Files:**
- Modify: `src/bt/data/sources/binance.py` — add `_fetch_oi_hist()`, register in `supported_types()` + `fetch()` dispatch
- Test: `tests/python/test_sources_binance_extensions.py` (new)

Endpoint: `GET https://fapi.binance.com/futures/data/openInterestHist`
Params: `symbol`, `period` (5m,15m,30m,1h,2h,4h,6h,12h,1d), `startTime`, `endTime`, `limit` (max 500)
Limit: 30 days history, 500 rows/call.

- [ ] **Step 2.1: Write failing test with mocked HTTP**

Create `tests/python/test_sources_binance_extensions.py`:

```python
"""Tests for new Binance fetch methods."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import polars as pl
import pytest

from bt.data.sources.binance import BinanceSource


@pytest.mark.asyncio
async def test_fetch_oi_hist_returns_canonical_schema():
    src = BinanceSource()
    fake_response = [
        {"symbol": "BTCUSDT", "sumOpenInterest": "10000.5",
         "sumOpenInterestValue": "500000000.0", "timestamp": 1700000000000},
        {"symbol": "BTCUSDT", "sumOpenInterest": "10100.0",
         "sumOpenInterestValue": "505000000.0", "timestamp": 1700000300000},
    ]
    with patch("bt.data.sources.binance.HttpClient") as MockHttp:
        instance = MockHttp.return_value
        instance.get = AsyncMock(return_value=fake_response)
        instance.close = AsyncMock()
        df = await src.fetch(
            pair="BTC-USDT", data_type="oi_hist", timeframe="5m",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
    assert df.columns == ["timestamp", "open_interest", "open_interest_value"]
    assert df["open_interest"].to_list() == [10000.5, 10100.0]


def test_oi_hist_in_supported_types():
    assert "oi_hist" in BinanceSource().supported_types()
```

- [ ] **Step 2.2: Run test to verify fail**

Run: `uv run pytest tests/python/test_sources_binance_extensions.py -v`
Expected: FAIL — `oi_hist` not in `supported_types()`, `_fetch_oi_hist` missing.

- [ ] **Step 2.3: Add `_fetch_oi_hist` to BinanceSource**

In `src/bt/data/sources/binance.py`, add inside `BinanceSource` class:

```python
async def _fetch_oi_hist(
    self, pair: str, timeframe: str, start: datetime, end: datetime,
) -> pl.DataFrame:
    """Fetch open interest history from Binance futures (max 30d, 500 rows/call)."""
    symbol = _symbol(pair)
    period = _timeframe_to_oi_period(timeframe)
    start_ms = _to_ms(start)
    end_ms = _to_ms(end)

    http = HttpClient(FUTURES_BASE_URL)
    all_rows = []
    current_start = start_ms

    try:
        while current_start < end_ms:
            params = {
                "symbol": symbol, "period": period,
                "startTime": current_start, "endTime": end_ms, "limit": 500,
            }
            resp = await http.get("/futures/data/openInterestHist", params=params)
            if not resp:
                break
            for r in resp:
                all_rows.append({
                    "timestamp": int(r["timestamp"]),
                    "open_interest": float(r["sumOpenInterest"]),
                    "open_interest_value": float(r["sumOpenInterestValue"]),
                })
            last_ts = int(resp[-1]["timestamp"])
            if last_ts <= current_start:
                break
            current_start = last_ts + 1
    finally:
        await http.close()

    if not all_rows:
        return pl.DataFrame(schema={
            "timestamp": pl.Int64, "open_interest": pl.Float64,
            "open_interest_value": pl.Float64,
        })
    return pl.DataFrame(all_rows).sort("timestamp")
```

Add helper at module bottom:

```python
def _timeframe_to_oi_period(tf: str) -> str:
    """Binance OI hist allowed periods: 5m,15m,30m,1h,2h,4h,6h,12h,1d."""
    allowed = {"5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}
    return tf if tf in allowed else "5m"
```

Update `supported_types()`:

```python
def supported_types(self) -> list[str]:
    return ["ohlcv", "funding", "oi", "oi_hist", "trades"]
```

Update `fetch()` dispatch — add branch:

```python
elif data_type == "oi_hist":
    return await self._fetch_oi_hist(pair, timeframe, start, end)
```

- [ ] **Step 2.4: Run test to verify pass**

Run: `uv run pytest tests/python/test_sources_binance_extensions.py -v`
Expected: PASS.

- [ ] **Step 2.5: Commit**

```bash
git add src/bt/data/sources/binance.py tests/python/test_sources_binance_extensions.py
git commit -m "feat(binance): add oi_hist fetcher (30d window, 500 rows/call)"
```

---

## Task 3: Binance LSR (3 variants)

**Files:**
- Modify: `src/bt/data/sources/binance.py` — add `_fetch_lsr()` shared, dispatch via `data_type`
- Test: `tests/python/test_sources_binance_extensions.py` (extend)

Endpoints:
- `/futures/data/topLongShortPositionRatio` (top trader by position value)
- `/futures/data/topLongShortAccountRatio` (top trader by account count)
- `/futures/data/globalLongShortAccountRatio` (all accounts)

All same shape: `{symbol, longShortRatio, longAccount, shortAccount, timestamp}`. Same `period` set as OI hist. 30-day limit.

- [ ] **Step 3.1: Write failing tests**

Append to `tests/python/test_sources_binance_extensions.py`:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("dtype,endpoint", [
    ("lsr_top_pos", "/futures/data/topLongShortPositionRatio"),
    ("lsr_top_acct", "/futures/data/topLongShortAccountRatio"),
    ("lsr_global", "/futures/data/globalLongShortAccountRatio"),
])
async def test_fetch_lsr_variants(dtype, endpoint):
    src = BinanceSource()
    fake = [
        {"symbol": "BTCUSDT", "longShortRatio": "1.5",
         "longAccount": "0.6", "shortAccount": "0.4", "timestamp": 1700000000000},
    ]
    captured_endpoint = {}
    async def fake_get(path, params=None):
        captured_endpoint["path"] = path
        return fake
    with patch("bt.data.sources.binance.HttpClient") as MockHttp:
        instance = MockHttp.return_value
        instance.get = fake_get
        instance.close = AsyncMock()
        df = await src.fetch(
            pair="BTC-USDT", data_type=dtype, timeframe="1h",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
    assert captured_endpoint["path"] == endpoint
    assert df.columns == ["timestamp", "long_short_ratio", "long_account", "short_account"]
    assert df["long_short_ratio"].to_list() == [1.5]


def test_lsr_in_supported_types():
    types = BinanceSource().supported_types()
    assert "lsr_top_pos" in types
    assert "lsr_top_acct" in types
    assert "lsr_global" in types
```

- [ ] **Step 3.2: Run to verify fail**

Run: `uv run pytest tests/python/test_sources_binance_extensions.py -v -k lsr`
Expected: FAIL on unsupported types.

- [ ] **Step 3.3: Implement `_fetch_lsr`**

Add to `BinanceSource`:

```python
_LSR_ENDPOINTS = {
    "lsr_top_pos": "/futures/data/topLongShortPositionRatio",
    "lsr_top_acct": "/futures/data/topLongShortAccountRatio",
    "lsr_global": "/futures/data/globalLongShortAccountRatio",
}

async def _fetch_lsr(
    self, pair: str, data_type: str, timeframe: str,
    start: datetime, end: datetime,
) -> pl.DataFrame:
    """Fetch long/short ratio (max 30d, 500 rows/call)."""
    endpoint = self._LSR_ENDPOINTS[data_type]
    symbol = _symbol(pair)
    period = _timeframe_to_oi_period(timeframe)
    start_ms = _to_ms(start)
    end_ms = _to_ms(end)

    http = HttpClient(FUTURES_BASE_URL)
    all_rows = []
    current_start = start_ms

    try:
        while current_start < end_ms:
            params = {
                "symbol": symbol, "period": period,
                "startTime": current_start, "endTime": end_ms, "limit": 500,
            }
            resp = await http.get(endpoint, params=params)
            if not resp:
                break
            for r in resp:
                all_rows.append({
                    "timestamp": int(r["timestamp"]),
                    "long_short_ratio": float(r["longShortRatio"]),
                    "long_account": float(r["longAccount"]),
                    "short_account": float(r["shortAccount"]),
                })
            last_ts = int(resp[-1]["timestamp"])
            if last_ts <= current_start:
                break
            current_start = last_ts + 1
    finally:
        await http.close()

    if not all_rows:
        return pl.DataFrame(schema={
            "timestamp": pl.Int64, "long_short_ratio": pl.Float64,
            "long_account": pl.Float64, "short_account": pl.Float64,
        })
    return pl.DataFrame(all_rows).sort("timestamp")
```

Update `supported_types()`:

```python
def supported_types(self) -> list[str]:
    return [
        "ohlcv", "funding", "oi", "oi_hist", "trades",
        "lsr_top_pos", "lsr_top_acct", "lsr_global",
    ]
```

Add to `fetch()` dispatch:

```python
elif data_type in self._LSR_ENDPOINTS:
    return await self._fetch_lsr(pair, data_type, timeframe, start, end)
```

- [ ] **Step 3.4: Run to verify pass**

Run: `uv run pytest tests/python/test_sources_binance_extensions.py -v -k lsr`
Expected: PASS.

- [ ] **Step 3.5: Commit**

```bash
git add src/bt/data/sources/binance.py tests/python/test_sources_binance_extensions.py
git commit -m "feat(binance): add LSR fetchers (top pos/acct, global)"
```

---

## Task 4: Binance taker buy/sell ratio

**Files:**
- Modify: `src/bt/data/sources/binance.py` — add `_fetch_taker_ratio()`
- Test: `tests/python/test_sources_binance_extensions.py` (extend)

Endpoint: `GET /futures/data/takerlongshortRatio`
Response: `{buySellRatio, buyVol, sellVol, timestamp}`. Same period set, 30d limit.

- [ ] **Step 4.1: Write failing test**

Append:

```python
@pytest.mark.asyncio
async def test_fetch_taker_ratio():
    src = BinanceSource()
    fake = [{
        "buySellRatio": "1.2", "buyVol": "120.0",
        "sellVol": "100.0", "timestamp": 1700000000000,
    }]
    with patch("bt.data.sources.binance.HttpClient") as MockHttp:
        instance = MockHttp.return_value
        instance.get = AsyncMock(return_value=fake)
        instance.close = AsyncMock()
        df = await src.fetch(
            pair="BTC-USDT", data_type="taker_ratio", timeframe="5m",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
    assert df.columns == ["timestamp", "buy_sell_ratio", "buy_vol", "sell_vol"]
    assert df["buy_sell_ratio"].to_list() == [1.2]
```

- [ ] **Step 4.2: Run fail**

Run: `uv run pytest tests/python/test_sources_binance_extensions.py::test_fetch_taker_ratio -v`
Expected: FAIL.

- [ ] **Step 4.3: Implement**

Add to `BinanceSource`:

```python
async def _fetch_taker_ratio(
    self, pair: str, timeframe: str, start: datetime, end: datetime,
) -> pl.DataFrame:
    """Fetch taker buy/sell ratio (max 30d, 500 rows/call)."""
    symbol = _symbol(pair)
    period = _timeframe_to_oi_period(timeframe)
    start_ms = _to_ms(start)
    end_ms = _to_ms(end)

    http = HttpClient(FUTURES_BASE_URL)
    all_rows = []
    current_start = start_ms

    try:
        while current_start < end_ms:
            params = {
                "symbol": symbol, "period": period,
                "startTime": current_start, "endTime": end_ms, "limit": 500,
            }
            resp = await http.get("/futures/data/takerlongshortRatio", params=params)
            if not resp:
                break
            for r in resp:
                all_rows.append({
                    "timestamp": int(r["timestamp"]),
                    "buy_sell_ratio": float(r["buySellRatio"]),
                    "buy_vol": float(r["buyVol"]),
                    "sell_vol": float(r["sellVol"]),
                })
            last_ts = int(resp[-1]["timestamp"])
            if last_ts <= current_start:
                break
            current_start = last_ts + 1
    finally:
        await http.close()

    if not all_rows:
        return pl.DataFrame(schema={
            "timestamp": pl.Int64, "buy_sell_ratio": pl.Float64,
            "buy_vol": pl.Float64, "sell_vol": pl.Float64,
        })
    return pl.DataFrame(all_rows).sort("timestamp")
```

Update `supported_types()` adding `"taker_ratio"`. Update dispatch:

```python
elif data_type == "taker_ratio":
    return await self._fetch_taker_ratio(pair, timeframe, start, end)
```

- [ ] **Step 4.4: Run pass**

Run: `uv run pytest tests/python/test_sources_binance_extensions.py::test_fetch_taker_ratio -v`
Expected: PASS.

- [ ] **Step 4.5: Commit**

```bash
git add src/bt/data/sources/binance.py tests/python/test_sources_binance_extensions.py
git commit -m "feat(binance): add taker_ratio fetcher"
```

---

## Task 5: Binance depth snapshot

**Files:**
- Modify: `src/bt/data/sources/binance.py` — add `_fetch_depth_snap()`
- Test: `tests/python/test_sources_binance_extensions.py` (extend)

Use existing `client.get_order_book(symbol, limit=1000)` from adapter. Aggregate to single-row snapshot with totals + imbalance + spread. Snapshot is current-time only (no historical) — `start`/`end` ignored, timestamp = wall clock.

- [ ] **Step 5.1: Write failing test**

Append:

```python
@pytest.mark.asyncio
async def test_fetch_depth_snap_aggregates_book():
    src = BinanceSource()
    fake_book = {
        "lastUpdateId": 1,
        "E": 1700000000000,
        "T": 1700000000000,
        "bids": [["100.0", "1.0"], ["99.5", "2.0"], ["99.0", "3.0"]],
        "asks": [["101.0", "1.5"], ["101.5", "2.5"], ["102.0", "1.0"]],
    }
    with patch("bt.data.sources.binance.BinanceFuturesUssdtRestClient") as MockClient:
        instance = MockClient.return_value
        instance.get_order_book = AsyncMock(return_value=fake_book)
        with patch("bt.data.sources.binance.HttpClient") as MockHttp:
            MockHttp.return_value.close = AsyncMock()
            df = await src.fetch(
                pair="BTC-USDT", data_type="depth_snap", timeframe="1m",
                start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                end=datetime(2024, 1, 2, tzinfo=timezone.utc),
            )
    assert len(df) == 1
    assert df["bid_total_qty"][0] == pytest.approx(6.0)
    assert df["ask_total_qty"][0] == pytest.approx(5.0)
    assert df["imbalance"][0] == pytest.approx((6.0 - 5.0) / (6.0 + 5.0))
    assert df["spread_bps"][0] == pytest.approx(((101.0 - 100.0) / 100.5) * 10_000)
```

- [ ] **Step 5.2: Run fail**

Run: `uv run pytest tests/python/test_sources_binance_extensions.py::test_fetch_depth_snap_aggregates_book -v`
Expected: FAIL.

- [ ] **Step 5.3: Implement**

Add to `BinanceSource`:

```python
async def _fetch_depth_snap(self, pair: str) -> pl.DataFrame:
    """Snapshot of current orderbook (top 1000 levels)."""
    symbol = _symbol(pair)
    http = HttpClient(FUTURES_BASE_URL)
    client = BinanceFuturesUssdtRestClient(http)
    try:
        book = await client.get_order_book(symbol=symbol, limit=1000)
    finally:
        await http.close()

    bids = book.get("bids", [])
    asks = book.get("asks", [])
    if not bids or not asks:
        return pl.DataFrame(schema={
            "timestamp": pl.Int64, "bid_total_qty": pl.Float64,
            "ask_total_qty": pl.Float64, "bid_top10_qty": pl.Float64,
            "ask_top10_qty": pl.Float64, "imbalance": pl.Float64,
            "spread_bps": pl.Float64,
        })

    bid_qty = [float(b[1]) for b in bids]
    ask_qty = [float(a[1]) for a in asks]
    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    bid_total = sum(bid_qty)
    ask_total = sum(ask_qty)
    bid_top10 = sum(bid_qty[:10])
    ask_top10 = sum(ask_qty[:10])
    mid = (best_bid + best_ask) / 2
    imbalance = (bid_total - ask_total) / (bid_total + ask_total) if (bid_total + ask_total) > 0 else 0.0
    spread_bps = ((best_ask - best_bid) / mid) * 10_000 if mid > 0 else 0.0
    ts = int(book.get("E") or book.get("T") or 0)

    return pl.DataFrame([{
        "timestamp": ts, "bid_total_qty": bid_total, "ask_total_qty": ask_total,
        "bid_top10_qty": bid_top10, "ask_top10_qty": ask_top10,
        "imbalance": imbalance, "spread_bps": spread_bps,
    }])
```

Update `supported_types()` adding `"depth_snap"`. Update dispatch:

```python
elif data_type == "depth_snap":
    return await self._fetch_depth_snap(pair)
```

- [ ] **Step 5.4: Run pass**

Run: `uv run pytest tests/python/test_sources_binance_extensions.py::test_fetch_depth_snap_aggregates_book -v`
Expected: PASS.

- [ ] **Step 5.5: Commit**

```bash
git add src/bt/data/sources/binance.py tests/python/test_sources_binance_extensions.py
git commit -m "feat(binance): add depth_snap (orderbook snapshot with imbalance + spread)"
```

---

## Task 6: FX source (USD/KRW)

**Files:**
- Create: `src/bt/data/sources/fx.py`
- Modify: `src/bt/data/fetch.py:17-22` — register `"fx"` in `SOURCE_REGISTRY`
- Test: `tests/python/test_sources_fx.py`

Two providers:
- Primary: ExchangeRate-API public endpoint `https://api.exchangerate-api.com/v4/latest/USD` (current-only, no key)
- Historical fallback: Upbit USDT-KRW pair (works as USD/KRW proxy since USDT≈USD)

Strategy: if `start == end` (or both today), use ExchangeRate-API; else delegate to Upbit USDT-KRW OHLCV via existing UpbitSource and reshape columns.

- [ ] **Step 6.1: Write failing test**

Create `tests/python/test_sources_fx.py`:

```python
"""Tests for FX source."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import polars as pl
import pytest


def test_fx_supported_types():
    from bt.data.sources.fx import FxSource
    assert FxSource().supported_types() == ["fx"]


@pytest.mark.asyncio
async def test_fx_current_uses_exchangerate_api():
    from bt.data.sources.fx import FxSource
    fake_resp = {"rates": {"KRW": 1350.5}, "time_last_updated": 1700000000}
    with patch("bt.data.sources.fx.HttpClient") as MockHttp:
        instance = MockHttp.return_value
        instance.get = AsyncMock(return_value=fake_resp)
        instance.close = AsyncMock()
        src = FxSource()
        df = await src.fetch(
            pair="USD-KRW", data_type="fx", timeframe="1d",
            start=datetime(2026, 5, 2, tzinfo=timezone.utc),
            end=datetime(2026, 5, 2, tzinfo=timezone.utc),
        )
    assert df.columns == ["timestamp", "pair", "rate"]
    assert df["rate"][0] == pytest.approx(1350.5)
    assert df["pair"][0] == "USD-KRW"


@pytest.mark.asyncio
async def test_fx_historical_uses_upbit_usdt_proxy():
    from bt.data.sources.fx import FxSource
    fake_upbit_df = pl.DataFrame({
        "timestamp": [1700000000000, 1700086400000],
        "open": [1340.0, 1345.0], "high": [1352.0, 1355.0],
        "low": [1338.0, 1342.0], "close": [1350.0, 1352.0],
        "volume": [10.0, 20.0],
    })
    with patch("bt.data.sources.fx.UpbitSource") as MockUpbit:
        instance = MockUpbit.return_value
        instance.fetch = AsyncMock(return_value=fake_upbit_df)
        src = FxSource()
        df = await src.fetch(
            pair="USD-KRW", data_type="fx", timeframe="1d",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
        )
    assert df.columns == ["timestamp", "pair", "rate"]
    assert len(df) == 2
    assert df["rate"][0] == 1350.0  # uses close
```

- [ ] **Step 6.2: Run fail**

Run: `uv run pytest tests/python/test_sources_fx.py -v`
Expected: FAIL — module missing.

- [ ] **Step 6.3: Implement FxSource**

Create `src/bt/data/sources/fx.py`:

```python
"""FX rate source — USD/KRW etc.

Current-day: ExchangeRate-API (free, no key).
Historical: Upbit USDT-KRW spot OHLCV close as USD proxy.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import polars as pl
from core.http.client import HttpClient

from bt.data.sources.upbit import UpbitSource


_FX_BASE = "https://api.exchangerate-api.com"


class FxSource:
    def supported_types(self) -> list[str]:
        return ["fx"]

    async def fetch(
        self, pair: str, data_type: str, timeframe: str,
        start: datetime, end: datetime,
    ) -> pl.DataFrame:
        if data_type != "fx":
            raise ValueError(f"FxSource does not support: {data_type}")

        base, quote = pair.split("-")
        now = datetime.now(timezone.utc)
        is_current = (now - start).total_seconds() < 86_400 * 2

        if is_current and base == "USD":
            return await self._fetch_current(pair, quote)
        return await self._fetch_historical(pair, base, quote, timeframe, start, end)

    async def _fetch_current(self, pair: str, quote: str) -> pl.DataFrame:
        http = HttpClient(_FX_BASE)
        try:
            resp = await http.get(f"/v4/latest/USD")
        finally:
            await http.close()
        rate = float(resp["rates"][quote])
        ts = int(resp.get("time_last_updated", 0)) * 1000
        return pl.DataFrame([{"timestamp": ts, "pair": pair, "rate": rate}])

    async def _fetch_historical(
        self, pair: str, base: str, quote: str, timeframe: str,
        start: datetime, end: datetime,
    ) -> pl.DataFrame:
        if not (base == "USD" and quote == "KRW"):
            raise ValueError(
                f"Historical FX only supports USD-KRW (proxy via Upbit USDT-KRW); got {pair}"
            )
        upbit = UpbitSource()
        df = await upbit.fetch(
            pair="USDT-KRW", data_type="ohlcv",
            timeframe=timeframe, start=start, end=end,
        )
        if len(df) == 0:
            return pl.DataFrame(schema={
                "timestamp": pl.Int64, "pair": pl.Utf8, "rate": pl.Float64,
            })
        return df.select([
            pl.col("timestamp"),
            pl.lit(pair).alias("pair"),
            pl.col("close").alias("rate"),
        ])
```

Modify `src/bt/data/fetch.py:17-22` to add:

```python
SOURCE_REGISTRY: dict[str, str] = {
    "binance": "bt.data.sources.binance:BinanceSource",
    "binance_spot": "bt.data.sources.binance:BinanceSpotSource",
    "bithumb": "bt.data.sources.bithumb:BithumbSource",
    "upbit": "bt.data.sources.upbit:UpbitSource",
    "fx": "bt.data.sources.fx:FxSource",
}
```

- [ ] **Step 6.4: Run pass**

Run: `uv run pytest tests/python/test_sources_fx.py -v`
Expected: PASS.

- [ ] **Step 6.5: Commit**

```bash
git add src/bt/data/sources/fx.py src/bt/data/fetch.py tests/python/test_sources_fx.py
git commit -m "feat(fx): USD-KRW source (ExchangeRate-API current + Upbit USDT proxy historical)"
```

---

## Task 7: Fear & Greed Index source

**Files:**
- Create: `src/bt/data/sources/fng.py`
- Modify: `src/bt/data/fetch.py` — register `"fng"`
- Test: `tests/python/test_sources_fng.py`

Endpoint: `GET https://api.alternative.me/fng/?limit=0&format=json`
Response: `{data: [{value, value_classification, timestamp, time_until_update}, ...]}`
`limit=0` returns full history (~daily granularity since Feb 2018). Timestamp is seconds, must convert to ms.

- [ ] **Step 7.1: Write failing test**

Create `tests/python/test_sources_fng.py`:

```python
"""Tests for Fear & Greed Index source."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest


def test_fng_supported_types():
    from bt.data.sources.fng import FngSource
    assert FngSource().supported_types() == ["fng"]


@pytest.mark.asyncio
async def test_fng_fetches_and_filters():
    from bt.data.sources.fng import FngSource
    fake = {"data": [
        {"value": "55", "value_classification": "Greed", "timestamp": "1704067200"},
        {"value": "30", "value_classification": "Fear", "timestamp": "1703980800"},
        {"value": "70", "value_classification": "Greed", "timestamp": "1700000000"},
    ]}
    with patch("bt.data.sources.fng.HttpClient") as MockHttp:
        instance = MockHttp.return_value
        instance.get = AsyncMock(return_value=fake)
        instance.close = AsyncMock()
        src = FngSource()
        df = await src.fetch(
            pair="BTC", data_type="fng", timeframe="1d",
            start=datetime(2023, 12, 31, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
    assert df.columns == ["timestamp", "value", "classification"]
    assert len(df) == 2
    assert df.sort("timestamp")["value"].to_list() == [30, 55]
    assert df["timestamp"].dtype.is_integer()
    assert df["timestamp"][0] >= 1_700_000_000_000
```

- [ ] **Step 7.2: Run fail**

Run: `uv run pytest tests/python/test_sources_fng.py -v`
Expected: FAIL.

- [ ] **Step 7.3: Implement FngSource**

Create `src/bt/data/sources/fng.py`:

```python
"""Fear & Greed Index source — alternative.me public API."""
from __future__ import annotations

from datetime import datetime

import polars as pl
from core.http.client import HttpClient


_FNG_BASE = "https://api.alternative.me"


class FngSource:
    def supported_types(self) -> list[str]:
        return ["fng"]

    async def fetch(
        self, pair: str, data_type: str, timeframe: str,
        start: datetime, end: datetime,
    ) -> pl.DataFrame:
        if data_type != "fng":
            raise ValueError(f"FngSource does not support: {data_type}")

        http = HttpClient(_FNG_BASE)
        try:
            resp = await http.get("/fng/", params={"limit": 0, "format": "json"})
        finally:
            await http.close()

        rows = []
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        for r in resp.get("data", []):
            ts_ms = int(r["timestamp"]) * 1000
            if start_ms <= ts_ms <= end_ms:
                rows.append({
                    "timestamp": ts_ms,
                    "value": int(r["value"]),
                    "classification": str(r["value_classification"]),
                })

        if not rows:
            return pl.DataFrame(schema={
                "timestamp": pl.Int64, "value": pl.Int64, "classification": pl.Utf8,
            })
        return pl.DataFrame(rows).sort("timestamp")
```

Add to `SOURCE_REGISTRY` in `src/bt/data/fetch.py`:

```python
    "fng": "bt.data.sources.fng:FngSource",
```

- [ ] **Step 7.4: Run pass**

Run: `uv run pytest tests/python/test_sources_fng.py -v`
Expected: PASS.

- [ ] **Step 7.5: Commit**

```bash
git add src/bt/data/sources/fng.py src/bt/data/fetch.py tests/python/test_sources_fng.py
git commit -m "feat(fng): Fear & Greed Index source via alternative.me"
```

---

## Task 8: On-chain BTC source

**Files:**
- Create: `src/bt/data/sources/onchain.py`
- Modify: `src/bt/data/fetch.py` — register `"onchain"`
- Test: `tests/python/test_sources_onchain.py`

blockchain.info charts API:
- `GET https://api.blockchain.info/charts/n-transactions?timespan=all&format=json` → tx count
- `GET https://api.blockchain.info/charts/hash-rate?timespan=all&format=json` → hashrate (TH/s)
- `GET https://api.blockchain.info/charts/transaction-fees-usd?timespan=all&format=json` → fees in USD

Each returns `{values: [{x: epoch_seconds, y: value}, ...]}`. Daily granularity.

Strategy: fetch 3 charts in parallel via `asyncio.gather`, join on `timestamp`, filter by start/end.

- [ ] **Step 8.1: Write failing test**

Create `tests/python/test_sources_onchain.py`:

```python
"""Tests for on-chain BTC source."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest


def test_onchain_supported_types():
    from bt.data.sources.onchain import OnchainSource
    assert OnchainSource().supported_types() == ["onchain"]


@pytest.mark.asyncio
async def test_onchain_joins_three_charts():
    from bt.data.sources.onchain import OnchainSource
    chart_responses = {
        "n-transactions": {"values": [
            {"x": 1704067200, "y": 350000.0},
            {"x": 1704153600, "y": 360000.0},
        ]},
        "hash-rate": {"values": [
            {"x": 1704067200, "y": 500_000_000.0},
            {"x": 1704153600, "y": 510_000_000.0},
        ]},
        "transaction-fees-usd": {"values": [
            {"x": 1704067200, "y": 100000.0},
            {"x": 1704153600, "y": 120000.0},
        ]},
    }

    async def fake_get(path, params=None):
        chart = path.rsplit("/", 1)[-1]
        return chart_responses[chart]

    with patch("bt.data.sources.onchain.HttpClient") as MockHttp:
        instance = MockHttp.return_value
        instance.get = fake_get
        instance.close = AsyncMock()
        src = OnchainSource()
        df = await src.fetch(
            pair="BTC", data_type="onchain", timeframe="1d",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
        )
    assert set(df.columns) == {"timestamp", "tx_count", "hashrate", "fees_usd"}
    assert len(df) == 2
    row = df.sort("timestamp").row(0, named=True)
    assert row["tx_count"] == 350000
    assert row["hashrate"] == 500_000_000.0
    assert row["fees_usd"] == 100000.0
```

- [ ] **Step 8.2: Run fail**

Run: `uv run pytest tests/python/test_sources_onchain.py -v`
Expected: FAIL.

- [ ] **Step 8.3: Implement OnchainSource**

Create `src/bt/data/sources/onchain.py`:

```python
"""On-chain BTC source — blockchain.info charts API."""
from __future__ import annotations

import asyncio
from datetime import datetime

import polars as pl
from core.http.client import HttpClient


_ONCHAIN_BASE = "https://api.blockchain.info"
_CHARTS = ("n-transactions", "hash-rate", "transaction-fees-usd")
_COL_MAP = {
    "n-transactions": "tx_count",
    "hash-rate": "hashrate",
    "transaction-fees-usd": "fees_usd",
}


class OnchainSource:
    def supported_types(self) -> list[str]:
        return ["onchain"]

    async def fetch(
        self, pair: str, data_type: str, timeframe: str,
        start: datetime, end: datetime,
    ) -> pl.DataFrame:
        if data_type != "onchain":
            raise ValueError(f"OnchainSource does not support: {data_type}")

        http = HttpClient(_ONCHAIN_BASE)
        try:
            results = await asyncio.gather(*[
                http.get(f"/charts/{c}", params={"timespan": "all", "format": "json"})
                for c in _CHARTS
            ])
        finally:
            await http.close()

        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        frames = []
        for chart, resp in zip(_CHARTS, results):
            col = _COL_MAP[chart]
            rows = []
            for v in resp.get("values", []):
                ts_ms = int(v["x"]) * 1000
                if start_ms <= ts_ms <= end_ms:
                    rows.append({"timestamp": ts_ms, col: float(v["y"])})
            if rows:
                frames.append(pl.DataFrame(rows))

        if not frames:
            return pl.DataFrame(schema={
                "timestamp": pl.Int64, "tx_count": pl.Int64,
                "hashrate": pl.Float64, "fees_usd": pl.Float64,
            })

        df = frames[0]
        for f in frames[1:]:
            df = df.join(f, on="timestamp", how="outer_coalesce")
        if "tx_count" in df.columns:
            df = df.with_columns(pl.col("tx_count").cast(pl.Int64))
        return df.sort("timestamp")
```

Add to `SOURCE_REGISTRY`:

```python
    "onchain": "bt.data.sources.onchain:OnchainSource",
```

- [ ] **Step 8.4: Run pass**

Run: `uv run pytest tests/python/test_sources_onchain.py -v`
Expected: PASS.

- [ ] **Step 8.5: Commit**

```bash
git add src/bt/data/sources/onchain.py src/bt/data/fetch.py tests/python/test_sources_onchain.py
git commit -m "feat(onchain): blockchain.info source (tx_count, hashrate, fees_usd)"
```

---

## Task 9: Liquidations WebSocket collector daemon

**Files:**
- Create: `src/bt/data/collect_liq.py`
- Modify: `src/bt/cli.py` — add `bt collect-liq` command
- Test: `tests/python/test_collect_liq.py`

WS endpoint: `wss://fstream.binance.com/ws/!forceOrder@arr` (all-symbols liquidation stream).
Each message:
```json
{"e":"forceOrder","E":1700000000000,"o":{"s":"BTCUSDT","S":"SELL","o":"LIMIT","f":"IOC","q":"0.5","p":"40000.0","ap":"40000.0","X":"FILLED","l":"0.5","z":"0.5","T":1700000000000}}
```
- `S = SELL` → long was liquidated (long_liq_usd += q*p)
- `S = BUY` → short was liquidated (short_liq_usd += q*p)
- Bin to 1-minute buckets. Append to daily Parquet at `data/raw/binance/{pair}/liquidations/1m/YYYY-MM-DD.parquet`.

Daemon writes one row per (minute, symbol). Flush every 60s. Catch reconnect on `ConnectionClosed`.

- [ ] **Step 9.1: Write failing test (collector aggregation logic)**

Create `tests/python/test_collect_liq.py`:

```python
"""Tests for liquidations WS collector."""
from pathlib import Path

import polars as pl


def test_aggregate_liquidation_messages():
    from bt.data.collect_liq import aggregate_messages

    msgs = [
        # SELL = long liquidated, $20k
        {"e": "forceOrder", "E": 1700000010000,
         "o": {"s": "BTCUSDT", "S": "SELL", "q": "0.5", "ap": "40000.0",
               "T": 1700000010000}},
        # BUY = short liquidated, $30k
        {"e": "forceOrder", "E": 1700000020000,
         "o": {"s": "BTCUSDT", "S": "BUY", "q": "0.75", "ap": "40000.0",
               "T": 1700000020000}},
        # Same minute, another long liq, $10k
        {"e": "forceOrder", "E": 1700000050000,
         "o": {"s": "BTCUSDT", "S": "SELL", "q": "0.25", "ap": "40000.0",
               "T": 1700000050000}},
        # Next minute
        {"e": "forceOrder", "E": 1700000070000,
         "o": {"s": "ETHUSDT", "S": "SELL", "q": "10", "ap": "2000.0",
               "T": 1700000070000}},
    ]
    df = aggregate_messages(msgs)
    btc_min1 = df.filter(
        (pl.col("symbol") == "BTCUSDT") & (pl.col("timestamp") == 1700000000000)
    ).row(0, named=True)
    assert btc_min1["long_liq_usd"] == 30000.0  # 20k + 10k
    assert btc_min1["short_liq_usd"] == 30000.0
    eth_min2 = df.filter(pl.col("symbol") == "ETHUSDT").row(0, named=True)
    assert eth_min2["long_liq_usd"] == 20000.0


def test_partition_path_per_day(tmp_path):
    from bt.data.collect_liq import partition_path
    p = partition_path(tmp_path, "BTCUSDT", 1700000000000)
    assert p.parent.name == "1m"
    assert p.parent.parent.name == "liquidations"
    assert "BTC-USDT" in str(p) or "BTCUSDT" in str(p)
    assert p.name.endswith(".parquet")
```

- [ ] **Step 9.2: Run fail**

Run: `uv run pytest tests/python/test_collect_liq.py -v`
Expected: FAIL — module missing.

- [ ] **Step 9.3: Implement collector**

Create `src/bt/data/collect_liq.py`:

```python
"""Binance Futures liquidation stream collector.

Live-only. Binance removed historical liquidations REST in 2021.
Streams !forceOrder@arr WS, aggregates to 1-minute buckets, appends to daily Parquet.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import websockets

WS_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"
BUCKET_MS = 60_000  # 1-minute


def aggregate_messages(msgs: list[dict]) -> pl.DataFrame:
    """Aggregate raw forceOrder messages to 1-min (symbol, timestamp) buckets."""
    rows = []
    for m in msgs:
        o = m.get("o", {})
        ts = int(o.get("T") or m.get("E", 0))
        bucket = (ts // BUCKET_MS) * BUCKET_MS
        symbol = str(o.get("s", ""))
        side = str(o.get("S", ""))
        qty = float(o.get("q", 0))
        price = float(o.get("ap") or o.get("p", 0))
        usd = qty * price
        rows.append({
            "timestamp": bucket, "symbol": symbol,
            "long_liq_usd": usd if side == "SELL" else 0.0,
            "short_liq_usd": usd if side == "BUY" else 0.0,
        })
    if not rows:
        return pl.DataFrame(schema={
            "timestamp": pl.Int64, "symbol": pl.Utf8,
            "long_liq_usd": pl.Float64, "short_liq_usd": pl.Float64,
        })
    return pl.DataFrame(rows).group_by(["timestamp", "symbol"]).agg([
        pl.col("long_liq_usd").sum(),
        pl.col("short_liq_usd").sum(),
    ]).sort(["timestamp", "symbol"])


def partition_path(root: Path, symbol: str, ts_ms: int) -> Path:
    """data/raw/binance/{pair}/liquidations/1m/YYYY-MM-DD.parquet"""
    pair = symbol.replace("USDT", "-USDT") if "USDT" in symbol and "-" not in symbol else symbol
    day = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    return root / "data" / "raw" / "binance" / pair / "liquidations" / "1m" / f"{day}.parquet"


async def _writer_loop(buffer: list[dict], root: Path, flush_interval: float = 60.0):
    """Periodically drain buffer to disk."""
    while True:
        await asyncio.sleep(flush_interval)
        if not buffer:
            continue
        msgs = buffer.copy()
        buffer.clear()
        df = aggregate_messages(msgs)
        for sym in df["symbol"].unique().to_list():
            sym_df = df.filter(pl.col("symbol") == sym).drop("symbol")
            for ts in sym_df["timestamp"].to_list():
                path = partition_path(root, sym, ts)
                path.parent.mkdir(parents=True, exist_ok=True)
                day_df = sym_df.filter(pl.col("timestamp") == ts)
                if path.exists():
                    existing = pl.read_parquet(path)
                    merged = pl.concat([existing, day_df]).group_by("timestamp").agg([
                        pl.col("long_liq_usd").sum(),
                        pl.col("short_liq_usd").sum(),
                    ]).sort("timestamp")
                    merged.write_parquet(path)
                else:
                    day_df.write_parquet(path)


async def collect(root: Path, flush_interval: float = 60.0) -> None:
    """Main entry — connect, buffer, flush. Reconnect on disconnect."""
    buffer: list[dict] = []
    writer = asyncio.create_task(_writer_loop(buffer, root, flush_interval))
    try:
        while True:
            try:
                async with websockets.connect(WS_URL) as ws:
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                            if msg.get("e") == "forceOrder":
                                buffer.append(msg)
                        except (json.JSONDecodeError, KeyError):
                            continue
            except (websockets.ConnectionClosed, OSError):
                await asyncio.sleep(2.0)
                continue
    finally:
        writer.cancel()
```

Modify `src/bt/cli.py`. After the `bt data import` block (around line 89), add:

```python
# ── bt collect-liq ────────────────────────────────────────────────────────────

@app.command("collect-liq")
def collect_liq(
    flush: float = typer.Option(60.0, help="Flush interval in seconds"),
) -> None:
    """Stream Binance liquidations and append to daily Parquet partitions.

    Long-running daemon. Run under a process supervisor (systemd, tmux, etc).
    Live-only — Binance removed historical liquidations REST in 2021.
    """
    import asyncio
    from bt.config import find_project_root
    from bt.data.collect_liq import collect

    typer.echo("Starting liquidation collector. Ctrl-C to stop.")
    asyncio.run(collect(find_project_root(), flush_interval=flush))
```

Add to `pyproject.toml` deps if missing: `websockets>=12.0`. (Verify before running tests.)

- [ ] **Step 9.4: Install websockets if missing**

Run: `uv pip install "websockets>=12.0"`
Verify: `uv pip list | grep websockets`

- [ ] **Step 9.5: Run pass**

Run: `uv run pytest tests/python/test_collect_liq.py -v`
Expected: PASS.

- [ ] **Step 9.6: Smoke test the daemon (manual, 10 sec)**

Run in one terminal:
```bash
timeout 10 uv run bt collect-liq --flush 5 || true
ls data/raw/binance/*/liquidations/1m/*.parquet 2>/dev/null | head
```
Expected: at least one parquet file created if any liquidation occurred during the 10-sec window. If markets are dead, no file is fine — verify the process started without import errors.

- [ ] **Step 9.7: Commit**

```bash
git add src/bt/data/collect_liq.py src/bt/cli.py tests/python/test_collect_liq.py pyproject.toml
git commit -m "feat(liq): live Binance liquidation stream collector daemon"
```

---

## Task 10: Fix output path layout for timeframed types

**Files:**
- Modify: `src/bt/data/fetch.py:83-95` — apply `{timeframe}` segment to all timeframed types
- Test: `tests/python/test_fetch_paths.py`

Current `fetch_data` only adds `{timeframe}` for `ohlcv`. Per `docs/architecture.md:177-192`, every timeframed type (ohlcv, trades, oi_hist, lsr_*, taker_ratio) should live under `{type}/{timeframe}/`. Untimeframed types (funding, oi snap, depth_snap, fng, onchain, fx-current) stay flat at `{type}/`.

- [ ] **Step 10.1: Write failing tests**

Create `tests/python/test_fetch_paths.py`:

```python
"""Tests for fetch output path layout."""
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest


def _stub_source(df):
    class S:
        def supported_types(self):
            return [
                "ohlcv", "funding", "oi", "oi_hist", "trades",
                "lsr_top_pos", "taker_ratio", "depth_snap", "fng", "onchain",
            ]
        async def fetch(self, **kw):
            return df
    return S()


@pytest.mark.parametrize("dtype,timeframe,expect_tf_segment", [
    ("ohlcv", "1h", True),
    ("trades", "5m", True),
    ("oi_hist", "5m", True),
    ("lsr_top_pos", "1h", True),
    ("taker_ratio", "1h", True),
    ("funding", "1h", False),
    ("oi", "1h", False),
    ("depth_snap", "1m", False),
    ("fng", "1d", False),
    ("onchain", "1d", False),
])
def test_fetch_path_layout(tmp_path, dtype, timeframe, expect_tf_segment):
    from bt.data import fetch as fetch_mod
    if dtype == "ohlcv":
        df = pl.DataFrame({
            "timestamp": [1000], "open": [1.0], "high": [2.0], "low": [0.5],
            "close": [1.5], "volume": [100.0],
        })
    else:
        from bt.data.schemas import SCHEMAS
        df = pl.DataFrame({c: [0] if d == pl.Int64 else [0.0] if d == pl.Float64 else ["x"]
                           for c, d in SCHEMAS[dtype].items()})

    with patch.object(fetch_mod, "_get_source", return_value=_stub_source(df)), \
         patch.object(fetch_mod, "find_project_root", return_value=tmp_path), \
         patch.object(fetch_mod, "validate_dataframe",
                      return_value={"valid": True, "errors": [], "warnings": [], "rows": 1}):
        result = fetch_mod.fetch_data(
            pair="BTC-USDT", source="binance", data_type=dtype,
            timeframe=timeframe, start="2024-01-01", end="2024-01-02",
        )
    path = Path(result["path"])
    parts = path.parts
    if expect_tf_segment:
        assert timeframe in parts, f"{dtype} should include {timeframe} in {parts}"
    else:
        assert timeframe not in parts[parts.index(dtype):], (
            f"{dtype} should NOT include {timeframe} after type segment in {parts}"
        )
```

- [ ] **Step 10.2: Run fail**

Run: `uv run pytest tests/python/test_fetch_paths.py -v`
Expected: most params FAIL — current code only special-cases `ohlcv`.

- [ ] **Step 10.3: Update path logic in `fetch_data()`**

In `src/bt/data/fetch.py`, replace lines 83-95:

```python
TIMEFRAMED_TYPES = frozenset({
    "ohlcv", "trades", "oi_hist",
    "lsr_top_pos", "lsr_top_acct", "lsr_global", "taker_ratio",
})

# ... inside fetch_data, replace the path block:
    if output:
        out_path = Path(output)
    else:
        root = find_project_root()
        pair_dir = pair.replace("/", "-")
        base = root / "data" / "raw" / source / pair_dir / data_type
        if data_type in TIMEFRAMED_TYPES:
            out_path = base / timeframe / "data.parquet"
        else:
            out_path = base / "data.parquet"
```

(Place `TIMEFRAMED_TYPES` near top of module after imports.)

- [ ] **Step 10.4: Run pass**

Run: `uv run pytest tests/python/test_fetch_paths.py -v`
Expected: all params PASS.

- [ ] **Step 10.5: Verify existing OHLCV path unchanged**

Run: `uv run pytest tests/python/test_data_validate.py -v`
Expected: still PASS — schema tests not touched.

- [ ] **Step 10.6: Commit**

```bash
git add src/bt/data/fetch.py tests/python/test_fetch_paths.py
git commit -m "fix(fetch): apply {timeframe} path segment to all timeframed data types"
```

---

## Task 11: CLI help text refresh

**Files:**
- Modify: `src/bt/cli.py:18-32` — extend `--type` help and `--source` help

- [ ] **Step 11.1: Update help strings**

In `src/bt/cli.py:21-22`, replace:

```python
    source: str = typer.Option(..., help="Data source (binance, bithumb, upbit)"),
    data_type: str = typer.Option("ohlcv", "--type", help="Data type (ohlcv, funding, oi, trades)"),
```

with:

```python
    source: str = typer.Option(
        ...,
        help="Source: binance, binance_spot, bithumb, upbit, fx, fng, onchain",
    ),
    data_type: str = typer.Option(
        "ohlcv", "--type",
        help="Type: ohlcv, funding, oi, oi_hist, trades, lsr_top_pos, "
             "lsr_top_acct, lsr_global, taker_ratio, depth_snap, fx, fng, onchain",
    ),
```

- [ ] **Step 11.2: Verify help renders**

Run: `uv run bt data fetch --help`
Expected: lists all sources + types in the help output.

- [ ] **Step 11.3: Commit**

```bash
git add src/bt/cli.py
git commit -m "docs(cli): refresh data fetch help with new sources/types"
```

---

## Task 12: Coverage doc

**Files:**
- Create: `docs/data-coverage.md`

- [ ] **Step 12.1: Write coverage matrix**

Create `docs/data-coverage.md`:

```markdown
# Data Coverage Matrix — Scanner Replication

Maps each akakin83 + londonpotato1 scanner to the bt data types it requires.

## Per-scanner requirements

| Scanner | Required types | Status after this plan |
|---------|----------------|------------------------|
| alpha-hunter | OI hist, funding, trades (CVD), depth_snap (MI proxy) | ✅ all covered |
| alpha-terminal | OHLCV, OI hist, funding, trades, liquidations (live), F&G | ✅ all covered (liq live-only) |
| kimp-wave | OHLCV (Upbit + Bithumb + Binance), FX | ✅ all covered |
| liqpressuremap | liquidations (live), OI hist, lsr_top_pos | ✅ all covered (liq live-only) |
| nahonja-scanner | OHLCV, funding, OI hist, taker_ratio, liquidations (live) | ✅ all covered |
| squeeze-hunter | OI hist, trades (CVD), liquidations (live), lsr_top_pos | ✅ all covered |
| kimchi-audit-picks | OHLCV (Upbit/Bithumb), aggregated coin metadata | OHLCV ✅; metadata = manual import |

## Per-type CLI examples

```bash
# Binance Futures
bt data fetch --pair BTC-USDT --source binance --type ohlcv --timeframe 1h --start 2024-01-01 --end 2024-12-31
bt data fetch --pair BTC-USDT --source binance --type funding --start 2024-01-01 --end 2024-12-31
bt data fetch --pair BTC-USDT --source binance --type oi_hist --timeframe 5m --start 2026-04-01 --end 2026-04-30
bt data fetch --pair BTC-USDT --source binance --type lsr_top_pos --timeframe 5m --start 2026-04-01 --end 2026-04-30
bt data fetch --pair BTC-USDT --source binance --type lsr_top_acct --timeframe 5m --start 2026-04-01 --end 2026-04-30
bt data fetch --pair BTC-USDT --source binance --type lsr_global --timeframe 5m --start 2026-04-01 --end 2026-04-30
bt data fetch --pair BTC-USDT --source binance --type taker_ratio --timeframe 5m --start 2026-04-01 --end 2026-04-30
bt data fetch --pair BTC-USDT --source binance --type trades --timeframe 1m --start 2026-04-01 --end 2026-04-02
bt data fetch --pair BTC-USDT --source binance --type depth_snap

# Korean exchanges
bt data fetch --pair BTC-KRW --source upbit --type ohlcv --timeframe 1m --start 2026-04-01 --end 2026-04-30
bt data fetch --pair BTC-KRW --source bithumb --type ohlcv --timeframe 1m --start 2026-04-01 --end 2026-04-30

# Macro
bt data fetch --pair USD-KRW --source fx --type fx --start 2026-04-01 --end 2026-04-30
bt data fetch --pair BTC --source fng --type fng --start 2024-01-01 --end 2026-04-30
bt data fetch --pair BTC --source onchain --type onchain --start 2024-01-01 --end 2026-04-30

# Liquidations (live daemon)
bt collect-liq --flush 60
```

## Limits

- **Binance OI hist / LSR / taker_ratio**: 30-day rolling window. For longer history run incremental fetches and append.
- **Liquidations**: REST API removed by Binance in 2021. Use `bt collect-liq` daemon (live capture only). Historical not available without paid 3rd-party feed.
- **Depth**: snapshot only, no historical orderbook reconstruction.
- **FX**: ExchangeRate-API gives current-day only. Historical USD/KRW uses Upbit USDT-KRW close as proxy.
```

- [ ] **Step 12.2: Commit**

```bash
git add docs/data-coverage.md
git commit -m "docs: add data-coverage matrix for scanner replication"
```

---

## Task 13: Full test sweep + integration sanity

- [ ] **Step 13.1: Run all Python tests**

Run: `uv run pytest tests/python/ -v`
Expected: all green. New tests added: extended schemas (6), Binance extensions (~6), FX (2), F&G (2), on-chain (2), collector (2), fetch paths (10). Total ~30 new, plus existing 36.

- [ ] **Step 13.2: Run Rust tests (sanity, untouched)**

Run: `cargo test -p bt-engine`
Expected: still green — no Rust touched.

- [ ] **Step 13.3: Live smoke test (one fetch per new type)**

Run each in turn (small windows to keep API call count low):

```bash
uv run bt data fetch --pair BTC-USDT --source binance --type oi_hist --timeframe 5m --start 2026-04-30 --end 2026-05-01
uv run bt data fetch --pair BTC-USDT --source binance --type lsr_top_pos --timeframe 5m --start 2026-04-30 --end 2026-05-01
uv run bt data fetch --pair BTC-USDT --source binance --type taker_ratio --timeframe 5m --start 2026-04-30 --end 2026-05-01
uv run bt data fetch --pair BTC-USDT --source binance --type depth_snap
uv run bt data fetch --pair USD-KRW --source fx --type fx --start 2026-05-01 --end 2026-05-02
uv run bt data fetch --pair BTC --source fng --type fng --start 2026-04-01 --end 2026-05-01
uv run bt data fetch --pair BTC --source onchain --type onchain --start 2026-04-01 --end 2026-05-01
```

Each must print `"status": "ok"` JSON and a parquet path. Verify file exists and has rows:

```bash
uv run bt data list | head -40
uv run bt data validate --path data/raw/binance/BTC-USDT/oi_hist/5m/data.parquet
```

- [ ] **Step 13.4: Final commit if any fixes were needed**

If any smoke test surfaces a real-world bug not caught by mocks (e.g., field name mismatch from actual Binance response), fix and commit. Otherwise skip.

---

## Self-Review

1. **Spec coverage**: Every scanner's data type either fetched or explicitly documented as live-only/unavailable in `docs/data-coverage.md`. ✅
2. **Placeholder scan**: No "TODO", "implement later", or vague handlers. All code blocks complete. ✅
3. **Type consistency**: Schema column names (`long_short_ratio`, `buy_sell_ratio`, `imbalance`) match between `schemas.py`, source code, and tests. ✅
4. **Path layout**: Timeframed vs untimeframed types explicitly tabulated in Task 10 test parametrize. Existing OHLCV behavior preserved. ✅
5. **Rust untouched**: Plan touches Python data ingestion only; engine, broker, portfolio remain. ✅

## Out-of-scope (intentional)

- Historical liquidations from 3rd-party feeds (Coinglass/Coinalyze) — paid, deferred.
- Full LOB reconstruction from depth WS — high storage cost, deferred.
- Derived feature modules (CVD, kimp, OI velocity, etc.) — separate Plan 2.
- Strategy ports replicating each scanner's signal logic — separate Plan 3.
- exchange-adapters PRs to add `openInterestHist`/LSR/taker methods natively — current plan calls REST directly via `HttpClient` to avoid coupling to upstream releases.
