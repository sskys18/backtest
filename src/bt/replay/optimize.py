"""Exhaustive filter + exit optimization over scan replay data."""

from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np

from bt.replay.loader import ScanLog, ScanSignal, load_scan_log
from bt.replay.simulator import EntryFilter, ExitRules, TradeResult, replay, summarize


# ── Filter definitions ──────────────────────────────────────────────────────

FILTERS: dict[str, EntryFilter] = {
    "chg<3":   lambda s: s.change_24h < 3,
    "chg<5":   lambda s: s.change_24h < 5,
    "chg<8":   lambda s: s.change_24h < 8,
    "chg<15":  lambda s: s.change_24h < 15,
    "vr<5":    lambda s: s.volume_ratio < 5,
    "vr<10":   lambda s: s.volume_ratio < 10,
    "vr<25":   lambda s: s.volume_ratio < 25,
    "vr>5":    lambda s: s.volume_ratio > 5,
    "bid>0.5": lambda s: s.bid_ratio > 0.5,
    "bid>0.7": lambda s: s.bid_ratio > 0.7,
    "bid>0.8": lambda s: s.bid_ratio > 0.8,
    "rsi<40":  lambda s: s.rsi_15m < 40,
    "rsi<50":  lambda s: s.rsi_15m < 50,
    "rsi>60":  lambda s: s.rsi_15m > 60,
    "ema<1":   lambda s: s.ema_cross < 1,
    "ema<2":   lambda s: s.ema_cross < 2,
    "ema<3":   lambda s: s.ema_cross < 3,
    "sc>35":   lambda s: s.score > 35,
    "sc>40":   lambda s: s.score > 40,
    "sc>45":   lambda s: s.score > 45,
    "sc>50":   lambda s: s.score > 50,
}


def _make_combo_filter(names: list[str]) -> EntryFilter:
    """Create a filter that requires ALL named filters to pass."""
    fns = [FILTERS[n] for n in names]
    def combo(s: ScanSignal) -> bool:
        return all(fn(s) for fn in fns)
    return combo


def _accept_all(s: ScanSignal) -> bool:
    return True


# ── Optimization ────────────────────────────────────────────────────────────

def optimize_filters(
    scan_log: ScanLog,
    exit_rules: ExitRules,
    max_combo_size: int = 3,
    min_trades: int = 20,
    max_concurrent: int = 3,
) -> list[dict]:
    """Test all filter combinations and rank by expected value.

    Returns list of results sorted by avg_pnl descending.
    """
    results = []

    # Baseline — no filter
    baseline_results = replay(scan_log, _accept_all, exit_rules, max_concurrent)
    baseline = summarize(baseline_results)
    baseline["filter"] = "none"
    results.append(baseline)

    filter_names = list(FILTERS.keys())

    # Singles
    for name in filter_names:
        trades = replay(scan_log, FILTERS[name], exit_rules, max_concurrent)
        s = summarize(trades)
        if s["trades"] >= min_trades:
            s["filter"] = name
            results.append(s)

    # Pairs
    for combo in combinations(filter_names, 2):
        fn = _make_combo_filter(list(combo))
        trades = replay(scan_log, fn, exit_rules, max_concurrent)
        s = summarize(trades)
        if s["trades"] >= min_trades:
            s["filter"] = " + ".join(combo)
            results.append(s)

    # Triples
    if max_combo_size >= 3:
        for combo in combinations(filter_names, 3):
            fn = _make_combo_filter(list(combo))
            trades = replay(scan_log, fn, exit_rules, max_concurrent)
            s = summarize(trades)
            if s["trades"] >= min_trades:
                s["filter"] = " + ".join(combo)
                results.append(s)

    results.sort(key=lambda x: x["avg_pnl"], reverse=True)
    return results


def optimize_exits(
    scan_log: ScanLog,
    entry_filter: EntryFilter,
    tp_range: list[float] | None = None,
    sl_range: list[float] | None = None,
    trail_range: list[float] | None = None,
    max_hold_range: list[int] | None = None,
    max_concurrent: int = 3,
) -> list[dict]:
    """Grid search over exit parameters with a fixed entry filter.

    Returns list of results sorted by avg_pnl descending.
    """
    if tp_range is None:
        tp_range = [0.5, 0.75, 1.0, 1.5, 2.0, 3.0]
    if sl_range is None:
        sl_range = [0.3, 0.5, 0.75, 1.0, 1.5]
    if trail_range is None:
        trail_range = [0.0, 0.5, 1.0]
    if max_hold_range is None:
        max_hold_range = [60, 120, 240, 360]

    results = []

    for tp in tp_range:
        for sl in sl_range:
            for trail in trail_range:
                for max_hold in max_hold_range:
                    rules = ExitRules(
                        tp_pct=tp, sl_pct=sl, trail_pct=trail,
                        max_hold_bars=max_hold,
                    )
                    trades = replay(scan_log, entry_filter, rules, max_concurrent)
                    s = summarize(trades)
                    s["tp"] = tp
                    s["sl"] = sl
                    s["trail"] = trail
                    s["max_hold"] = max_hold
                    results.append(s)

    results.sort(key=lambda x: x["avg_pnl"], reverse=True)
    return results


