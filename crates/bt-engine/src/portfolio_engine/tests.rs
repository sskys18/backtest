//! P1 unit tests — every expected value hand-derived from spec §5 arithmetic,
//! NOT pasted from engine output.

use super::*;

fn cfg() -> PortfolioConfig {
    PortfolioConfig {
        initial_collateral: 100_000.0,
        fee_rate: 0.0,
        slippage_bps: 0.0,
        im_rate: 0.1,
        mm_rate: 0.05,
        leverage_cap: 10.0, // loose by default; tighten per-test
        max_net: 10.0,
        max_per_asset: 10.0,
        rebalance_freq: 1,
        fill_model: FillModel::NextBar,
        session_aware: false,
        on_violation: ViolationMode::Reject,
        cap_kind: CapKind::Hard,
        numeraire_is_unit_fx: true,
    }
}

/// Build a single-asset run helper. weights/funding length T.
#[allow(clippy::too_many_arguments)]
fn run1(
    close: &[f64],
    open: &[f64],
    funding: &[f64],
    weights: &[f64],
    cfg: &PortfolioConfig,
) -> PortfolioResult {
    let t = close.len();
    let n = 1;
    let active = vec![true; t];
    let sessions = vec![2u8; t];
    let fx = vec![1.0; t];
    let dt: Vec<i64> = (0..t as i64).collect();
    run_panel_core(
        &["A".into()],
        t,
        n,
        close,
        open,
        funding,
        &fx,
        weights,
        &active,
        &sessions,
        &dt,
        cfg,
        None,
        None,
        BiasMode::Forbidden,
    )
    .unwrap()
}

// ---------------------------------------------------------------------------
// Funding
// ---------------------------------------------------------------------------

#[test]
fn funding_accrual_on_carried_book() {
    // t0: open long via rebalance to W=1 at close[0]=100 → fills at open[1].
    // Actually next-bar fill: rebalance at t0 enqueues, fills open[1].
    // We want a position carried over [t1,t2] then funding charged at t2 on the
    // t1 book marked close[1], and NO charge at the bar the position opened.
    let mut c = cfg();
    c.im_rate = 0.1;
    c.leverage_cap = 1.0;
    c.max_net = 1.0;
    c.max_per_asset = 1.0;
    // close = open everywhere = 100 so qty math is clean.
    let close = [100.0, 100.0, 100.0, 100.0];
    let open = [100.0, 100.0, 100.0, 100.0];
    // funding nonzero at t1 (the bar the position OPENS/fills) AND t2.
    // The t1 charge must be 0 because funding runs on the PRE-fill carried book,
    // which is still flat at t1 (the open happens in t1's fill step, after funding).
    let funding = [0.0, 0.02, 0.01, 0.0];
    // hold W=1 throughout.
    let w = [1.0, 1.0, 1.0, 1.0];
    let r = run1(&close, &open, &funding, &w, &c);
    // Rebalance at t0: target_notional = equity(100000)*1 = 100000 → qty=1000.
    // Order fills at open[1]=100 → position 1000 from t1.
    // t1 funding (rate 0.02) runs on the carried pre-fill book = EMPTY → 0,
    // even though funding[1] != 0. Proves a newly-opened position isn't charged.
    assert_eq!(r.funding_pnl[1], 0.0, "new position must not be charged at its open bar");
    // Funding at t2 charged on carried t1 book: notional = 1000*close[1]=100000.
    // funding_pnl = -100000 * 0.01 = -1000 (long pays positive funding).
    assert!((r.funding_pnl[2] - (-1000.0)).abs() < 1e-6, "got {}", r.funding_pnl[2]);
    // No funding at t0,t3 (funding rate 0 there / no t-1 at t0).
    assert_eq!(r.funding_pnl[0], 0.0);
    assert_eq!(r.funding_pnl[3], 0.0);
}

#[test]
fn funding_mark_uses_prev_close() {
    // If funding used close[t] instead of close[t-1], the charge differs.
    let mut c = cfg();
    c.leverage_cap = 1.0;
    c.max_net = 1.0;
    c.max_per_asset = 1.0;
    let close = [100.0, 100.0, 200.0, 200.0]; // jump at t2
    let open = [100.0, 100.0, 100.0, 200.0];
    let funding = [0.0, 0.0, 0.01, 0.0];
    let w = [1.0, 1.0, 1.0, 1.0];
    let r = run1(&close, &open, &funding, &w, &c);
    // position from t1 = 1000 qty (target 100000 / open[1] 100).
    // funding at t2 marks close[t-1]=close[1]=100 → notional 1000*100=100000.
    // -> -1000. NOT close[2]=200 (which would give -2000).
    assert!((r.funding_pnl[2] - (-1000.0)).abs() < 1e-6, "got {}", r.funding_pnl[2]);
}

// ---------------------------------------------------------------------------
// Short PnL sign + no cash credit
// ---------------------------------------------------------------------------

