"""Scan replay simulator — walks forward through 1min bars after each signal."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import polars as pl

from bt.replay.loader import ScanLog, ScanSignal


@dataclass
class TradeResult:
    """Outcome of a simulated trade."""
    market: str
    entry_ts: int
    entry_price: float
    exit_price: float
    exit_ts: int
    pnl_pct: float
    hold_bars: int
    exit_reason: str  # "TP", "SL", "TRAIL", "STALE"
    max_gain_pct: float
    max_loss_pct: float
    # Signal values at entry
    score: float
    change_24h: float
    volume_ratio: float
    bid_ratio: float
    rsi_15m: float
    ema_cross: float


@dataclass
class ExitRules:
    """Configurable exit parameters."""
    tp_pct: float = 2.0        # Take profit %
    sl_pct: float = 0.5        # Stop loss %
    trail_pct: float = 0.0     # Trailing stop % (0 = disabled)
    max_hold_bars: int = 360   # Max hold in 1min bars (360 = 6 hours)
    fee_pct: float = 0.05      # Upbit fee (5bps each way = 10bps round trip)


EntryFilter = Callable[[ScanSignal], bool]


def simulate_trade(
    signal: ScanSignal,
    bars: pl.DataFrame,
    rules: ExitRules,
) -> TradeResult | None:
    """Simulate a single trade from a scan signal.

    Walks forward through 1min bars after entry, checking TP/SL/Trail/Stale.
    Returns None if no bars available after entry.
    """
    # Find bars after entry timestamp
    entry_ts = signal.timestamp_ms
    entry_price = signal.price
    if entry_price <= 0:
        return None

    # Apply entry fee
    effective_entry = entry_price * (1 + rules.fee_pct / 100)

    # Get bars after entry
    future = bars.filter(pl.col("timestamp") > entry_ts).sort("timestamp")
    if len(future) == 0:
        return None

    max_bars = min(rules.max_hold_bars, len(future))
    highs = future["high"].to_numpy()[:max_bars]
    lows = future["low"].to_numpy()[:max_bars]
    closes = future["close"].to_numpy()[:max_bars]
    timestamps = future["timestamp"].to_numpy()[:max_bars]

    tp_price = effective_entry * (1 + rules.tp_pct / 100)
    sl_price = effective_entry * (1 - rules.sl_pct / 100)

    peak = effective_entry
    max_gain = 0.0
    max_loss = 0.0

    for i in range(max_bars):
        high = highs[i]
        low = lows[i]
        close = closes[i]

        # Track max gain/loss
        bar_gain = (high - effective_entry) / effective_entry
        bar_loss = (effective_entry - low) / effective_entry
        max_gain = max(max_gain, bar_gain)
        max_loss = max(max_loss, bar_loss)

        # Check SL first (conservative: assume worst case hit within bar)
        if low <= sl_price:
            exit_price = sl_price * (1 - rules.fee_pct / 100)
            pnl = (exit_price - effective_entry) / effective_entry
            return TradeResult(
                market=signal.market, entry_ts=entry_ts, entry_price=entry_price,
                exit_price=float(sl_price), exit_ts=int(timestamps[i]),
                pnl_pct=pnl, hold_bars=i + 1, exit_reason="SL",
                max_gain_pct=max_gain, max_loss_pct=max_loss,
                score=signal.score, change_24h=signal.change_24h,
                volume_ratio=signal.volume_ratio, bid_ratio=signal.bid_ratio,
                rsi_15m=signal.rsi_15m, ema_cross=signal.ema_cross,
            )

        # Check TP
        if high >= tp_price:
            exit_price = tp_price * (1 - rules.fee_pct / 100)
            pnl = (exit_price - effective_entry) / effective_entry
            return TradeResult(
                market=signal.market, entry_ts=entry_ts, entry_price=entry_price,
                exit_price=float(tp_price), exit_ts=int(timestamps[i]),
                pnl_pct=pnl, hold_bars=i + 1, exit_reason="TP",
                max_gain_pct=max_gain, max_loss_pct=max_loss,
                score=signal.score, change_24h=signal.change_24h,
                volume_ratio=signal.volume_ratio, bid_ratio=signal.bid_ratio,
                rsi_15m=signal.rsi_15m, ema_cross=signal.ema_cross,
            )

        # Trailing stop
        if rules.trail_pct > 0:
            peak = max(peak, high)
            trail_stop = peak * (1 - rules.trail_pct / 100)
            if low <= trail_stop:
                exit_price = trail_stop * (1 - rules.fee_pct / 100)
                pnl = (exit_price - effective_entry) / effective_entry
                return TradeResult(
                    market=signal.market, entry_ts=entry_ts, entry_price=entry_price,
                    exit_price=float(trail_stop), exit_ts=int(timestamps[i]),
                    pnl_pct=pnl, hold_bars=i + 1, exit_reason="TRAIL",
                    max_gain_pct=max_gain, max_loss_pct=max_loss,
                    score=signal.score, change_24h=signal.change_24h,
                    volume_ratio=signal.volume_ratio, bid_ratio=signal.bid_ratio,
                    rsi_15m=signal.rsi_15m, ema_cross=signal.ema_cross,
                )

    # STALE — max hold reached, exit at last close
    exit_price = closes[max_bars - 1] * (1 - rules.fee_pct / 100)
    pnl = (exit_price - effective_entry) / effective_entry

    return TradeResult(
        market=signal.market, entry_ts=entry_ts, entry_price=entry_price,
        exit_price=float(closes[max_bars - 1]), exit_ts=int(timestamps[max_bars - 1]),
        pnl_pct=pnl, hold_bars=max_bars, exit_reason="STALE",
        max_gain_pct=max_gain, max_loss_pct=max_loss,
        score=signal.score, change_24h=signal.change_24h,
        volume_ratio=signal.volume_ratio, bid_ratio=signal.bid_ratio,
        rsi_15m=signal.rsi_15m, ema_cross=signal.ema_cross,
    )


def replay(
    scan_log: ScanLog,
    entry_filter: EntryFilter,
    exit_rules: ExitRules,
    max_concurrent: int = 3,  # max simultaneous positions
    cooldown_bars: int = 5,   # min bars between entries on same market
) -> list[TradeResult]:
    """Replay all scans through the simulator.

    Applies entry_filter to each signal, simulates trades, respects
    position limits and cooldown periods.
    """
    results = []
    # Track active positions and cooldowns
    last_entry_ts: dict[str, int] = {}  # market → last entry timestamp
    active_count = 0
    active_exits: list[int] = []  # exit timestamps of active trades

    # Sort signals by time
    sorted_signals = sorted(scan_log.signals, key=lambda s: s.timestamp_ms)

    for signal in sorted_signals:
        # Clean up expired active trades
        active_exits = [t for t in active_exits if t > signal.timestamp_ms]
        active_count = len(active_exits)

        # Skip if at max concurrent positions
        if active_count >= max_concurrent:
            continue

        # Skip if on cooldown for this market
        cooldown_ms = cooldown_bars * 60_000  # 1min bars
        if signal.market in last_entry_ts:
            if signal.timestamp_ms - last_entry_ts[signal.market] < cooldown_ms:
                continue

        # Apply entry filter
        if not entry_filter(signal):
            continue

        # Need OHLCV data for this market
        if signal.market not in scan_log.ohlcv:
            continue

        bars = scan_log.ohlcv[signal.market]
        result = simulate_trade(signal, bars, exit_rules)

        if result is not None:
            results.append(result)
            last_entry_ts[signal.market] = signal.timestamp_ms
            active_exits.append(result.exit_ts)

    return results


def summarize(results: list[TradeResult]) -> dict:
    """Summarize trade results."""
    if not results:
        return {"trades": 0, "wr": 0, "avg_pnl": 0, "total_pnl": 0, "sharpe": 0}

    n = len(results)
    pnls = [r.pnl_pct for r in results]
    wins = sum(1 for p in pnls if p > 0)

    avg_pnl = np.mean(pnls)
    std_pnl = np.std(pnls) if len(pnls) > 1 else 0
    sharpe = avg_pnl / std_pnl if std_pnl > 0 else 0

    return {
        "trades": n,
        "wins": wins,
        "losses": n - wins,
        "wr": wins / n,
        "avg_pnl": avg_pnl,
        "total_pnl": sum(pnls),
        "sharpe": sharpe,
        "avg_hold_bars": np.mean([r.hold_bars for r in results]),
        "by_reason": {
            reason: sum(1 for r in results if r.exit_reason == reason)
            for reason in ["TP", "SL", "TRAIL", "STALE"]
        },
    }