def full_optimize(
    jsonl_path: str,
    ohlcv_dir: str,
    output_path: str | None = None,
) -> dict:
    """Run full optimization: entry filters → exit params → combined.

    Returns best entry filter + exit rules + expected performance.
    """
    scan_log = load_scan_log(jsonl_path, ohlcv_dir)
    print(f"Loaded {len(scan_log.signals)} signals, {len(scan_log.ohlcv)} markets with OHLCV")

    # Phase 1: Find best entry filter with default exit rules
    print("\n[Phase 1] Optimizing entry filters...")
    default_exit = ExitRules(tp_pct=1.5, sl_pct=0.5, max_hold_bars=240)
    filter_results = optimize_filters(scan_log, default_exit, max_combo_size=3, min_trades=15)

    print(f"\nTop 10 entry filters:")
    print(f"{'Filter':<50} {'N':>5} {'WR':>6} {'AvgPnL':>8} {'Sharpe':>7}")
    print("-" * 80)
    for r in filter_results[:10]:
        print(f"{r['filter']:<50} {r['trades']:>5} {r['wr']*100:>5.1f}% {r['avg_pnl']*100:>+7.3f}% {r['sharpe']:>6.3f}")

    # Phase 2: Optimize exits for best entry filter
    best_filter_name = filter_results[0]["filter"]
    print(f"\n[Phase 2] Optimizing exit rules for filter: {best_filter_name}")

    if best_filter_name == "none":
        best_entry_fn = _accept_all
    else:
        best_entry_fn = _make_combo_filter(best_filter_name.split(" + "))

    exit_results = optimize_exits(scan_log, best_entry_fn)

    print(f"\nTop 10 exit configs:")
    print(f"{'TP':>5} {'SL':>5} {'Trail':>5} {'Hold':>5} {'N':>5} {'WR':>6} {'AvgPnL':>8} {'Sharpe':>7}")
    print("-" * 55)
    for r in exit_results[:10]:
        print(f"{r['tp']:>4.1f}% {r['sl']:>4.1f}% {r['trail']:>4.1f}% {r['max_hold']:>4}m {r['trades']:>5} {r['wr']*100:>5.1f}% {r['avg_pnl']*100:>+7.3f}% {r['sharpe']:>6.3f}")

    # Phase 3: Final combined result
    best_exit = exit_results[0]
    final_rules = ExitRules(
        tp_pct=best_exit["tp"], sl_pct=best_exit["sl"],
        trail_pct=best_exit["trail"], max_hold_bars=best_exit["max_hold"],
    )
    final_trades = replay(scan_log, best_entry_fn, final_rules)
    final_summary = summarize(final_trades)

    output = {
        "entry_filter": best_filter_name,
        "exit_rules": {
            "tp_pct": best_exit["tp"],
            "sl_pct": best_exit["sl"],
            "trail_pct": best_exit["trail"],
            "max_hold_bars": best_exit["max_hold"],
        },
        "performance": final_summary,
        "top_filters": filter_results[:10],
        "top_exits": exit_results[:10],
    }

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(json.dumps(output, indent=2, default=str))

    print(f"\n{'='*60}")
    print(f"BEST COMBINED RESULT:")
    print(f"  Entry: {best_filter_name}")
    print(f"  Exit:  TP={best_exit['tp']}% SL={best_exit['sl']}% Trail={best_exit['trail']}% MaxHold={best_exit['max_hold']}min")
    print(f"  Trades: {final_summary['trades']}, WR: {final_summary['wr']*100:.1f}%")
    print(f"  Avg PnL: {final_summary['avg_pnl']*100:+.3f}%, Sharpe: {final_summary['sharpe']:.3f}")
    print(f"  Exit reasons: {final_summary['by_reason']}")
    print(f"{'='*60}")

    return output