#[test]
fn short_pnl_sign() {
    let mut c = cfg();
    c.leverage_cap = 1.0;
    c.max_net = 1.0;
    c.max_per_asset = 1.0;
    // short via W=-1. price falls → profit.
    let close = [100.0, 100.0, 90.0, 90.0];
    let open = [100.0, 100.0, 100.0, 90.0];
    let funding = [0.0, 0.0, 0.0, 0.0];
    let w = [-1.0, -1.0, -1.0, -1.0];
    let r = run1(&close, &open, &funding, &w, &c);
    // rebalance t0: target -100000 / open[1] 100 = -1000 qty from t1.
    // at t2, mark close=90: unrealized = qty*(close*fx) - cost_basis
    //   cost_basis = -1000 * (100*1) = -100000. value = -1000*90 = -90000.
    //   unrealized = -90000 - (-100000) = +10000  → equity rose. short profits.
    assert!(r.equity[2] > r.equity[1], "short should profit when price falls");
    assert!((r.equity[2] - 110_000.0).abs() < 1e-6, "got {}", r.equity[2]);

    // mirror: price rises → loss.
    let close2 = [100.0, 100.0, 110.0, 110.0];
    let open2 = [100.0, 100.0, 100.0, 110.0];
    let r2 = run1(&close2, &open2, &funding, &w, &c);
    assert!((r2.equity[2] - 90_000.0).abs() < 1e-6, "got {}", r2.equity[2]);
}

#[test]
fn short_open_no_cash_credit() {
    // Opening a short must NOT credit collateral by notional. With cost_basis
    // accounting, collateral is constant; at the open bar (before price moves)
    // equity == initial. close==open so no MTM move.
    let mut c = cfg();
    c.leverage_cap = 1.0;
    c.max_net = 1.0;
    c.max_per_asset = 1.0;
    let close = [100.0, 100.0, 100.0];
    let open = [100.0, 100.0, 100.0];
    let funding = [0.0, 0.0, 0.0];
    let w = [-1.0, -1.0, -1.0];
    let r = run1(&close, &open, &funding, &w, &c);
    // At t1 the short is open at 100, marked 100 → unrealized 0 → equity 100000.
    // A spot model crediting 100000 proceeds would show 200000.
    assert!((r.equity[1] - 100_000.0).abs() < 1e-6, "got {}", r.equity[1]);
}

// ---------------------------------------------------------------------------
// Equity invariant
// ---------------------------------------------------------------------------

#[test]
fn equity_invariant_holds_each_bar() {
    // Re-derive invariant directly: re-run a small scenario and check equity
    // equals collateral + realized + unrealized + funding - fees by
    // reconstructing from the result series is circular, so instead assert
    // monotone arithmetic on a known path (open long, price up, partial close).
    let mut c = cfg();
    c.fee_rate = 0.001;
    c.slippage_bps = 0.0;
    c.leverage_cap = 2.0; // headroom so price-driven gross drift doesn't trip hard cap
    c.max_net = 2.0;
    c.max_per_asset = 2.0;
    c.rebalance_freq = 100; // rebalance only at t0 (no re-trade)
    let close = [100.0, 110.0, 120.0, 130.0];
    let open = [100.0, 100.0, 100.0, 100.0];
    let funding = [0.0, 0.0, 0.0, 0.0];
    let w = [1.0, 0.0, 0.0, 0.0]; // target full long at t0 only
    let r = run1(&close, &open, &funding, &w, &c);
    // t0 rebalance: target 100000/open[1]=100 → 1000 qty, fills t1 open=100.
    // fee at fill = 1000*100*0.001 = 100.
    // t1 mark close 110: unrealized = 1000*110 - 1000*100 = 10000. equity = 100000+0+10000+0-100 = 109900.
    assert!((r.equity[1] - 109_900.0).abs() < 1e-6, "t1 got {}", r.equity[1]);
    // t2 mark 120: unrealized 20000 → equity 100000+20000-100 = 119900.
    assert!((r.equity[2] - 119_900.0).abs() < 1e-6, "t2 got {}", r.equity[2]);
    // t3 mark 130: unrealized 30000 → 129900.
    assert!((r.equity[3] - 129_900.0).abs() < 1e-6, "t3 got {}", r.equity[3]);
}

// ---------------------------------------------------------------------------
// Rebalance / drift
// ---------------------------------------------------------------------------

#[test]
fn rebalance_diff_enqueues_next_bar() {
    let mut c = cfg();
    c.leverage_cap = 1.0;
    c.max_net = 1.0;
    c.max_per_asset = 1.0;
    let close = [100.0, 100.0, 100.0];
    let open = [100.0, 105.0, 100.0]; // open[1] differs from close[0]
    let funding = [0.0, 0.0, 0.0];
    let w = [1.0, 1.0, 1.0];
    let r = run1(&close, &open, &funding, &w, &c);
    // first trade should fill at open[1]=105, not close[0]=100.
    assert_eq!(r.trades[0].bar, 1);
    assert!((r.trades[0].price - 105.0).abs() < 1e-9, "got {}", r.trades[0].price);
}

#[test]
fn immediate_fill_model_fills_same_bar_close() {
    // §5 step 5 ablation: fill_model == Immediate fills rebalance orders SAME bar
    // at close[t], vs the default NextBar which fills at open[t+1]. Use distinct
    // close[0] vs open[1] so the fill bar/price unambiguously identifies the path.
    let mut c = cfg();
    c.leverage_cap = 1.0;
    c.max_net = 1.0;
    c.max_per_asset = 1.0;
    // close[0]=100, open[1]=105 → the two paths fill at different bars AND prices.
    let close = [100.0, 100.0, 100.0];
    let open = [100.0, 105.0, 100.0];
    let funding = [0.0, 0.0, 0.0];
    let w = [1.0, 1.0, 1.0];

    // NextBar (default): rebalance at t0 enqueues, fills open[1]=105 at bar 1.
    let mut c_next = c.clone();
    c_next.fill_model = FillModel::NextBar;
    let r_next = run1(&close, &open, &funding, &w, &c_next);
    assert_eq!(r_next.trades[0].bar, 1, "next_bar must fill at t+1");
    assert!((r_next.trades[0].price - 105.0).abs() < 1e-9, "next_bar price {}", r_next.trades[0].price);

    // Immediate: rebalance at t0 fills SAME bar at close[0]=100.
    let mut c_imm = c.clone();
    c_imm.fill_model = FillModel::Immediate;
    let r_imm = run1(&close, &open, &funding, &w, &c_imm);
    assert_eq!(r_imm.trades[0].bar, 0, "immediate must fill at same bar t");
    assert!((r_imm.trades[0].price - 100.0).abs() < 1e-9, "immediate price {}", r_imm.trades[0].price);
}

