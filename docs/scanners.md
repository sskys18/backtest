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
| `liq_intensity`, `liq_z`, `liq_imbalance` | `coinank` source (free, historical), `bt collect-liq` live capture, or Coinalyze/Coinglass with API key |
| `taker_ratio` | Binance `taker_ratio` or Binance Vision `metrics` |
| `toptrader_lsr` | Binance `lsr_top_pos` or Binance Vision `metrics` |
| `rs_btc` | `bt.features.rs` with aligned BTC close in the feature parquet |
| `stage` | `bt.features.stage_gate` with `oi_z`, `cvd`, and `funding_rate` |

## Known gaps

- Binance does not publish historical liquidations on REST or Vision dump. `bt collect-liq` captures live forward via WebSocket; `coinank` source provides aggregated historical (180d at 1d, 30d at 4h, 7.5d at 1h).
- CoinAnk uses a browser-embedded API key. If 403 ("system error") returns, wait for the rate limit (typically minutes) or refresh the key from `coinank.com` network tab into env `COINANK_APIKEY`.
- Coinalyze and Coinglass historical liquidation adapters require user-owned API keys.
- `kimchi_audit` is a universe-selection scanner over pair metadata, not a bar-timing scanner.

## Tuning workflow

For the four APPROXIMATION scanners (`alpha_hunter`, `alpha_terminal`, `nahonja`, `squeeze_hunter`), tune with:

```bash
bt scanner-tune --name all --features path/to/features.parquet --trials 200 --horizon 12 --tp 0.02 --sl 0.01
```

Best params per scanner save under `experiments/scanner_tuning/<name>_best.yaml`. Apply by passing `--params` to `bt scan-backtest`.

Objective is forward-return Sharpe with TP/SL clamp; signal rate above 15% is penalized to avoid degenerate "always signal" solutions. With historical liquidation data via `coinank`, all four scanners (including `squeeze_hunter`) can be tuned end-to-end.

See `docs/investigations/2026-05-02-historical-liquidations.md` for the full data-source exploration that led to the CoinAnk choice.
