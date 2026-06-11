# Long-only alt momentum basket vs BTC buy-hold (Binance spot)

> 2026-06-05. Question: can a long-only cross-sectional alt basket beat holding
> BTC, including the recent down market?

## Verdict: NO — trustworthy negative.

Long-only momentum basket of Binance-spot alts loses to BTC buy-hold on every
headline metric, full window and recent. Survivorship tailwind present (klines
serve only *live* symbols → the basket's worst trades are deleted), and it still
loses → conclusive per the pre-registered interpretation rule.

## Setup
- **Universe:** 414 Binance-spot USDT pairs (stablecoins + leveraged tokens
  filtered), trailing 7-bar $1M median-volume liquidity floor (no look-ahead).
  BTC held OUT of the panel (benchmark only).
- **Window:** 2024-12-19 → 2026-06-05 daily (534 bars). Median 251 liquid alts/day.
- **Strategy:** `CrossSectionalStrategy` lookback=30, top_k=10, bottom_k=0
  (long-only), equal weight, weekly rebalance, 10bps fee + 5bps slippage.
- **Configs:** `configs/panel/binance_alt_vs_btc_daily.yaml`; fetch
  `scripts/fetch_binance_spot_panel.py`; run `scripts/run_alt_vs_btc.py`.

## Results (NET of fees)
| metric | BASKET | BTC buy-hold | winner |
|--------|--------|--------------|--------|
| return (full) | −91.4% | −37.0% | BTC |
| sharpe (full) | −1.70 | −0.49 | BTC |
| sortino (full) | −2.32 | −0.68 | BTC |
| max_dd (full) | −91.5% | −50.8% | BTC |
| return (recent 90b) | −14.2% | −7.0% | BTC |
| sharpe (recent 90b) | −0.52 | −0.57 | ~tie (basket marginal) |

Walk-forward OOS: per-fold sharpe [−1.15, −1.55, −3.10, −1.51], OOS-mean −1.83,
**DSR=0.035** (96.5% chance the edge is noise/negative), PBO=None (single config).

## Attribution (decomposition)
| component | total return | read |
|-----------|--------------|------|
| BTC buy-hold | −37.0% | benchmark |
| passive eq-wt all-alt index | −83.3% | **alt-beta lost 2.2× BTC** (dominant) |
| momentum basket gross | −88.2% | selection added −5pp (whipsaw) |
| momentum basket net | −91.4% | fees −3pp |

Drivers in order: (1) alts-as-a-class were crushed vs BTC in this BTC-dominance /
alt-bear regime; (2) momentum selection made it *worse* (buys recent winners that
mean-revert/crash in a downtrend); (3) fees minor.

## Takeaway
In the Dec'24–Jun'26 regime, the alpha was *positional BTC beta*, not alt
cross-section. Long-only alt momentum is structurally short the dominant trade.
Consistent with prior crypto-signal nulls (kr-tech-alpha-findings). Do NOT tune
lookback/top_k to force a win — that's overfitting with no multiple-testing
correction (DSR already near-zero). A credible *positive* would require a
point-in-time (survivorship-free) universe + the search-space/PBO wiring
(handoff step 1), and a regime where alts aren't in secular decline vs BTC.