#[test]
fn rebalance_freq_drift() {
    let mut c = cfg();
    c.rebalance_freq = 3;
    c.leverage_cap = 2.0; // headroom; not testing caps here
    c.max_net = 2.0;
    c.max_per_asset = 2.0;
    // flat prices so the only thing that can cause a re-trade is a W change at a
    // rebalance bar. W halves at t3 → forces a re-trade; non-rebalance bars never trade.
    let close = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0];
    let open = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0];
    let funding = [0.0; 6];
    let w = [1.0, 1.0, 1.0, 0.5, 0.5, 0.5];
    let r = run1(&close, &open, &funding, &w, &c);
    // rebalance bars: t0 (W=1 → open 1000 qty, fills t1), t3 (W=0.5 → target 500,
    // diff -500, fills t4). Non-rebalance bars t1,t2,t4,t5 do NOT re-trade.
    // So trades occur at bars {1, 4} only (position drifts/holds between).
    let trade_bars: Vec<usize> = r.trades.iter().map(|t| t.bar).collect();
    assert_eq!(trade_bars, vec![1, 4], "got {:?}", trade_bars);
}

// ---------------------------------------------------------------------------
// Margin breach / wipeout
// ---------------------------------------------------------------------------

#[test]
fn margin_breach_force_reduces() {
    // Heavy leverage, adverse move → equity below maintenance or wiped out.
    let mut c = cfg();
    c.leverage_cap = 5.0;
    c.max_net = 5.0;
    c.max_per_asset = 5.0;
    c.mm_rate = 0.05;
    c.rebalance_freq = 100; // open once
    // 5x long, price crashes 80%.
    let close = [100.0, 100.0, 20.0, 20.0];
    let open = [100.0, 100.0, 100.0, 20.0];
    let funding = [0.0; 4];
    let w = [5.0, 0.0, 0.0, 0.0];
    let r = run1(&close, &open, &funding, &w, &c);
    // t0 target 5*100000=500000 / open[1]=100 → 5000 qty from t1.
    // t2 mark 20: unrealized = 5000*20 - 5000*100 = -400000. equity=100000-400000 = -300000 <=0 → wipeout flat+halt.
    assert!(r.equity[2] <= 0.0, "t2 equity {}", r.equity[2]);
    // after wipeout the position must be flat: a liquidation trade exists at t2.
    let liq = r.trades.iter().any(|t| t.bar == 2 && (t.qty + 5000.0).abs() < 1e-6);
    assert!(liq, "expected flatten trade at wipeout, trades={:?}", r.trades.iter().map(|t|(t.bar,t.qty)).collect::<Vec<_>>());
    // halted: no further trades after t2.
    assert!(r.trades.iter().all(|t| t.bar <= 2));
}

#[test]
fn margin_breach_force_reduce_non_wipeout() {
    // Maintenance breach WITHOUT wipeout (0 < equity < maintenance): the engine
    // must enqueue a de-risk order scaling the book toward the bound, NOT halt.
    let mut c = cfg();
    c.leverage_cap = 20.0; // loose so the hard-cap branch doesn't fire (isolate margin)
    c.max_net = 20.0;
    c.max_per_asset = 20.0;
    c.mm_rate = 0.3; // high maintenance
    c.rebalance_freq = 100; // open once at t0
    // 3x long. price drops 20%.
    let close = [100.0, 100.0, 80.0, 80.0];
    let open = [100.0, 100.0, 100.0, 80.0];
    let funding = [0.0; 4];
    let w = [3.0, 0.0, 0.0, 0.0];
    let r = run1(&close, &open, &funding, &w, &c);
    // t0: target 3*100000=300000 / open[1]=100 → qty 3000 from t1.
    // t2 close 80: unrealized = 3000*(80-100) = -60000 → equity 40000 (>0, no wipeout).
    //   gross_notional = 3000*80 = 240000; maint = 240000*0.3 = 72000.
    //   equity 40000 < 72000 → margin breach. scale = 40000/72000 = 0.5556.
    //   target qty = 3000*0.5556 = 1666.67. de-risk order fills t3.
    assert!(r.equity[2] > 0.0, "should not be wiped out, equity {}", r.equity[2]);
    let reduce = r.trades.iter().find(|t| t.bar == 3).expect("expected margin de-risk trade at t3");
    // dq sells part of the long: 1666.67 - 3000 = -1333.33 (negative = sell).
    assert!(reduce.qty < 0.0, "margin reduce should sell, got {}", reduce.qty);
    assert!((reduce.qty + 1333.333).abs() < 1e-1, "reduce dq {}", reduce.qty);
}

// ---------------------------------------------------------------------------
// Hard cap
// ---------------------------------------------------------------------------

