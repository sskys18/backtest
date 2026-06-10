//! Additive multi-asset perp PortfolioEngine (P1).
//!
//! Signed-position portfolio with collateral/margin accounting, perp funding,
//! strict per-bar order, order-priority netting, weight validation, fx PIT
//! conversion, and a leak-proof bias point-in-time ingestion gate.
//!
//! This module is **pure Rust** — every validation and simulation rule lives in
//! `run_panel_core` so the engine is fully testable with `cargo test`
//! (`default = []`, no `python` feature). `python.rs` ships only a thin numpy →
//! slice converter that calls into this core.
//!
//! NOTE: do not `use crate::types::*` here — `types::FillModel` would collide
//! with this module's self-contained `FillModel`.

use std::collections::HashMap;
use thiserror::Error;

// ---------------------------------------------------------------------------
// Config + enums
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FillModel {
    NextBar,
    Immediate,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ViolationMode {
    Reject,
    Renormalize,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum CapKind {
    Hard,
    Soft { tolerance: f64 },
}

/// Bias point-in-time ingestion mode (C1 structural seam, P1.6).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BiasMode {
    /// Default: any non-None bias is rejected.
    Forbidden,
    Backtest,
    Forward,
}

#[derive(Debug, Clone)]
pub struct PortfolioConfig {
    pub initial_collateral: f64,
    pub fee_rate: f64,
    pub slippage_bps: f64,
    pub im_rate: f64,
    pub mm_rate: f64,
    pub leverage_cap: f64,
    pub max_net: f64,
    pub max_per_asset: f64,
    pub rebalance_freq: usize,
    pub fill_model: FillModel,
    pub session_aware: bool,
    pub on_violation: ViolationMode,
    pub cap_kind: CapKind,
    pub numeraire_is_unit_fx: bool,
}

impl Default for PortfolioConfig {
    fn default() -> Self {
        Self {
            initial_collateral: 100_000.0,
            fee_rate: 0.0,
            slippage_bps: 0.0,
            im_rate: 0.1,
            mm_rate: 0.05,
            leverage_cap: 1.0,
            max_net: 1.0,
            max_per_asset: 1.0,
            rebalance_freq: 1,
            fill_model: FillModel::NextBar,
            session_aware: false,
            on_violation: ViolationMode::Reject,
            cap_kind: CapKind::Hard,
            numeraire_is_unit_fx: true,
        }
    }
}

// ---------------------------------------------------------------------------
// Results
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct TradeRec {
    pub bar: usize,
    pub asset: usize,
    pub qty: f64,    // signed Δqty filled (numeraire-neutral; native qty)
    pub price: f64,  // native fill price (pre-fx)
    pub fee: f64,    // in numeraire
    pub fx: f64,     // fx applied at fill bar
}

#[derive(Debug, Clone, Default)]
pub struct PortfolioResult {
    pub equity: Vec<f64>,
    pub gross_exposure: Vec<f64>,
    pub net_exposure: Vec<f64>,
    pub funding_pnl: Vec<f64>,
    pub turnover: Vec<f64>,
    pub realized_pnl: Vec<f64>,
    pub trades: Vec<TradeRec>,
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

#[derive(Debug, Error)]
pub enum EngineError {
    #[error("shape mismatch: {0}")]
    Shape(String),
    #[error("weight violation at (t={t}, asset={asset}, value={value}): {reason}")]
    WeightViolation {
        t: usize,
        asset: usize,
        value: f64,
        reason: String,
    },
    #[error("bias rejected: {0}")]
    BiasRejected(String),
    #[error("config error: {0}")]
    Config(String),
}

// ---------------------------------------------------------------------------
// Order priority (C8). Higher discriminant = higher priority.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum OrderPriority {
    Rebalance = 0,
    HardCapReduce = 1,
    MarginReduce = 2,
    SessionFlat = 3,
    Liquidation = 4,
}

/// A single net pending order for one asset, fillable at t+1.
#[derive(Debug, Clone, Copy)]
pub struct PendingOrder {
    pub target_qty: f64, // absolute target signed qty after fill
    pub priority: OrderPriority,
}

// ---------------------------------------------------------------------------
// Bias gate (C1, P1.6) — pure fn, callable from core + tests.
// ---------------------------------------------------------------------------

/// Validate the bias artifact at ingestion. Returns Ok(()) when the bias may be
/// applied, Err(BiasRejected) otherwise. Pure — no engine state needed.
///
/// - `bias`: optional [T*N] row-major matrix.
/// - `available_at`: optional [T] — the timestamp the bias value for row t
///   became knowable.
/// - `decision_time`: [T] — the decision timestamp for row t (when W[t] is
///   decided). The order fills next bar.
/// - `mode`: Forbidden (default) rejects any non-None bias.
pub fn validate_bias(
    bias: Option<&[f64]>,
    available_at: Option<&[i64]>,
    decision_time: &[i64],
    mode: BiasMode,
    t: usize,
    n: usize,
) -> Result<(), EngineError> {
    match (bias, mode) {
        (None, _) => Ok(()),
        (Some(_), BiasMode::Forbidden) => Err(EngineError::BiasRejected(
            "bias is forbidden by default (mode=forbidden); pass mode in {backtest,forward}".into(),
        )),
        (Some(b), BiasMode::Backtest) | (Some(b), BiasMode::Forward) => {
            if b.len() != t * n {
                return Err(EngineError::BiasRejected(format!(
                    "bias shape must be [T*N]={}, got {}",
                    t * n,
                    b.len()
                )));
            }
            // Non-finite (NaN/inf) bias must be rejected explicitly: NaN folds
            // into W and slips the weight caps (`NaN > cap` is false), silently
            // corrupting the run. Reject at ingestion before any tilt is applied.
            for (i, v) in b.iter().enumerate() {
                if !v.is_finite() {
                    return Err(EngineError::BiasRejected(format!(
                        "bias must be finite: bias[{i}]={v} is not finite"
                    )));
                }
            }
            // Per-t availability is mandatory: a scalar / window-constant bias
            // (no per-t available_at) is rejected — there is no "current view".
            let av = available_at.ok_or_else(|| {
                EngineError::BiasRejected(
                    "bias requires per-t available_at (scalar/window-constant bias rejected)".into(),
                )
            })?;
            if av.len() != t {
                return Err(EngineError::BiasRejected(format!(
                    "available_at must have length T={}, got {} (scalar/window-constant rejected)",
                    t,
                    av.len()
                )));
            }
            // Window-constant availability: a length-T available_at whose entries
            // are all identical is "today's view stamped backward across the
            // fold" — the §13 leak the two-layer gate exists to close. A direct
            // run_panel caller bypasses bias.py's check, so the seam must reject
            // it here too (not merely "in prose" / Python-only).
            if t > 1 && av.iter().all(|&x| x == av[0]) {
                return Err(EngineError::BiasRejected(
                    "available_at is window-constant (all identical) — today's \
                     view stamped backward is forbidden".into(),
                ));
            }
            for ti in 0..t {
                if av[ti] > decision_time[ti] {
                    return Err(EngineError::BiasRejected(format!(
                        "future bias: available_at[{ti}]={} > decision_time[{ti}]={}",
                        av[ti], decision_time[ti]
                    )));
                }
            }
            Ok(())
        }
    }
}

// ---------------------------------------------------------------------------
// Core engine
// ---------------------------------------------------------------------------

/// Enqueue a pending order with priority netting (C8). A new order replaces
/// any lower-or-equal-priority pending order for the same asset; a strictly
/// higher-priority pending order is kept (the new lower one is dropped/netted).
/// Single net pending per asset.
fn enqueue(pending: &mut HashMap<usize, PendingOrder>, asset: usize, order: PendingOrder) {
    match pending.get(&asset) {
        Some(existing) if existing.priority > order.priority => { /* keep higher-priority */ }
        _ => {
            pending.insert(asset, order);
        }
    }
}

/// Matrices are row-major `[T*N]` (row t, col a at index `t*n + a`).
/// `assets` is the canonical positional column order: column k == assets[k].
#[allow(clippy::too_many_arguments)]
pub fn run_panel_core(
    assets: &[String],
    t: usize,
    n: usize,
    close: &[f64],
    open: &[f64],
    funding: &[f64],
    fx: &[f64],
    weights: &[f64],
    active_mask: &[bool],
    sessions: &[u8],
    decision_time: &[i64],
    cfg: &PortfolioConfig,
    bias: Option<&[f64]>,
    bias_available_at: Option<&[i64]>,
    bias_mode: BiasMode,
) -> Result<PortfolioResult, EngineError> {
    // ---- Alignment validation (C5) ----
    if assets.len() != n {
        return Err(EngineError::Shape(format!(
            "len(assets)={} != N={}",
            assets.len(),
            n
        )));
    }
    let need = t * n;
    for (name, m) in [
        ("close", close),
        ("open", open),
        ("funding", funding),
        ("fx", fx),
        ("weights", weights),
    ] {
        if m.len() != need {
            return Err(EngineError::Shape(format!(
                "{name} has len {} != T*N {}",
                m.len(),
                need
            )));
        }
    }
    if active_mask.len() != need {
        return Err(EngineError::Shape(format!(
            "active_mask has len {} != T*N {}",
            active_mask.len(),
            need
        )));
    }
    if sessions.len() != need {
        return Err(EngineError::Shape(format!(
            "sessions has len {} != T*N {}",
            sessions.len(),
            need
        )));
    }
    if decision_time.len() != t {
        return Err(EngineError::Shape(format!(
            "decision_time has len {} != T {}",
            decision_time.len(),
            t
        )));
    }
    if cfg.rebalance_freq == 0 {
        return Err(EngineError::Config("rebalance_freq must be >= 1".into()));
    }

    // ---- Bias ingestion gate (C1, P1.6) ----
    validate_bias(bias, bias_available_at, decision_time, bias_mode, t, n)?;

    let idx = |ti: usize, a: usize| ti * n + a;
    let fxv = |ti: usize, a: usize| if cfg.numeraire_is_unit_fx { 1.0 } else { fx[idx(ti, a)] };

    // ---- Apply accepted bias tilt BEFORE validation (so tilt cannot smuggle
    // gross past the caps — C5/§4 caps bind on the traded weights) ----
    let mut w: Vec<f64> = weights.to_vec();
    if let Some(b) = bias {
        if bias_mode != BiasMode::Forbidden {
            for i in 0..need {
                w[i] += b[i];
            }
        }
    }

    // ---- Weight validation (C5, §4) on every row, before simulating ----
    // Returns a possibly-renormalized owned weight matrix.
    for ti in 0..t {
        // active-mask: NaN/inactive cell with nonzero W → hard-fail (never renormalized away).
        for a in 0..n {
            let wv = w[idx(ti, a)];
            // Non-finite resulting weight (NaN/inf, e.g. from a bad bias fold or
            // upstream weight) must hard-fail: a NaN slips every `> cap` check.
            if !wv.is_finite() {
                return Err(EngineError::WeightViolation {
                    t: ti,
                    asset: a,
                    value: wv,
                    reason: "weight is not finite (NaN/inf)".into(),
                });
            }
            if wv != 0.0 && (!active_mask[idx(ti, a)] || close[idx(ti, a)].is_nan()) {
                return Err(EngineError::WeightViolation {
                    t: ti,
                    asset: a,
                    value: wv,
                    reason: "nonzero weight on inactive/NaN cell".into(),
                });
            }
        }
        let mut gross = 0.0;
        let mut net = 0.0;
        for a in 0..n {
            let wv = w[idx(ti, a)];
            gross += wv.abs();
            net += wv;
            if wv.abs() > cfg.max_per_asset + 1e-12 {
                match cfg.on_violation {
                    ViolationMode::Reject => {
                        return Err(EngineError::WeightViolation {
                            t: ti,
                            asset: a,
                            value: wv,
                            reason: format!("|W| > max_per_asset {}", cfg.max_per_asset),
                        })
                    }
                    ViolationMode::Renormalize => {}
                }
            }
        }
        let gross_viol = gross > cfg.leverage_cap + 1e-12;
        let net_viol = net.abs() > cfg.max_net + 1e-12;
        if gross_viol || net_viol {
            match cfg.on_violation {
                ViolationMode::Reject => {
                    let (asset, value, reason) = if gross_viol {
                        (0, gross, format!("gross Σ|W| > leverage_cap {}", cfg.leverage_cap))
                    } else {
                        (0, net, format!("|net| > max_net {}", cfg.max_net))
                    };
                    return Err(EngineError::WeightViolation { t: ti, asset, value, reason });
                }
                ViolationMode::Renormalize => {
                    if gross_viol && gross > 0.0 {
                        let scale = cfg.leverage_cap / gross;
                        for a in 0..n {
                            w[idx(ti, a)] *= scale;
                        }
                    }
                }
            }
        }
    }

    // ---- Account state ----
    let collateral = cfg.initial_collateral; // constant base (no deposit/withdraw)
    let mut positions = vec![0.0_f64; n]; // signed qty (native units)
    // Numeraire cost basis of the current signed position (PIT fx locked in).
    // cost_basis_num = qty * entry_px * entry_fx. unrealized = qty*close*fx[t] - cost_basis_num.
    let mut cost_basis_num = vec![0.0_f64; n];
    let mut realized_cum = 0.0_f64; // numeraire
    let mut funding_cum = 0.0_f64; // numeraire
    let mut fees_cum = 0.0_f64; // numeraire
    let mut peak_equity = collateral;
    let mut halted = false;

    let mut pending: HashMap<usize, PendingOrder> = HashMap::new();

    let mut res = PortfolioResult {
        equity: Vec::with_capacity(t),
        gross_exposure: Vec::with_capacity(t),
        net_exposure: Vec::with_capacity(t),
        funding_pnl: Vec::with_capacity(t),
        turnover: Vec::with_capacity(t),
        realized_pnl: Vec::with_capacity(t),
        trades: Vec::new(),
    };

    for ti in 0..t {
        let mut bar_funding = 0.0_f64;
        let mut bar_turnover = 0.0_f64;

        // ---- Step 1: funding on carried (pre-fill) book, mark close[t-1] (C3) ----
        if ti > 0 {
            for a in 0..n {
                let qty = positions[a];
                if qty != 0.0 && funding[idx(ti, a)] != 0.0 {
                    let prev_close = close[idx(ti - 1, a)];
                    if !prev_close.is_nan() {
                        let notional = qty * prev_close * fxv(ti - 1, a);
                        // long pays positive funding, short receives.
                        let f = -notional * funding[idx(ti, a)];
                        bar_funding += f;
                        funding_cum += f;
                    }
                }
            }
        }

        // ---- Step 2: fill pending orders from t-1 at open[t] (next_bar) ----
        if !halted {
            drain_fills(
                &mut pending,
                &mut positions,
                &mut cost_basis_num,
                &mut realized_cum,
                &mut fees_cum,
                &mut bar_turnover,
                &mut res.trades,
                open,
                &fxv,
                cfg,
                ti,
                n,
            );
        }

        // ---- Step 3: MTM at close[t] ----
        let unrealized = compute_unrealized(&positions, &cost_basis_num, close, &fxv, ti, n);
        let equity = collateral + realized_cum + unrealized + funding_cum - fees_cum;
        if equity > peak_equity {
            peak_equity = equity;
        }

        // gross/net exposure in numeraire / equity.
        let mut gross_notional = 0.0;
        let mut net_notional = 0.0;
        for a in 0..n {
            let c = close[idx(ti, a)];
            if !c.is_nan() && positions[a] != 0.0 {
                let nv = positions[a] * c * fxv(ti, a);
                gross_notional += nv.abs();
                net_notional += nv;
            }
        }
        let eq_div = if equity.abs() < 1e-12 { 1.0 } else { equity };
        res.equity.push(equity);
        res.gross_exposure.push(gross_notional / eq_div);
        res.net_exposure.push(net_notional / eq_div);
        res.funding_pnl.push(bar_funding);
        res.turnover.push(bar_turnover); // per-bar notional traded
        res.realized_pnl.push(realized_cum); // cumulative

        if halted {
            continue;
        }

        // ---- Step 6 (margin) — wipeout / maintenance breach ----
        let maint = positions
            .iter()
            .enumerate()
            .map(|(a, q)| {
                let c = close[idx(ti, a)];
                if c.is_nan() {
                    0.0
                } else {
                    (q * c * fxv(ti, a)).abs() * cfg.mm_rate
                }
            })
            .sum::<f64>();
        if equity <= 0.0 {
            // total wipeout: flatten immediately at close[t], book realized, halt.
            pending.clear();
            for a in 0..n {
                if positions[a] != 0.0 {
                    let c = close[idx(ti, a)];
                    if c.is_nan() {
                        continue;
                    }
                    let fx_c = fxv(ti, a);
                    let dq = -positions[a];
                    let notional = dq.abs() * c * fx_c;
                    let fee = notional * cfg.fee_rate;
                    fees_cum += fee;
                    apply_fill(
                        &mut positions[a],
                        &mut cost_basis_num[a],
                        &mut realized_cum,
                        0.0,
                        c,
                        fx_c,
                    );
                    res.trades.push(TradeRec {
                        bar: ti,
                        asset: a,
                        qty: dq,
                        price: c,
                        fee,
                        fx: fx_c,
                    });
                }
            }
            halted = true;
        } else if equity < maint {
            // force-reduce toward maintenance bound: scale book down.
            let scale = if maint > 0.0 { (equity / maint).clamp(0.0, 1.0) } else { 1.0 };
            for a in 0..n {
                if positions[a] != 0.0 {
                    enqueue(
                        &mut pending,
                        a,
                        PendingOrder {
                            target_qty: positions[a] * scale,
                            priority: OrderPriority::MarginReduce,
                        },
                    );
                }
            }
        }

        if halted {
            continue;
        }

        // ---- Step 4-5: rebalance to target (only on rebalance bar) ----
        if ti % cfg.rebalance_freq == 0 {
            for a in 0..n {
                let c = close[idx(ti, a)];
                if c.is_nan() {
                    continue;
                }
                // Inactive cell (e.g. session==0 closed row on a full_calendar
                // daily panel): no orders may originate here — neither opening
                // (W there is forced to 0 by validation) nor a flatten of a
                // carried book. The position is held through the closure and
                // W resumes control on the next active bar.
                if !active_mask[idx(ti, a)] {
                    continue;
                }
                let wv = w[idx(ti, a)]; // bias already folded in pre-validation
                let target_notional = equity * wv; // numeraire
                let px_num = c * fxv(ti, a);
                let target_qty = if px_num.abs() < 1e-12 { 0.0 } else { target_notional / px_num };
                if (target_qty - positions[a]).abs() > 1e-12 {
                    enqueue(
                        &mut pending,
                        a,
                        PendingOrder { target_qty, priority: OrderPriority::Rebalance },
                    );
                }
            }
        }

        // ---- Step 7: cap force-reduce every bar (C7, hard vs soft) ----
        // Hard cap: bind on the leverage cap itself every bar. Soft cap: allow
        // drift within `tolerance`, force-reduce ONLY beyond the tolerance band,
        // i.e. against `leverage_cap * (1 + tolerance)`. tolerance=0.0 makes Soft
        // byte-identical to Hard. Either way, the reduce scales the book back to
        // the *cap* (leverage_cap), not the tolerance edge.
        let effective_cap = match cfg.cap_kind {
            CapKind::Hard => cfg.leverage_cap,
            CapKind::Soft { tolerance } => cfg.leverage_cap * (1.0 + tolerance),
        };
        // re-evaluate gross against the effective cap on current book.
        if gross_notional / eq_div > effective_cap + 1e-12 {
            let scale = (cfg.leverage_cap * eq_div) / gross_notional;
            for a in 0..n {
                if positions[a] != 0.0 {
                    enqueue(
                        &mut pending,
                        a,
                        PendingOrder {
                            target_qty: positions[a] * scale,
                            priority: OrderPriority::HardCapReduce,
                        },
                    );
                }
            }
        }

        // ---- Step 8: session force-flat (intraday only) ----
        if cfg.session_aware {
            for a in 0..n {
                // session 4=close, 0=closed, 5=overnight → force flat.
                let s = sessions[idx(ti, a)];
                if (s == 4 || s == 0 || s == 5) && positions[a] != 0.0 {
                    enqueue(
                        &mut pending,
                        a,
                        PendingOrder { target_qty: 0.0, priority: OrderPriority::SessionFlat },
                    );
                }
            }
        }

        // ---- Step 5/6/8 `immediate` ablation (spec §5): fill SAME bar at
        // close[t] instead of next-bar open[t+1]. Drained AFTER all enqueues so
        // C8 priority netting is preserved. The equity/exposure series for this
        // bar were pushed at step 3 (pre-fill), so they are stale-by-one on
        // immediate bars — acceptable for a documented ablation. NextBar (the
        // default) leaves `pending` to be drained at the next bar's step 2. ----
        if cfg.fill_model == FillModel::Immediate && !halted {
            drain_fills(
                &mut pending,
                &mut positions,
                &mut cost_basis_num,
                &mut realized_cum,
                &mut fees_cum,
                &mut bar_turnover,
                &mut res.trades,
                close,
                &fxv,
                cfg,
                ti,
                n,
            );
            // bar_turnover was already pushed at step 3; reflect immediate fills.
            if let Some(last) = res.turnover.last_mut() {
                *last = bar_turnover;
            }
        }
    }

    Ok(res)
}

/// Fill every pending order at `price[t,a]` (slippage applied), booking fees,
/// turnover, realized PnL and a trade record, then clearing the pending map.
/// Shared by step 2 (next_bar, price = open[t]) and the `immediate` ablation
/// (price = close[t]); draining the same map after all enqueues preserves C8
/// priority netting for free.
#[allow(clippy::too_many_arguments)]
fn drain_fills<F: Fn(usize, usize) -> f64>(
    pending: &mut HashMap<usize, PendingOrder>,
    positions: &mut [f64],
    cost_basis_num: &mut [f64],
    realized_cum: &mut f64,
    fees_cum: &mut f64,
    bar_turnover: &mut f64,
    trades: &mut Vec<TradeRec>,
    price: &[f64],
    fxv: &F,
    cfg: &PortfolioConfig,
    ti: usize,
    n: usize,
) {
    let slip = cfg.slippage_bps / 10_000.0;
    let pend_now: Vec<(usize, PendingOrder)> = pending.iter().map(|(a, o)| (*a, *o)).collect();
    for (a, ord) in pend_now {
        let raw_px = price[ti * n + a];
        if raw_px.is_nan() {
            continue;
        }
        let cur = positions[a];
        let dq = ord.target_qty - cur;
        if dq == 0.0 {
            pending.remove(&a);
            continue;
        }
        // slippage worsens fill: buy higher, sell lower.
        let fill_px = if dq > 0.0 {
            raw_px * (1.0 + slip)
        } else {
            raw_px * (1.0 - slip)
        };
        let fx_fill = fxv(ti, a);
        let notional = dq.abs() * fill_px * fx_fill;
        let fee = notional * cfg.fee_rate;
        *fees_cum += fee;
        *bar_turnover += notional;

        // realized PnL + cost-basis update via numeraire cost basis.
        apply_fill(
            &mut positions[a],
            &mut cost_basis_num[a],
            realized_cum,
            ord.target_qty,
            fill_px,
            fx_fill,
        );

        trades.push(TradeRec {
            bar: ti,
            asset: a,
            qty: dq,
            price: fill_px,
            fee,
            fx: fx_fill,
        });
        pending.remove(&a);
    }
}

/// Apply a fill moving signed qty from `*pos` to `target`, updating the
/// numeraire cost basis and booking realized PnL on any closed portion.
///
/// Cost basis is carried in numeraire (`cost_basis_num = qty·entry_px·entry_fx`),
/// so PIT fx is locked at entry by construction:
/// - open/add same dir:  `cost_basis_num += Δq·fill_px·fill_fx`
/// - reduce same dir:     realize on closed qty at (fill − cb_unit); scale basis
/// - flip:                realize full old qty, basis = new qty·fill_px·fill_fx
///
/// This makes equity continuous across a fill (up to fee): the asset's
/// pre-fill contribution `q_old·(px·fx − cb_unit)` equals
/// `realized + q_new·(px·fx − cb_unit)`.
fn apply_fill(
    pos: &mut f64,
    cost_basis_num: &mut f64,
    realized_cum: &mut f64,
    target: f64,
    fill_px: f64,
    fill_fx: f64,
) {
    let cur = *pos;
    let px_num = fill_px * fill_fx; // per-unit numeraire price at fill

    if cur == 0.0 {
        // fresh open
        *cost_basis_num = target * px_num;
        *pos = target;
        return;
    }

    let same_dir = (cur > 0.0 && target >= 0.0) || (cur < 0.0 && target <= 0.0);
    let cb_unit = *cost_basis_num / cur; // per-unit basis (numeraire), signed-consistent

    if same_dir {
        if target.abs() >= cur.abs() {
            // adding in same direction: extend basis by the added qty.
            let add = target - cur; // same sign as cur
            *cost_basis_num += add * px_num;
        } else {
            // reducing same direction: realize on the closed portion.
            let closed = cur - target; // qty removed, same sign as cur
            *realized_cum += closed * (px_num - cb_unit);
            *cost_basis_num = target * cb_unit; // remaining basis at original unit cost
        }
    } else {
        // flip through zero: realize the entire old position, open the residual.
        *realized_cum += cur * (px_num - cb_unit);
        *cost_basis_num = target * px_num;
    }
    *pos = target;
}

fn compute_unrealized<F: Fn(usize, usize) -> f64>(
    positions: &[f64],
    cost_basis_num: &[f64],
    close: &[f64],
    fxv: &F,
    ti: usize,
    n: usize,
) -> f64 {
    let mut u = 0.0;
    for a in 0..n {
        let q = positions[a];
        if q == 0.0 {
            continue;
        }
        let c = close[ti * n + a];
        if c.is_nan() {
            continue;
        }
        // value at current fx minus cost basis (entry fx locked in basis).
        u += q * c * fxv(ti, a) - cost_basis_num[a];
    }
    u
}

#[cfg(test)]
mod tests;
