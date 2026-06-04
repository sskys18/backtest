# Engine Reference Repos — Multi-Asset Quant Backtest Engine

> Compiled 2026-06-04. Stars + last-push dates verified via GitHub API on that date.
> Companion to `docs/specs/2026-06-04-multi-asset-quant-engine-design.md`. Recency = last push to default branch.

The build's axis: **panel-vectorized signal `(T×N)` → event-driven multi-asset portfolio exec**.
No single OSS engine does both halves well — borrow the signal layer from one, the exec layer from another.

## 1. Backtest engines (panel ↔ event axis)

| Repo | What | Stars / recency | Borrow |
|---|---|---|---|
| [nautilus_trader](https://github.com/nautechsystems/nautilus_trader) | Rust-native deterministic event-driven multi-venue engine | 23.3k · 2026-06-04 (daily) | **Exec-layer reference.** `Cache`/`Portfolio` event-state model + message-bus actor arch; nanosecond deterministic replay; multi-venue account/position + **perp funding** first-class. |
| [qlib](https://github.com/microsoft/qlib) (MSFT) | AI quant platform, cross-sectional equity | 44k · 2026-04-22 | **Signal-layer / T×N reference.** `Alpha158`/`Alpha360` panel feature handler, `TopkDropoutStrategy` (signed CS ranking → positions), expression engine for vectorized factors. Take patterns, not the heavy dep. |
| [vectorbt](https://github.com/polakowo/vectorbt) | Vectorized backtest over broadcast NumPy | 7.8k · 2026-04-25 | `Portfolio.from_signals`/`from_orders` broadcast order-gen (T×N×params one call). Note: **vectorbtPRO is closed/commercial** — borrow PRO ideas from public docs only. |
| [QuantConnect/Lean](https://github.com/QuantConnect/Lean) | C#(+Py) institutional multi-asset | 19.7k · 2026-06-03 (daily) | Architecture only: `Universe`→`PortfolioConstruction`→`RiskManagement`→`Execution` separated pipeline. C#-first; reference, not code source. |

**Cut by the axis (cautionary):**
- [backtesting.py](https://github.com/kernc/backtesting.py) (8.5k · 2025-12-20) — **single-asset only**, disqualified. Clean API to study.
- [bt](https://github.com/pmorissette/bt) (2.9k · 2026-05-05) — daily-rebalance weighting only; no intraday/event/short/perp.
- [zipline-reloaded](https://github.com/stefan-jansen/zipline-reloaded) (1.8k · 2026-01-06) — US-equity/daily DNA, calendar-bound, no perp/funding. Legacy ref.

## 2. Anti-overfit / walk-forward (de Prado)

Category is **thin** — DSR/PBO/CPCV are normally self-implemented from the papers. One repo with live 2026 activity:

| Repo | What | Stars / recency | Verdict |
|---|---|---|---|
| [skfolio](https://github.com/skfolio/skfolio) | sklearn-style portfolio opt | 2.0k · 2026-05-29 | **The one live CPCV.** `model_selection.CombinatorialPurgedCV` (purge+embargo, sklearn splitter) — cleanest API to copy. Has walk-forward + `RatioMeasure`. |
| ~~mlfinlab~~ | de Prado AFML toolkit | 4.8k · **2023-10 stale** | Canonical algos (purged k-fold, embargo, PBO via CSCV) but paywalled+unmaintained. Read algos, don't depend. |
| ~~pypbo~~ | Prob. of Backtest Overfit | 133 · **2023-06 stale** | CSCV→PBO computation reference only. |
| ~~timeseriescv~~ | Purged/combinatorial CV | 288 · **2022-02 stale** | Superseded by skfolio. |

**Recommendation:** CPCV from skfolio; **hand-roll DSR + PBO** from Bailey & López de Prado (2014/2016) — ~100 lines each, put in Rust core for sweep speed.

## 3. Rust cores + PyO3 bindings

| Repo | What | Stars / recency | Borrow |
|---|---|---|---|
| [nautilus_trader](https://github.com/nautechsystems/nautilus_trader) | Rust core + PyO3 | 23.3k · daily | Production ref. Crate layout (`nautilus-core`/`-model`/`-backtest`), Cython→PyO3 migration — direct for our `crates/bt-engine` + maturin. |
| [hftbacktest](https://github.com/nkaz001/hftbacktest) | Full-tick L2/L3 HFT backtester, Rust | 4.1k · 2025-12-23 | Queue-position + latency modeling, orderbook replay loop — if pushing to true microstructure (Upbit/Bithumb imbalance edge). Heavier than mid-term needs. |
| [raptorbt](https://github.com/alphabench/raptorbt) | Rust+PyO3 vectorbt drop-in, metric parity | **22 · 2026-06-03** | **Closest structural analog** to our build. Borrow metric-parity test layout + PyO3 array-boundary design. 22 stars — blueprint only, do NOT depend. |

## 4. LLM trading-agent frameworks — forward-only thesis lane (§13)

**Leakage:** LLM has a training cutoff → on pre-cutoff history it has lookahead (its "analysis" encodes knowing what happened next). Any backtest over that window = fictional Sharpe. Use **forward/live-from-today only** as a discretionary thesis/sector layer ON TOP of the validated engine — never in the backtest loop.

| Repo | What | Stars / recency | Use |
|---|---|---|---|
| [TradingAgents](https://github.com/TauricResearch/TradingAgents) | Multi-agent LLM firm (analyst/researcher/trader/risk debate) | 82.8k · 2026-06-01 | Agent-role decomposition pattern (fundamental/sentiment/technical→trader→risk). Forward-only. |
| [ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) | LLM hedge-fund team, persona analysts | 59.8k · 2026-05-28 | Lighter LangGraph agent-graph for sector/fundamental thesis. Forward-only. |
| [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot) | AI4Finance equity-research agents | 7.2k · 2026-05-10 | Structured fundamental write-up step. Forward-only. |

## Bottom line for the build

- **Signal (T×N):** qlib data-handler + ranking-strategy patterns; vectorbt broadcast-order.
- **Event exec:** nautilus_trader Cache/Portfolio/message-bus; perp funding + multi-venue first-class there.
- **Rust+PyO3 scaffold:** nautilus crate layout (serious ref); raptorbt (small "exactly my shape" blueprint).
- **Anti-overfit:** skfolio `CombinatorialPurgedCV` splitter; hand-roll DSR + PBO from de Prado.
- **LLM agents:** TradingAgents role-decomposition, forward-only thesis overlay, strictly outside backtest.