#[test]
fn hard_cap_force_reduce() {
    // A SHORT whose price RISES grows notional while shrinking equity, so
    // gross/equity drifts above a hard cap between rebalances → step 7 must
    // force-reduce the same bar (this is the branch a long can never reach,
    // since a long's gain feeds equity 1:1 with notional).
    let mut c = cfg();
    c.cap_kind = CapKind::Hard;
    c.leverage_cap = 1.0; // hard cap 1.0x gross
    c.max_net = 1.0;
    c.max_per_asset = 1.0;
    c.mm_rate = 0.05; // maintenance well below equity → no margin breach
    c.rebalance_freq = 100; // open at t0, never rebalance again
    // short at 100; price rises to 120.
    let close = [100.0, 100.0, 120.0, 120.0];
    let open = [100.0, 100.0, 100.0, 120.0];
    let funding = [0.0; 4];
    let w = [-1.0, 0.0, 0.0, 0.0];
    let r = run1(&close, &open, &funding, &w, &c);
    // t0 rebalance W=-1: target -100000/open[1]=100 → qty -1000, fills t1.
    // t2 close 120: unrealized = -1000*(120-100) = -20000 → equity = 80000.
    //   gross notional = |-1000*120| = 120000; gross/equity = 120000/80000 = 1.5 > cap 1.0.
    //   maintenance = 120000*0.05 = 6000 < 80000 → NOT a margin breach. step 7 fires.
    //   force-reduce target qty = -1000 * (cap*equity / gross_notional)
    //     = -1000 * (1.0*80000 / 120000) = -666.67. fills t3.
    // Assert: a hard-cap reduce trade fills at t3 reducing the short toward the bound.
    let reduce = r.trades.iter().find(|t| t.bar == 3);
    let reduce = reduce.expect("expected a hard-cap reduce trade at t3");
    // dq should be POSITIVE (buying back part of the short): -666.67 - (-1000) = +333.33.
    assert!(reduce.qty > 0.0, "reduce dq should buy back short, got {}", reduce.qty);
    assert!((reduce.qty - 333.333_333).abs() < 1e-2, "reduce dq {}", reduce.qty);
}

#[test]
fn soft_cap_drifts_within_tolerance() {
    // EXACT SAME short-drift scenario as hard_cap_force_reduce (gross drifts to
    // 1.5x against a 1.0 cap), but with a LARGE Soft tolerance=1.0 →
    // effective_cap = leverage_cap*(1+tol) = 2.0. The 1.5x drift is WITHIN the
    // tolerance band → step 7 must NOT force-reduce, so only the t1 open trade
    // fires. This is the discriminating contrast vs the Hard case (and vs
    // tolerance=0.0), which DO inject a reduce at t3.
    //
    // NON-VACUITY: the buggy code (`if let CapKind::Hard`) also yields 1 trade
    // here (step 7 is a no-op for Soft), so this assertion alone does not prove
    // the fix. It is paired with `soft_cap_beyond_tolerance_reduces` below,
    // which the buggy code FAILS — together they pin both branches.
    let mut c = cfg();
    c.cap_kind = CapKind::Soft { tolerance: 1.0 };
    c.leverage_cap = 1.0;
    c.max_net = 1.0;
    c.max_per_asset = 1.0;
    c.mm_rate = 0.05;
    c.rebalance_freq = 100;
    let close = [100.0, 100.0, 120.0, 120.0];
    let open = [100.0, 100.0, 100.0, 120.0];
    let funding = [0.0; 4];
    let w = [-1.0, 0.0, 0.0, 0.0];
    let r = run1(&close, &open, &funding, &w, &c);
    // gross/equity = 1.5 < effective_cap 2.0 → within tolerance → NO reduce.
    assert_eq!(r.trades.len(), 1, "soft cap within tolerance must not inject reduce trades, got {:?}",
        r.trades.iter().map(|t|(t.bar,t.qty)).collect::<Vec<_>>());
    assert_eq!(r.trades[0].bar, 1);
}

#[test]
fn soft_cap_beyond_tolerance_reduces() {
    // SAME short-drift scenario (gross drifts to 1.5x against a 1.0 cap), but
    // with Soft tolerance=0.0 → effective_cap = leverage_cap*(1+0) = 1.0, i.e.
    // a Soft cap with zero tolerance must behave IDENTICALLY to a Hard cap:
    // step 7 fires and force-reduces toward the bound. The buggy code
    // (`if let CapKind::Hard`) makes step 7 a no-op for ANY Soft variant, so it
    // yields only 1 trade and FAILS this test — that is the proof of the fix.
    let mut c = cfg();
    c.cap_kind = CapKind::Soft { tolerance: 0.0 };
    c.leverage_cap = 1.0;
    c.max_net = 1.0;
    c.max_per_asset = 1.0;
    c.mm_rate = 0.05;
    c.rebalance_freq = 100;
    let close = [100.0, 100.0, 120.0, 120.0];
    let open = [100.0, 100.0, 100.0, 120.0];
    let funding = [0.0; 4];
    let w = [-1.0, 0.0, 0.0, 0.0];
    let r = run1(&close, &open, &funding, &w, &c);
    // gross/equity = 1.5 > effective_cap 1.0 → beyond tolerance → reduce at t3.
    // Identical arithmetic to hard_cap_force_reduce: dq = +333.33 (buy back short).
    let reduce = r.trades.iter().find(|t| t.bar == 3)
        .expect("soft cap tolerance=0.0 must force-reduce (acts like hard) at t3");
    assert!(reduce.qty > 0.0, "reduce dq should buy back short, got {}", reduce.qty);
    assert!((reduce.qty - 333.333_333).abs() < 1e-2, "reduce dq {}", reduce.qty);
}

// ---------------------------------------------------------------------------
// Weight validation
// ---------------------------------------------------------------------------

