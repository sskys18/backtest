# Scanner Backtest Reference

Each scanner is implemented in `src/bt/scanners/<name>.py`. Run scanners with:

```bash
bt scan-backtest --name <name|all> --features path/to/features.parquet
```

## Scanner registry

| Name | Source URL | Approximation? | Required columns | Public spec basis |
|------|------------|----------------|------------------|-------------------|
| `kimp_wave` | `akakin83/kimp-wave` | No | `kimp`, `volume` | UI: kimp 1.5%+, volume $50M+ |
| `liqpressuremap` | `akakin83/liqpressuremap` | No | `liq_intensity`, `liq_z`, `liq_imbalance` | UI: 3-sigma ignition, abs(bias) >= 0.6 |
| `alpha_hunter` | `akakin83/alpha-hunter` | Yes | `oi_z`, `cvd`, `funding_rate`, `stage` | Stage Gate weights private |
| `alpha_terminal` | `akakin83/alpha-terminal` | Yes | `close`, `vwap`, `rs_btc`, `liq_z`, `oi_z` | 18-factor weights private |
| `nahonja` | `akakin83/nahonja-scanner` | Yes | `oi_z`, `taker_ratio`, `funding_rate`, `liq_z`, `cvd`, `rs_btc` | 8-signal weights private |
| `squeeze_hunter` | `akakin83/squeeze-hunter` | Yes | `oi_velocity`, `cvd`, `liq_z`, `toptrader_lsr` | Layer weights private |
| `kimchi_audit` | `londonpotato1/kimchi-audit-picks` | No | `pair`, `score`, `bithumb_concentration`, `whale_pct` | UI: score 55+, concentration 50%+ |

Hyphenated public names such as `alpha-hunter` and `kimchi-audit-picks` are accepted by the CLI and normalized to the canonical registry names above.

## APPROXIMATION marker

Scanners marked `Yes` have private formulas. Their `compute()` methods use inferred defaults from public UI text, with code-level `APPROXIMATION` comments and tunable parameters.

Example:

```bash
bt scan-backtest --name alpha_terminal --features features.parquet \
  --params '{"alpha_terminal": {"score_threshold": 70, "w_vwap": 30}}'
```

## Required upstream data

| Feature | Required source/type |
|---------|----------------------|
| `kimp` | Upbit OHLCV + Binance OHLCV + FX |
| `oi_z`, `oi_velocity` | Binance `oi_hist` or Binance Vision `metrics` |
| `cvd` | Binance `trades` |
| `funding_rate` | Binance `funding` |
| `liq_intensity`, `liq_z`, `liq_imbalance` | `bt collect-liq` live capture, or Coinalyze/Coinglass with API key |
| `taker_ratio` | Binance `taker_ratio` or Binance Vision `metrics` |
| `toptrader_lsr` | Binance `lsr_top_pos` or Binance Vision `metrics` |
| `rs_btc` | `bt.features.rs` with aligned BTC close in the feature parquet |
| `stage` | `bt.features.stage_gate` with `oi_z`, `cvd`, and `funding_rate` |

## Known gaps

- Binance free historical liquidation data is not available. Binance WebSocket liquidation capture is live-forward only.
- Coinalyze and Coinglass historical liquidation adapters require user-owned API keys.
- `kimchi_audit` is a universe-selection scanner over pair metadata, not a bar-timing scanner.
