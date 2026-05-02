"""Binance data source — OHLCV, funding rates, OI via exchange-adapters."""

from __future__ import annotations

import asyncio
from datetime import datetime

import polars as pl
from core.http.client import HttpClient
from exchanges.binance.futures_usdt.rest_client import BinanceFuturesUssdtRestClient
from exchanges.binance.futures_usdt.constants import BASE_URL as FUTURES_BASE_URL
from exchanges.binance.spot.rest_client import BinanceSpotRestClient
from exchanges.binance.spot.constants import BASE_URL as SPOT_BASE_URL


def _to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _symbol(pair: str) -> str:
    """Convert BTC-USDT → BTCUSDT."""
    return pair.replace("-", "")


class BinanceSource:
    """Binance data source supporting OHLCV, funding, OI, trades."""

    _LSR_ENDPOINTS = {
        "lsr_top_pos": "/futures/data/topLongShortPositionRatio",
        "lsr_top_acct": "/futures/data/topLongShortAccountRatio",
        "lsr_global": "/futures/data/globalLongShortAccountRatio",
    }

    def supported_types(self) -> list[str]:
        return [
            "ohlcv", "funding", "oi", "oi_hist", "trades",
            "lsr_top_pos", "lsr_top_acct", "lsr_global",
            "taker_ratio", "depth_snap",
        ]

    async def fetch(
        self,
        pair: str,
        data_type: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> pl.DataFrame:
        if data_type == "ohlcv":
            return await self._fetch_ohlcv(pair, timeframe, start, end)
        elif data_type == "funding":
            return await self._fetch_funding(pair, start, end)
        elif data_type == "oi":
            return await self._fetch_oi(pair)
        elif data_type == "oi_hist":
            return await self._fetch_oi_hist(pair, timeframe, start, end)
        elif data_type == "trades":
            return await self._fetch_trades(pair, timeframe, start, end)
        elif data_type in self._LSR_ENDPOINTS:
            return await self._fetch_lsr(pair, data_type, timeframe, start, end)
        elif data_type == "taker_ratio":
            return await self._fetch_taker_ratio(pair, timeframe, start, end)
        elif data_type == "depth_snap":
            return await self._fetch_depth_snap(pair)
        else:
            raise ValueError(f"Unsupported data type for Binance: {data_type}")

    async def _fetch_ohlcv(
        self, pair: str, timeframe: str, start: datetime, end: datetime,
    ) -> pl.DataFrame:
        """Fetch OHLCV klines from Binance futures, paginated."""
        symbol = _symbol(pair)
        interval = _timeframe_to_interval(timeframe)
        start_ms = _to_ms(start)
        end_ms = _to_ms(end)

        http = HttpClient(FUTURES_BASE_URL)
        client = BinanceFuturesUssdtRestClient(http)

        all_rows = []
        current_start = start_ms

        try:
            while current_start < end_ms:
                klines = await client.get_klines(
                    symbol=symbol,
                    interval=interval,
                    limit=1500,
                    start_time=current_start,
                    end_time=end_ms,
                )

                if not klines:
                    break

                for k in klines:
                    all_rows.append({
                        "timestamp": int(k[0]),
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                    })

                # Move past last candle
                last_ts = int(klines[-1][0])
                if last_ts <= current_start:
                    break
                current_start = last_ts + 1

        finally:
            await http.close()

        if not all_rows:
            return pl.DataFrame(schema={"timestamp": pl.Int64, "open": pl.Float64, "high": pl.Float64, "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64})

        return pl.DataFrame(all_rows).sort("timestamp")

    async def _fetch_funding(
        self, pair: str, start: datetime, end: datetime,
    ) -> pl.DataFrame:
        """Fetch funding rate history from Binance futures."""
        symbol = _symbol(pair)
        start_ms = _to_ms(start)
        end_ms = _to_ms(end)

        http = HttpClient(FUTURES_BASE_URL)
        client = BinanceFuturesUssdtRestClient(http)

        all_rows = []
        current_start = start_ms

        try:
            while current_start < end_ms:
                rates = await client.get_funding_rate(
                    symbol=symbol,
                    start_time=current_start,
                    end_time=end_ms,
                    limit=1000,
                )

                if not rates:
                    break

                for r in rates:
                    all_rows.append({
                        "timestamp": int(r["fundingTime"]),
                        "funding_rate": float(r["fundingRate"]),
                        "mark_price": float(r.get("markPrice", 0)),
                    })

                last_ts = int(rates[-1]["fundingTime"])
                if last_ts <= current_start:
                    break
                current_start = last_ts + 1

        finally:
            await http.close()

        if not all_rows:
            return pl.DataFrame(schema={"timestamp": pl.Int64, "funding_rate": pl.Float64, "mark_price": pl.Float64})

        return pl.DataFrame(all_rows).sort("timestamp")

    async def _fetch_oi(self, pair: str) -> pl.DataFrame:
        """Fetch current open interest from Binance futures."""
        symbol = _symbol(pair)

        http = HttpClient(FUTURES_BASE_URL)
        client = BinanceFuturesUssdtRestClient(http)

        try:
            data = await client.get_open_interest(symbol=symbol)
            return pl.DataFrame([{
                "timestamp": int(data.get("time", 0)),
                "open_interest": float(data.get("openInterest", 0)),
            }])
        finally:
            await http.close()

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
        return pl.DataFrame(all_rows).unique(subset=["timestamp"]).sort("timestamp")

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
        return pl.DataFrame(all_rows).unique(subset=["timestamp"]).sort("timestamp")

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
        return pl.DataFrame(all_rows).unique(subset=["timestamp"]).sort("timestamp")

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
        ts = int(book.get("E") or book.get("T") or int(datetime.now().timestamp() * 1000))

        return pl.DataFrame([{
            "timestamp": ts, "bid_total_qty": bid_total, "ask_total_qty": ask_total,
            "bid_top10_qty": bid_top10, "ask_top10_qty": ask_top10,
            "imbalance": imbalance, "spread_bps": spread_bps,
        }])

    async def _fetch_trades(
        self, pair: str, timeframe: str, start: datetime, end: datetime,
    ) -> pl.DataFrame:
        """Fetch aggregated trades from Binance futures."""
        symbol = _symbol(pair)
        start_ms = _to_ms(start)
        end_ms = _to_ms(end)

        http = HttpClient(FUTURES_BASE_URL)
        client = BinanceFuturesUssdtRestClient(http)

        all_rows = []
        current_start = start_ms

        try:
            while current_start < end_ms:
                trades = await client.get_agg_trades(
                    symbol=symbol,
                    start_time=current_start,
                    end_time=min(current_start + 3_600_000, end_ms),
                    limit=1000,
                )

                if not trades:
                    current_start += 3_600_000
                    continue

                for t in trades:
                    all_rows.append({
                        "timestamp": int(t["T"]),
                        "price": float(t["p"]),
                        "quantity": float(t["q"]),
                        "is_buyer_maker": bool(t["m"]),
                    })

                last_ts = int(trades[-1]["T"])
                if last_ts <= current_start:
                    current_start += 3_600_000
                else:
                    current_start = last_ts + 1

        finally:
            await http.close()

        if not all_rows:
            return pl.DataFrame(schema={
                "timestamp": pl.Int64, "num_trades": pl.UInt64,
                "buy_volume": pl.Float64, "sell_volume": pl.Float64, "vwap": pl.Float64,
            })

        # Aggregate into bar-sized bins
        df = pl.DataFrame(all_rows)
        interval_ms = _timeframe_to_ms(timeframe)

        return df.with_columns(
            (pl.col("timestamp") // interval_ms * interval_ms).alias("timestamp")
        ).group_by("timestamp").agg([
            pl.len().cast(pl.UInt64).alias("num_trades"),
            pl.when(pl.col("is_buyer_maker").not_())
                .then(pl.col("quantity")).otherwise(0.0)
                .sum().alias("buy_volume"),
            pl.when(pl.col("is_buyer_maker"))
                .then(pl.col("quantity")).otherwise(0.0)
                .sum().alias("sell_volume"),
            (pl.col("price") * pl.col("quantity")).sum().alias("_tp_vol"),
            pl.col("quantity").sum().alias("_total_vol"),
        ]).with_columns(
            (pl.col("_tp_vol") / pl.col("_total_vol")).alias("vwap")
        ).drop(["_tp_vol", "_total_vol"]).sort("timestamp")


class BinanceSpotSource:
    """Binance spot — simpler, OHLCV only."""

    def supported_types(self) -> list[str]:
        return ["ohlcv"]

    async def fetch(
        self, pair: str, data_type: str, timeframe: str,
        start: datetime, end: datetime,
    ) -> pl.DataFrame:
        symbol = _symbol(pair)
        interval = _timeframe_to_interval(timeframe)

        http = HttpClient(SPOT_BASE_URL)
        client = BinanceSpotRestClient(http)

        all_rows = []
        # Spot klines don't have start/end params in the adapter,
        # so we fetch max and filter
        try:
            klines = await client.get_klines(symbol=symbol, interval=interval, limit=1000)
            for k in klines:
                ts = int(k[0])
                if _to_ms(start) <= ts <= _to_ms(end):
                    all_rows.append({
                        "timestamp": ts,
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                    })
        finally:
            await http.close()

        if not all_rows:
            return pl.DataFrame(schema={"timestamp": pl.Int64, "open": pl.Float64, "high": pl.Float64, "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64})

        return pl.DataFrame(all_rows).sort("timestamp")


def _timeframe_to_interval(tf: str) -> str:
    """Convert bt timeframe to Binance interval string."""
    return tf  # Already compatible: 1m, 5m, 15m, 1h, 4h, 1d


def _timeframe_to_ms(tf: str) -> int:
    """Convert timeframe to milliseconds."""
    mapping = {
        "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
        "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000,
        "1d": 86_400_000,
    }
    return mapping.get(tf, 3_600_000)


def _timeframe_to_oi_period(tf: str) -> str:
    """Binance OI hist allowed periods: 5m,15m,30m,1h,2h,4h,6h,12h,1d."""
    allowed = {"5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}
    return tf if tf in allowed else "5m"