#[test]
fn weight_violation_rejected() {
    let mut c = cfg();
    c.leverage_cap = 1.0;
    c.max_net = 1.0;
    c.max_per_asset = 1.0;
    // gross Σ|W| = 1.5 > leverage_cap 1.0 → reject.
    let close = vec![100.0, 100.0, 100.0, 100.0];
    let open = close.clone();
    let fund = vec![0.0; 4];
    let active = vec![true; 4];
    let sessions = vec![2u8; 4];
    let fx = vec![1.0; 4];
    let dt: Vec<i64> = (0..2).collect();
    let w = vec![1.0, 0.5, 1.0, 0.5]; // T=2,N=2 gross 1.5
    let err = run_panel_core(
        &["A".into(), "B".into()],
        2,
        2,
        &close,
        &open,
        &fund,
        &fx,
        &w,
        &active,
        &sessions,
        &dt,
        &c,
        None,
        None,
        BiasMode::Forbidden,
    )
    .unwrap_err();
    matches!(err, EngineError::WeightViolation { .. });
    if let EngineError::WeightViolation { reason, .. } = &err {
        assert!(reason.contains("leverage_cap"), "reason {}", reason);
    } else {
        panic!("expected WeightViolation, got {err:?}");
    }
}

#[test]
fn weight_on_inactive_cell_hard_fails() {
    let mut c = cfg();
    c.leverage_cap = 2.0;
    c.max_net = 2.0;
    c.max_per_asset = 2.0;
    // asset B inactive at t0 but carries nonzero W → hard-fail.
    let close = vec![100.0, f64::NAN, 100.0, 100.0]; // t0: B NaN
    let open = vec![100.0, 100.0, 100.0, 100.0];
    let fund = vec![0.0; 4];
    let active = vec![true, false, true, true];
    let sessions = vec![2u8; 4];
    let fx = vec![1.0; 4];
    let dt: Vec<i64> = (0..2).collect();
    let w = vec![1.0, 1.0, 1.0, 1.0];
    let err = run_panel_core(
        &["A".into(), "B".into()],
        2,
        2,
        &close,
        &open,
        &fund,
        &fx,
        &w,
        &active,
        &sessions,
        &dt,
        &c,
        None,
        None,
        BiasMode::Forbidden,
    )
    .unwrap_err();
    if let EngineError::WeightViolation { reason, t, asset, .. } = &err {
        assert!(reason.contains("inactive"), "reason {}", reason);
        assert_eq!((*t, *asset), (0, 1));
    } else {
        panic!("expected WeightViolation for inactive cell, got {err:?}");
    }
}

// ---------------------------------------------------------------------------
// C8 order priority matrix
// ---------------------------------------------------------------------------

#[test]
fn priority_ordering_is_total() {
    assert!(OrderPriority::Liquidation > OrderPriority::SessionFlat);
    assert!(OrderPriority::SessionFlat > OrderPriority::MarginReduce);
    assert!(OrderPriority::MarginReduce > OrderPriority::HardCapReduce);
    assert!(OrderPriority::HardCapReduce > OrderPriority::Rebalance);
}

#[test]
fn priority_liquidation_over_all() {
    let mut p: HashMap<usize, PendingOrder> = HashMap::new();
    for lower in [
        OrderPriority::SessionFlat,
        OrderPriority::MarginReduce,
        OrderPriority::HardCapReduce,
        OrderPriority::Rebalance,
    ] {
        p.clear();
        super::enqueue(&mut p, 0, PendingOrder { target_qty: 5.0, priority: lower });
        super::enqueue(
            &mut p,
            0,
            PendingOrder { target_qty: 0.0, priority: OrderPriority::Liquidation },
        );
        assert_eq!(p[&0].priority, OrderPriority::Liquidation);
        assert_eq!(p[&0].target_qty, 0.0);
    }
}

#[test]
fn priority_session_flat_over_lower() {
    for lower in [OrderPriority::MarginReduce, OrderPriority::HardCapReduce, OrderPriority::Rebalance] {
        let mut p: HashMap<usize, PendingOrder> = HashMap::new();
        super::enqueue(&mut p, 0, PendingOrder { target_qty: 5.0, priority: lower });
        super::enqueue(&mut p, 0, PendingOrder { target_qty: 0.0, priority: OrderPriority::SessionFlat });
        assert_eq!(p[&0].priority, OrderPriority::SessionFlat);
    }
}

#[test]
fn priority_margin_over_cap_and_rebalance() {
    for lower in [OrderPriority::HardCapReduce, OrderPriority::Rebalance] {
        let mut p: HashMap<usize, PendingOrder> = HashMap::new();
        super::enqueue(&mut p, 0, PendingOrder { target_qty: 5.0, priority: lower });
        super::enqueue(&mut p, 0, PendingOrder { target_qty: 2.0, priority: OrderPriority::MarginReduce });
        assert_eq!(p[&0].priority, OrderPriority::MarginReduce);
    }
}

#[test]
fn priority_hardcap_over_rebalance() {
    let mut p: HashMap<usize, PendingOrder> = HashMap::new();
    super::enqueue(&mut p, 0, PendingOrder { target_qty: 5.0, priority: OrderPriority::Rebalance });
    super::enqueue(&mut p, 0, PendingOrder { target_qty: 2.0, priority: OrderPriority::HardCapReduce });
    assert_eq!(p[&0].priority, OrderPriority::HardCapReduce);
}

