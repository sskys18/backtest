# Short-horizon order-flow (microstructure) signal search — Binance perp

> 2026-06-05. Question: does order-flow (taker buy/sell ratio, OI momentum) give a
> short-horizon cross-sectional edge that price-surge signals did not?

## Verdict: trustworthy negative — killed by a placebo (shuffle) test.

The apparent edge is the trade mechanic + small-sample luck, NOT the signal:
shuffling the order-flow signal in time (destroying predictive content) barely
changes returns. Plus the data is structurally too thin to confirm anything.

## Setup
- **Data:** Binance USDT-perp, top-50 by 24h volume + BTC held out. **5m/15m/1h**
  taker buy/sell ratio + open interest. Window capped by the API.
- **Hard data limit (key finding):** `/futures/data/*` (taker, OI) return only the
  **last ~500 points regardless of startTime** — 5m→1.7d, 15m→5.2d, **1h→20.8d**.
  Historical intraday microstructure is NOT freely available at depth; it must be
  collected live going forward (this is why the repo has the scan-replay lineage).
- **Used 1h** (deepest: 500 bars / 21 days, 50 alts).
- **Strategy** (`MicroStrategy`, `scripts/search_micro_signal.py`): per bar long the
  strongest firing alt by order-flow (taker / oi_mom / combo), close-trailing stop,
  hold ≤8h. Perp fees 4bps + 2bps slip.
- **Harness:** single-shot untouched OOS (tune IS 70% → freeze 1 → eval OOS 30%) +
  rolling walk-forward (DSR, CSCV PBO) + **shuffle placebo control**.

## Results (1h)
| test | value |
|------|-------|
| single-shot OOS NET | +53.7% (**but 1 trade** — no power) |
| single-shot OOS GROSS | +141% |
| BTC OOS | −14.9% |
| **DSR** (deflated by 66 trials) | **0.138** (needs >0.95) |
| walk-forward per-fold OOS sharpe | [7.2, 5.7, 15.7, −13.3] |
| **PBO** | 0.25 (passes, but see placebo) |

Pre-registered edge (NET>0 AND DSR>0.95 AND PBO<0.5): **fails on DSR.**

## The placebo (shuffle) control — the decisive test
Permute taker & OI in time within each coin (destroys any real signal), rerun the
best config full-sample:
| | trades | cum |
|--|--|--|
| real order-flow signal | 8 | +401% |
| **shuffled signal** | 10 | **+213%** |

A *destroyed* signal still returns +213%. So the profit is the **mechanic** (1%
trailing stop + 8–10 concentrated longs in a 21-day window where some alts pumped)
plus luck — not order flow. With 8–10 trades, signal is indistinguishable from
noise. This is why naive "+401%" / "+53.7%" backtests lie.

## Takeaway
On the only freely testable microstructure data (21d / 500 bars, 1h), order-flow
shows **no edge attributable to the signal**. Less of a clean null than price
(PBO/folds looked superficially better), but the placebo proves it's mechanic+luck.
The real blocker is data: confirming a short-horizon microstructure edge requires
**live-collected order-flow at depth** (orderbook snapshots, trade prints) over
months — exactly the `bt replay` + scan-log design. Free historical API ≈ 500
points is too shallow. Do NOT trust headline returns from <30-trade backtests;
always run the shuffle placebo + untouched OOS.
