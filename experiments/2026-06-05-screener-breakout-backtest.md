# Backtesting the heat-screener logic: new-high breakout + volume surge

> 2026-06-05. Can the screener's entry rule (buy a coin breaking to a new high on
> a volume surge, ride a trailing stop) actually be traded profitably?

## Verdict: signal has real content, but NO profitable OOS edge.

Across 5 OOS windows the breakout+volume signal beats its own placebo (it shapes
returns — unlike microstructure) but is net negative (1/5 windows positive). It
loses LESS than random, which is not tradeable. The screener is a watch tool, not
an auto-strategy.

## Setup
- Data: 5m 60d panel, 155 liquid Binance-spot alts (deepest intraday w/ volume;
  OI piece untestable — free OI caps at 21d).
- `BreakoutStrategy` (`scripts/experiments/backtest_screener.py`): long the strongest coin
  making a new `hi_lookback`-bar high on volume >= `vol_mult` x baseline, ranked
  by volume surge, top_k=1, close-trailing stop. 10bps fee + 5bps slip.
- Controls: single-shot untouched OOS + shuffle placebo (permute volume in time)
  + 5-fold rolling walk-forward with per-fold placebo.
- Provenance (added post-review): the committed `backtest_screener.py` implements
  the single-shot OOS + placebo + DSR path. The 5-fold walk-forward and the
  regime-gate runs below were session-interactive variations of that harness;
  their driver code was NOT committed — numbers recorded as observed, treat as
  non-reproducible-from-repo. The verdict does not rest on them alone: the
  committed single-shot path already shows DSR 0.118 (no edge).

## Results
Single-shot OOS (tune IS70% -> freeze -> eval untouched 30%, ~18d):
- OOS NET **+43.8%** (69 trades), GROSS +82.5%, BTC −18.5%, DSR=0.118 (<0.95).
- Placebo (volume shuffled): **−9.1%**. Real >> placebo → signal has content.

5-fold walk-forward (the real test of robustness):
| fold | real net | placebo | BTC |
|------|----------|---------|-----|
| 0 | −11.7% | −17.7% | +4.8% |
| 1 | +27.9% | +0.6% | +3.8% |
| 2 | −47.5% | −23.7% | −4.0% |
| 3 | −11.6% | −23.9% | −1.3% |
| 4 | −24.1% | −29.0% | −19.5% |

**1/5 folds positive, median −11.7%.** Real beats placebo in 4/5 (signal real),
but absolute returns negative in 4/5.

## Reconciliation
The eye-catching +43.8% single-shot was ONE favorable window (fold-1 type, +27.9%).
The breakout+volume signal genuinely shapes returns (real > placebo, repeatably) —
it is NOT the pure mechanic+luck the microstructure test was. But the shaping isn't
enough to overcome cost + alt-bear drag: it makes losses smaller, not profits.

## Regime-gate attempt (salvage try) — did NOT work
Hypothesis: the signal works in BTC-uptrends (fold 1, the only consistent winner).
Gate: only enter breakouts when BTC > its trailing 1-day MA (fixed, no tuning).
- Single-shot OOS gated: +14.4% vs placebo −32.3% (real >> placebo persists).
- 5-fold: 1/5 positive, median **−23.4%** (worse than ungated −11.7%).
The 1-day-MA gate is too crude (on 48% of bars, not the profitable ones). Only
fold 1 wins across all variants — and 60 days contains ~ONE real uptrend window,
so a regime strategy can't be validated here (n≈1 regime). NOT pursued further:
trying filters until one passes IS the overfitting this harness guards against.

## Takeaway
Best-behaved signal of the session (passes placebo, content confirmed) yet still
no tradeable OOS edge in this regime. This is precisely why the heat screener is
framed as DESCRIPTIVE monitoring: "what is breaking out on volume" is a real,
detectable event — but auto-buying it loses. Use the screener to build a watch
list; entry/exit/risk is human judgment. A tradeable version would need: a
catalyst/regime filter (don't trade breakouts in an alt-bear), a short leg or
market hedge, and far more OOS data than 60 days.