#[test]
fn pending_order_netting_same_asset() {
    // A higher-priority pending is NOT overwritten by a later lower-priority one.
    let mut p: HashMap<usize, PendingOrder> = HashMap::new();
    super::enqueue(&mut p, 0, PendingOrder { target_qty: 0.0, priority: OrderPriority::Liquidation });
    super::enqueue(&mut p, 0, PendingOrder { target_qty: 9.0, priority: OrderPriority::Rebalance });
    // single net pending per asset, liquidation wins.
    assert_eq!(p.len(), 1);
    assert_eq!(p[&0].priority, OrderPriority::Liquidation);
    assert_eq!(p[&0].target_qty, 0.0);
}

// ---------------------------------------------------------------------------
// C2 mixed-currency fx
// ---------------------------------------------------------------------------

#[test]
fn mixed_currency_partial_close_and_funding() {
    // Exercises apply_fill's REDUCE branch AND funding under fx != 1, with
    // realized PnL converted at PIT fx. All values hand-derived from spec §5.
    let mut c = cfg();
    c.numeraire_is_unit_fx = false;
    c.fee_rate = 0.0;
    c.slippage_bps = 0.0;
    c.leverage_cap = 2.0;
    c.max_net = 2.0;
    c.max_per_asset = 2.0;
    c.rebalance_freq = 1;
    let t = 4;
    let n = 1;
    let close = vec![100.0, 100.0, 100.0, 100.0]; // native flat
    let open = vec![100.0, 100.0, 100.0, 100.0];
    let fund = vec![0.0, 0.0, 0.01, 0.0];
    let fx = vec![2.0, 2.0, 3.0, 3.0]; // fx jumps 2→3 at t2
    let active = vec![true; 4];
    let sessions = vec![2u8; 4];
    let dt: Vec<i64> = (0..t as i64).collect();
    let w = vec![0.5, 0.5, 0.25, 0.25];
    let r = run_panel_core(
        &["B".into()],
        t,
        n,
        &close,
        &open,
        &fund,
        &fx,
        &w,
        &active,
        &sessions,
        &dt,
        &c,
        None,
        None,
        BiasMode::Forbidden,
    )
    .unwrap();
    // t0 W=0.5 → target 50000 / px_num(open[1]=100*fx2=200) = qty 250, basis 50000. fills t1.
    // t1: funding 0; unreal 0 → equity 100000. rebalance keeps qty 250 (no trade).
    assert!((r.equity[1] - 100_000.0).abs() < 1e-6, "t1 {}", r.equity[1]);
    // t2: funding on carried book = 250*close[1]100*fx(t1)2 = 50000 notional → -500.
    assert!((r.funding_pnl[2] - (-500.0)).abs() < 1e-6, "fund t2 {}", r.funding_pnl[2]);
    //   MTM fx=3: val 250*100*3=75000, basis 50000 → unreal +25000.
    //   equity = 100000 + 25000 - 500 = 124500.
    assert!((r.equity[2] - 124_500.0).abs() < 1e-6, "t2 {}", r.equity[2]);
    // t2 rebalance W=0.25: target 124500*0.25=31125 / px_num(open[3]=100*fx3=300) = 103.75.
    //   partial close diff -146.25 fills t3. realized = 146.25*(300-200)=14625.
    //   remaining basis 103.75*200=20750.
    // t3 MTM fx=3: val 103.75*100*3=31125, basis 20750 → unreal +10375.
    //   equity = 100000 + 14625(realized) + 10375(unreal) - 500(funding) = 124500. continuity holds.
    assert!((r.realized_pnl[3] - 14625.0).abs() < 1e-6, "realized t3 {}", r.realized_pnl[3]);
    assert!((r.equity[3] - 124_500.0).abs() < 1e-6, "t3 {}", r.equity[3]);
}

#[test]
fn equity_invariant_mixed_currency() {
    // 2 assets. A is numeraire (fx=1). B is non-numeraire with CHANGING fx.
    // Verify equity in numeraire holds via PIT fx through open → MTM.
    let mut c = cfg();
    c.numeraire_is_unit_fx = false;
    c.fee_rate = 0.0;
    c.leverage_cap = 2.0;
    c.max_net = 2.0;
    c.max_per_asset = 2.0;
    c.rebalance_freq = 100; // open at t0 only
    let t = 3;
    let n = 2;
    // close (native): A stays 100; B stays 50 (native price flat → all PnL is fx).
    let close = vec![
        100.0, 50.0, // t0
        100.0, 50.0, // t1
        100.0, 50.0, // t2
    ];
    let open = close.clone();
    let fund = vec![0.0; 6];
    // fx: A=1 always. B fx rises 2.0 → 2.0 → 3.0 (changes at t2).
    let fx = vec![
        1.0, 2.0, // t0
        1.0, 2.0, // t1
        1.0, 3.0, // t2
    ];
    let active = vec![true; 6];
    let sessions = vec![2u8; 6];
    let dt: Vec<i64> = (0..t as i64).collect();
    // W: A=0.5, B=0.5 at t0.
    let w = vec![0.5, 0.5, 0.0, 0.0, 0.0, 0.0];
    let r = run_panel_core(
        &["A".into(), "B".into()],
        t,
        n,
        &close,
        &open,
        &fund,
        &fx,
        &w,
        &active,
        &sessions,
        &dt,
        &c,
        None,
        None,
        BiasMode::Forbidden,
    )
    .unwrap();
    // t0 rebalance, equity=100000.
    //   A target notional = 100000*0.5 = 50000; px_num = 100*1 = 100 → qtyA=500.
    //   B target notional = 100000*0.5 = 50000; px_num(t0)=50*2=100 → qtyB=500.
    //   fills at open[1]. open[1] native A=100,fx=1 → px_num 100 → qtyA 500.
    //                      open[1] native B=50, fx=2 → px_num 100 → qtyB 500.
    //   cost_basis_A = 500*100 = 50000; cost_basis_B = 500*100 = 50000.
    // t1 MTM: A close 100 fx1 → val 50000; B close 50 fx2 → val 50000.
    //   unrealized = (50000-50000)+(50000-50000)=0 → equity 100000.
    assert!((r.equity[1] - 100_000.0).abs() < 1e-6, "t1 {}", r.equity[1]);
    // t2 MTM: B fx jumps to 3 → B val = 500*50*3 = 75000; basis 50000 → +25000.
    //   A unchanged. equity = 100000 + 25000 = 125000.
    assert!((r.equity[2] - 125_000.0).abs() < 1e-6, "t2 {}", r.equity[2]);

    // numeraire_is_unit_fx=true with fx≡1 gives identical to skipping conversion:
    // run same shapes with fx all 1 and unit flag, B native price moving to mimic.
    // (sanity) flat native + unit fx → equity stays 100000.
    let mut cu = c.clone();
    cu.numeraire_is_unit_fx = true;
    let fx1 = vec![1.0; 6];
    let ru = run_panel_core(
        &["A".into(), "B".into()],
        t,
        n,
        &close,
        &open,
        &fund,
        &fx1,
        &w,
        &active,
        &sessions,
        &dt,
        &cu,
        None,
        None,
        BiasMode::Forbidden,
    )
    .unwrap();
    assert!((ru.equity[2] - 100_000.0).abs() < 1e-6, "unit fx flat {}", ru.equity[2]);
}

// ---------------------------------------------------------------------------
// Bias ingestion gate (C1, P1.6)
// ---------------------------------------------------------------------------

fn bias_run(
    bias: Option<&[f64]>,
    available_at: Option<&[i64]>,
    mode: BiasMode,
    decision_time: &[i64],
) -> Result<PortfolioResult, EngineError> {
    let t = 2;
    let n = 1;
    let close = vec![100.0, 100.0];
    let open = vec![100.0, 100.0];
    let fund = vec![0.0; 2];
    let fx = vec![1.0; 2];
    let active = vec![true; 2];
    let sessions = vec![2u8; 2];
    let w = vec![0.0, 0.0];
    let mut c = cfg();
    c.leverage_cap = 5.0;
    c.max_net = 5.0;
    c.max_per_asset = 5.0;
    run_panel_core(
        &["A".into()],
        t,
        n,
        &close,
        &open,
        &fund,
        &fx,
        &w,
        &active,
        &sessions,
        decision_time,
        &c,
        bias,
        available_at,
        mode,
    )
}

#[test]
fn bias_forbidden_by_default() {
    let dt = [10i64, 20];
    let b = [0.1, 0.1];
    let err = bias_run(Some(&b), Some(&[5, 15]), BiasMode::Forbidden, &dt).unwrap_err();
    assert!(matches!(err, EngineError::BiasRejected(_)), "got {err:?}");
}

#[test]
fn bias_future_available_at_rejected() {
    let dt = [10i64, 20];
    let b = [0.1, 0.1];
    // available_at[1]=25 > decision_time[1]=20 → future leak → reject.
    let err = bias_run(Some(&b), Some(&[5, 25]), BiasMode::Backtest, &dt).unwrap_err();
    if let EngineError::BiasRejected(msg) = &err {
        assert!(msg.contains("future"), "msg {}", msg);
    } else {
        panic!("expected BiasRejected, got {err:?}");
    }
}

#[test]
fn bias_missing_available_at_rejected() {
    let dt = [10i64, 20];
    let b = [0.1, 0.1];
    let err = bias_run(Some(&b), None, BiasMode::Backtest, &dt).unwrap_err();
    assert!(matches!(err, EngineError::BiasRejected(_)), "got {err:?}");
}

#[test]
fn bias_scalar_rejected() {
    // scalar/window-constant available_at (len 1, not T) → reject.
    let dt = [10i64, 20];
    let b = [0.1, 0.1];
    let err = bias_run(Some(&b), Some(&[5]), BiasMode::Backtest, &dt).unwrap_err();
    assert!(matches!(err, EngineError::BiasRejected(_)), "got {err:?}");
}

#[test]
fn bias_nan_rejected() {
    // A non-finite bias value (NaN) must be rejected at ingestion. Without an
    // explicit is_finite() guard a NaN bias slips through (NaN comparisons are
    // all false), then folds into W and silently corrupts the run.
    let dt = [10i64, 20];
    let b = [0.1, f64::NAN];
    let err = bias_run(Some(&b), Some(&[5, 15]), BiasMode::Backtest, &dt).unwrap_err();
    if let EngineError::BiasRejected(msg) = &err {
        assert!(msg.contains("finite"), "msg {}", msg);
    } else {
        panic!("expected BiasRejected for NaN bias, got {err:?}");
    }
}

#[test]
fn bias_window_constant_rejected() {
    // A length-T available_at whose entries are all identical (and all <=
    // decision_time, so the future check would pass) is "today's view stamped
    // backward across the fold" — the §13 leak. The seam must reject it rather
    // than apply the tilt; a direct run_panel caller bypasses bias.py's check.
    let dt = [10i64, 20];
    let b = [0.5, 0.5];
    // av = [5, 5]: window-constant, both <= decision_time → must still REJECT.
    let err = bias_run(Some(&b), Some(&[5, 5]), BiasMode::Backtest, &dt).unwrap_err();
    if let EngineError::BiasRejected(msg) = &err {
        assert!(msg.contains("window-constant"), "msg {}", msg);
    } else {
        panic!("expected BiasRejected for window-constant available_at, got {err:?}");
    }
}

#[test]
fn bias_valid_pit_accepted() {
    let dt = [10i64, 20];
    let b = [0.5, 0.5]; // adds a 0.5 long tilt
    // available_at all <= decision_time → accepted, runs, tilt applied.
    let r = bias_run(Some(&b), Some(&[5, 15]), BiasMode::Backtest, &dt).unwrap();
    // base W=0 + bias 0.5 → a long position is opened (trade exists).
    assert!(!r.trades.is_empty(), "valid bias should apply a tilt → trades");
}

// ---------------------------------------------------------------------------
// Alignment (C5)
// ---------------------------------------------------------------------------

#[test]
fn run_panel_rejects_n_mismatch() {
    // assets len 2 but N declared 3.
    let close = vec![1.0; 6]; // T=2,N=3
    let err = run_panel_core(
        &["A".into(), "B".into()], // len 2
        2,
        3,
        &close,
        &close,
        &vec![0.0; 6],
        &vec![1.0; 6],
        &vec![0.0; 6],
        &vec![true; 6],
        &vec![2u8; 6],
        &[0, 1],
        &cfg(),
        None,
        None,
        BiasMode::Forbidden,
    )
    .unwrap_err();
    assert!(matches!(err, EngineError::Shape(_)), "got {err:?}");
}

#[test]
fn run_panel_rejects_col_count_drift() {
    // weights matrix has wrong length (T*N mismatch).
    let close = vec![1.0; 4]; // T=2,N=2
    let err = run_panel_core(
        &["A".into(), "B".into()],
        2,
        2,
        &close,
        &close,
        &vec![0.0; 4],
        &vec![1.0; 4],
        &vec![0.0; 6], // WRONG: len 6 not 4
        &vec![true; 4],
        &vec![2u8; 4],
        &[0, 1],
        &cfg(),
        None,
        None,
        BiasMode::Forbidden,
    )
    .unwrap_err();
    assert!(matches!(err, EngineError::Shape(_)), "got {err:?}");
}

// ---------------------------------------------------------------------------
// Session force-flat (intraday)
// ---------------------------------------------------------------------------

#[test]
fn session_force_flat_intraday() {
    let mut c = cfg();
    c.session_aware = true;
    c.leverage_cap = 1.0;
    c.max_net = 1.0;
    c.max_per_asset = 1.0;
    c.rebalance_freq = 1;
    // sessions: open at t0,t1; close(4) at t2 → force flat enqueued, fills t3.
    let close = vec![100.0, 100.0, 100.0, 100.0];
    let open = vec![100.0, 100.0, 100.0, 100.0];
    let fund = vec![0.0; 4];
    let fx = vec![1.0; 4];
    let active = vec![true; 4];
    let sessions = vec![2u8, 2, 4, 2];
    let dt: Vec<i64> = (0..4).collect();
    let w = vec![1.0, 1.0, 1.0, 1.0];
    let r = run_panel_core(
        &["A".into()],
        4,
        1,
        &close,
        &open,
        &fund,
        &fx,
        &w,
        &active,
        &sessions,
        &dt,
        &c,
        None,
        None,
        BiasMode::Forbidden,
    )
    .unwrap();
    // a flatten trade (qty back to 0) fills at t3 from the t2 session-close enqueue.
    let flat = r.trades.iter().any(|tr| tr.bar == 3 && (tr.qty + 1000.0).abs() < 1e-6);
    assert!(flat, "expected session-flat trade at t3, trades={:?}", r.trades.iter().map(|t|(t.bar,t.qty)).collect::<Vec<_>>());
}

// ---------------------------------------------------------------------------
// C9 execution: closed (inactive) rows on the daily path
// ---------------------------------------------------------------------------

#[test]
fn inactive_closed_row_holds_position_no_forced_roundtrip() {
    // Codex push-gate: full_calendar daily panel — a session==0 closed row is
    // INACTIVE (loader) and W there is 0. The engine must NOT originate orders
    // on the closed bar (no flatten of the carried book, no re-open after):
    // exactly ONE trade (the initial open) over the whole run.
    let mut c = cfg();
    c.leverage_cap = 1.0;
    c.max_net = 1.0;
    c.max_per_asset = 1.0;
    let close = [100.0, 100.0, 100.0, 100.0, 100.0];
    let open = [100.0, 100.0, 100.0, 100.0, 100.0];
    let fund = [0.0; 5];
    let fx = [1.0; 5];
    // t2 = closed day: inactive, session 0, W forced 0. Active resumes t3 W=1.
    let w = [1.0, 1.0, 0.0, 1.0, 1.0];
    let active = [true, true, false, true, true];
    let sessions = [2u8, 2, 0, 2, 2];
    let dt: Vec<i64> = (0..5).collect();
    let r = run_panel_core(
        &["A".into()],
        5,
        1,
        &close,
        &open,
        &fund,
        &fx,
        &w,
        &active,
        &sessions,
        &dt,
        &c,
        None,
        None,
        BiasMode::Forbidden,
    )
    .unwrap();
    assert_eq!(
        r.trades.len(),
        1,
        "closed bar must not force a flatten/re-open round-trip, trades={:?}",
        r.trades.iter().map(|t| (t.bar, t.qty)).collect::<Vec<_>>()
    );
    assert_eq!(r.trades[0].bar, 1, "only the initial open fill at t1");
}
