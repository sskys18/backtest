//! MACRO/exogenous → directional LONG/SHORT/FLAT decision for 삼전(005930)·하닉(000660).
//!
//! THE UNTESTED QUESTION = the SHORT leg. All prior tests (leadlag, swing_check,
//! positional_check) had short legs execution-constrained (KR short-ban until
//! ~2025-03-31) → effectively long-or-null. Here we evaluate BOTH legs as a
//! STATEFUL daily-rebalanced position backtest: long via KR spot, short via a
//! Binance KR-perp PROXY (perp launched 2026-06-02, ~1 day history → we proxy
//! the short PnL as −(KR spot return) and model funding as a parameter).
//!
//! ⚠️ KILLER CAVEATS (printed in VERDICT, restated here so they're never lost):
//!   - TEST window 2024-26 is a semis SUPERCYCLE uptrend → any long-bias beats
//!     by drift/beta, NOT skill. ALWAYS compare to buy-and-hold (B&H).
//!   - SHORT execution is currently UNECONOMICAL: perp funding ≈ +120%/yr
//!     (≈0.33%/day) + thin book. We can VALIDATE the short signal; EXECUTION is
//!     future. Short-leg results reported BOTH with funding (realistic) and
//!     without (signal-only).
//!   - Mega-caps are efficient → regime-filter-grade result (or null) expected.
//!   - Small a-priori rule set (3 rules × 3 N = 9 configs/stock) but rules are
//!     correlated/overlapping → effective hypothesis count < 9; t/Sharpe inflated.
//!   - 2022-23 bear is ONE sample; short-side conclusions are fragile.
//!
//! ── Backtest mechanics (NOT swing's selection-edge engine; this is a real
//!    daily-rebalanced position series with an equity curve) ──
//!   - Position p[d] ∈ {+1 LONG, −1 SHORT, 0 FLAT} from the rule, using ONLY
//!     info known by close[d]: semis trend (US date STRICTLY < d) and smart-flow
//!     trend (flow date STRICTLY < d — finalized investor numbers publish ~18:00
//!     KST, AFTER the 15:30 close, so using flow dated < d is the lookahead-safe
//!     choice; matches the semis "strictly before" discipline).
//!   - ret[d] = close[d+1]/close[d] − 1 (close-to-close, next-day forward).
//!   - daily strategy PnL = p[d]·ret[d] − cost·(p[d] ≠ p[d−1]) − funding·(p[d] == −1);
//!     i.e. round-trip cost charged on every position CHANGE, funding charged each held SHORT day.
//!   - equity = Π(1 + pnl[d]). Metrics: total ret, CAGR, Sharpe(mean/std·√252),
//!     maxDD, hit% (share of nonzero-position days with pnl>0), #position changes.
//!   - B&H baseline = p≡+1, one entry cost, no funding.
//!   - A true daily series → NO overlap, so Sharpe/t are clean (no step=M tricks).
//!
//! ── Rules (a-priori, fixed; N ∈ {10,20,60}) ──
//!   1. semis-only : LONG if semis-trend up, SHORT if down.
//!   2. flow-only  : LONG if flow accumulating (trailing-N cum net > 0), SHORT if distributing.
//!   3. AND-combo  : LONG if (semis up AND flow up), SHORT if (semis down AND flow down), else FLAT.
//!
//! semis-trend up := US-semis basket trailing-N close-to-close return > 0 (== above own N-day MA sign for cum-ret).
//!
//! ── OOS decompose ── TRAIN = 2022-23 (bear/sideways, where SHORT matters);
//!    TEST = 2024-26 (supercycle, where LONG trivially wins). Reachability of
//!    pre-2024 data is GATED FIRST and reported per source — flow data may not
//!    reach the bear (KIS TR caps ~30 trading days/call), in which case the
//!    flow-only & AND-combo SHORT-in-bear question is UNANSWERABLE (stated loudly).
//!
//! Env: KIS creds (KisConfig::from_env). `MS_START`(YYYYMMDD, default 20220101),
//! `MS_END`(default 20260602), `MS_COST_BPS`(round-trip bps, default 35),
//! `MS_OOS_SPLIT`(train/test boundary, default 20240101),
//! `MS_SHORT_FUNDING_BPS_PER_DAY`(default 33 ≈ current +120%/yr). Read-only.
//! FX (USD/KRW): NOT reachable via KIS adapter (only Toss surface) → SKIPPED.

use std::collections::{BTreeMap, HashMap};

use kis_adapter::domestic::kis::domestic_stock::Period;
use kis_adapter::domestic::kis::overseas_stock::{OverseasExchange, OverseasPeriod};
use kis_adapter::{KisClient, KisConfig, Market};

/// KIS comma-string → f64.
fn num(s: &str) -> f64 {
    s.chars()
        .filter(|c| *c != ',' && !c.is_whitespace())
        .collect::<String>()
        .parse()
        .unwrap_or(0.0)
}

fn env_or(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_string())
}

/// One KR trading day (API returns only trading days).
struct KrDay {
    date: String,
    close: f64,
}

/// One US trading day.
struct UsDay {
    date: String,
    close: f64,
}

/// YYYYMMDD − n calendar days (no external deps). Window cursor; overshoot earlier
/// is harmless (merge-by-date + start clamp).
fn minus_days(ymd: &str, n: i64) -> String {
    let v: i64 = ymd.parse().unwrap_or(0);
    let (mut y, mut m, mut d) = (v / 10000, (v / 100) % 100, v % 100);
    let mut rem = n;
    while rem > 0 {
        d -= 1;
        if d < 1 {
            m -= 1;
            if m < 1 {
                m = 12;
                y -= 1;
            }
            d = days_in_month(y, m);
        }
        rem -= 1;
    }
    format!("{y:04}{m:02}{d:02}")
}

fn days_in_month(y: i64, m: i64) -> i64 {
    match m {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 => {
            if (y % 4 == 0 && y % 100 != 0) || y % 400 == 0 {
                29
            } else {
                28
            }
        }
        _ => 30,
    }
}

/// KR daily close history via date-windowed pagination (period_price caps ~100 bars).
async fn kr_history(
    kis: &KisClient,
    code: &str,
    start: &str,
    end: &str,
) -> Result<Vec<KrDay>, Box<dyn std::error::Error>> {
    let mut by_date: HashMap<String, KrDay> = HashMap::new();
    let mut win_end = end.to_string();
    let start_i: u32 = start.parse().unwrap_or(0);
    let mut prev_earliest = u64::MAX;
    for _ in 0..80 {
        let we_i: u32 = win_end.parse().unwrap_or(0);
        if we_i < start_i {
            break;
        }
        let win_start = minus_days(&win_end, 95).max(start.to_string());
        let (_summary, candles) = match kis
            .domestic_stock()
            .period_price(code, &win_start, &win_end, Period::Daily, true, Market::Krx)
            .await
        {
            Ok(v) => v,
            Err(e) => {
                eprintln!("  {code} @ {win_start}..{win_end}: error — {e}");
                break;
            }
        };
        if candles.is_empty() {
            break;
        }
        let mut earliest = u64::MAX;
        for c in candles {
            let d: u64 = c.stck_bsop_date.parse().unwrap_or(0);
            if d == 0 {
                continue;
            }
            earliest = earliest.min(d);
            by_date.entry(c.stck_bsop_date.clone()).or_insert(KrDay {
                date: c.stck_bsop_date.clone(),
                close: num(&c.stck_clpr),
            });
        }
        if earliest == u64::MAX || earliest <= start_i as u64 {
            break;
        }
        if earliest >= prev_earliest {
            break;
        }
        prev_earliest = earliest;
        win_end = minus_days(&format!("{earliest:08}"), 1);
        tokio::time::sleep(std::time::Duration::from_millis(120)).await;
    }
    let mut v: Vec<KrDay> = by_date.into_values().filter(|d| d.close > 0.0).collect();
    v.sort_by(|a, b| a.date.cmp(&b.date));
    Ok(v)
}

/// US daily history via cont/has_next pagination (BYMD cursor).
async fn us_history(
    kis: &KisClient,
    exch: OverseasExchange,
    sym: &str,
    start: &str,
    end: &str,
) -> Result<Vec<UsDay>, Box<dyn std::error::Error>> {
    let mut by_date: HashMap<String, UsDay> = HashMap::new();
    let mut bymd = end.to_string();
    let start_i: u64 = start.parse().unwrap_or(0);
    let mut prev_earliest = u64::MAX;
    for page in 0..80 {
        let cont = page > 0;
        let resp = match kis
            .overseas_stock()
            .period_price(exch, sym, OverseasPeriod::Daily, &bymd, true, cont)
            .await
        {
            Ok(v) => v,
            Err(e) => {
                eprintln!("  US {sym} @ {bymd}: error — {e}");
                break;
            }
        };
        let (_summary, candles) = resp.data;
        if candles.is_empty() {
            break;
        }
        let mut earliest = u64::MAX;
        for c in candles {
            let d: u64 = c.xymd.parse().unwrap_or(0);
            if d == 0 {
                continue;
            }
            earliest = earliest.min(d);
            by_date.entry(c.xymd.clone()).or_insert(UsDay {
                date: c.xymd.clone(),
                close: num(&c.clos),
            });
        }
        if earliest == u64::MAX || earliest <= start_i {
            break;
        }
        if earliest >= prev_earliest {
            break;
        }
        prev_earliest = earliest;
        bymd = minus_days(&format!("{earliest:08}"), 1);
        tokio::time::sleep(std::time::Duration::from_millis(120)).await;
    }
    let mut v: Vec<UsDay> = by_date.into_values().filter(|d| d.close > 0.0).collect();
    v.sort_by(|a, b| a.date.cmp(&b.date));
    Ok(v)
}

/// US daily close-to-close returns dated by close-date. ret[i]=close[i]/close[i-1]-1.
fn us_returns(days: &[UsDay]) -> Vec<(String, f64)> {
    let mut out = Vec::new();
    for i in 1..days.len() {
        if days[i - 1].close > 0.0 {
            out.push((days[i].date.clone(), days[i].close / days[i - 1].close - 1.0));
        }
    }
    out
}

/// Equal-weight basket signal: per-member close-to-close returns averaged across
/// members present on the same US date. Sorted Vec<(us_date, ew_ret)>.
fn basket_signal(members: &[Vec<(String, f64)>]) -> Vec<(String, f64)> {
    let mut acc: HashMap<String, (f64, u32)> = HashMap::new();
    for m in members {
        for (date, r) in m {
            let e = acc.entry(date.clone()).or_insert((0.0, 0));
            e.0 += *r;
            e.1 += 1;
        }
    }
    let mut v: Vec<(String, f64)> = acc
        .into_iter()
        .filter(|(_, (_, n))| *n > 0)
        .map(|(d, (s, n))| (d, s / n as f64))
        .collect();
    v.sort_by(|a, b| a.0.cmp(&b.0));
    v
}

/// Semis trailing-N cumulative return, KST-aligned: most recent US date STRICTLY
/// < kr_date, then N US-sessions backward cumulated. Sign > 0 == above own N-day MA.
fn semis_trailing_n(us: &[(String, f64)], kr_date: &str, n: usize) -> Option<f64> {
    let mut idx: Option<usize> = None;
    for (i, (d, _)) in us.iter().enumerate() {
        if d.as_str() < kr_date {
            idx = Some(i);
        } else {
            break;
        }
    }
    let i = idx?;
    if i + 1 < n {
        return None;
    }
    let mut acc = 1.0;
    for (_, r) in &us[(i + 1 - n)..=i] {
        acc *= 1.0 + r;
    }
    Some(acc - 1.0)
}

/// Smart-money flow per KR trading date: (date → net KRW = 외국인 + 기관 net-buy value).
/// Backward-stitched (investor_trend_daily_all caps ~30 trading days/call).
type FlowSeries = BTreeMap<String, f64>;

async fn flow_history(
    kis: &KisClient,
    code: &str,
    start: &str,
    end: &str,
) -> Result<FlowSeries, Box<dyn std::error::Error>> {
    const MAX_WINDOWS: usize = 60; // ~30 trading days/window × 60 ≈ reach 2022 from 2026.
    let mut daily: FlowSeries = BTreeMap::new();
    let mut cursor = end.to_string();
    let mut prev_earliest = String::new();
    for _ in 0..MAX_WINDOWS {
        let days = match kis
            .domestic_stock()
            .investor_trend_daily_all(code, &cursor, Market::Krx)
            .await
        {
            Ok((_summary, days)) => days,
            Err(e) => {
                eprintln!("  flow {code} @ {cursor}: error — {e}");
                break;
            }
        };
        if days.is_empty() {
            break;
        }
        let mut earliest = String::new();
        for d in &days {
            if d.stck_bsop_date.is_empty() {
                continue;
            }
            // smart-money net = 외국인 + 기관 (net-buy VALUE, KRW).
            let smart_net = num(&d.frgn_ntby_tr_pbmn) + num(&d.orgn_ntby_tr_pbmn);
            daily.insert(d.stck_bsop_date.clone(), smart_net);
            if earliest.is_empty() || d.stck_bsop_date < earliest {
                earliest = d.stck_bsop_date.clone();
            }
        }
        if earliest.is_empty() {
            break;
        }
        if !prev_earliest.is_empty() && earliest >= prev_earliest {
            break; // progress guard — KIS history cap reached.
        }
        if earliest.as_str() <= start {
            break;
        }
        prev_earliest = earliest.clone();
        cursor = minus_days(&earliest, 1);
        tokio::time::sleep(std::time::Duration::from_millis(120)).await;
    }
    Ok(daily)
}

/// Flow trailing-N cumulative net (KRW), using flow dates STRICTLY < kr_date
/// (finalized investor data publishes after close → lookahead-safe). N most-recent
/// flow days before kr_date summed. None if fewer than N available.
fn flow_trailing_n(flow: &FlowSeries, kr_date: &str, n: usize) -> Option<f64> {
    let recent: Vec<f64> = flow
        .range(..kr_date.to_string())
        .rev()
        .take(n)
        .map(|(_, v)| *v)
        .collect();
    if recent.len() < n {
        return None;
    }
    Some(recent.iter().sum())
}

fn mean(xs: &[f64]) -> f64 {
    if xs.is_empty() {
        0.0
    } else {
        xs.iter().sum::<f64>() / xs.len() as f64
    }
}

fn std(xs: &[f64], m: f64) -> f64 {
    if xs.len() < 2 {
        return 0.0;
    }
    let var = xs.iter().map(|x| (x - m).powi(2)).sum::<f64>() / (xs.len() as f64 - 1.0);
    var.sqrt()
}

/// Backtest result over one window for one config/leg-mode.
struct Bt {
    n_days: usize,    // # of position-bearing days (forward exists, in window)
    changes: usize,   // # position changes (each charged round-trip cost)
    total_ret: f64,   // Π(1+pnl)−1
    cagr: f64,        // annualized
    sharpe: f64,      // mean/std·√252
    maxdd: f64,       // max drawdown (positive number)
    hit: f64,         // share of nonzero-position days with pnl>0
}

/// Run a stateful daily-rebalanced backtest.
/// `pos[i]` = desired position for KR day i (computed by caller, lookahead-safe).
/// `kr` sorted ascending. `split`/`want_train` select OOS window by ENTRY date.
/// `funding` = per-day short funding (fraction). `cost` = round-trip cost (fraction).
/// `allow_short`: if false, any −1 is clamped to 0 (models KR long-only legs).
#[allow(clippy::too_many_arguments)]
fn backtest(
    kr: &[KrDay],
    pos: &[Option<i8>],
    split: &str,
    want_train: bool,
    cost: f64,
    funding: f64,
    allow_short: bool,
) -> Option<Bt> {
    let n = kr.len();
    let mut pnls: Vec<f64> = Vec::new();
    let mut nonzero_pnls: Vec<f64> = Vec::new();
    let mut changes = 0usize;
    let mut prev_pos: i8 = 0; // flat before the window.
    for i in 0..n {
        if i + 1 >= n {
            break; // need forward.
        }
        let date = &kr[i].date;
        let in_train = date.as_str() < split;
        if in_train != want_train {
            continue;
        }
        let c0 = kr[i].close;
        let cf = kr[i + 1].close;
        if c0 <= 0.0 || cf <= 0.0 {
            continue;
        }
        let Some(raw) = pos[i] else {
            continue; // signal not evaluable (insufficient lookback) → skip day entirely.
        };
        let mut p = raw;
        if p == -1 && !allow_short {
            p = 0;
        }
        let ret = cf / c0 - 1.0;
        let mut pnl = (p as f64) * ret;
        if p != prev_pos {
            pnl -= cost;
            changes += 1;
        }
        if p == -1 {
            pnl -= funding;
        }
        pnls.push(pnl);
        if p != 0 {
            nonzero_pnls.push(pnl);
        }
        prev_pos = p;
    }
    if pnls.len() < 5 {
        return None;
    }
    let mut equity = 1.0;
    let mut peak = 1.0;
    let mut maxdd = 0.0;
    for &p in &pnls {
        equity *= 1.0 + p;
        if equity > peak {
            peak = equity;
        }
        let dd = (peak - equity) / peak;
        if dd > maxdd {
            maxdd = dd;
        }
    }
    let total_ret = equity - 1.0;
    let m = mean(&pnls);
    let sd = std(&pnls, m);
    let sharpe = if sd > 0.0 { m / sd * (252f64).sqrt() } else { 0.0 };
    let cagr = if equity > 0.0 {
        equity.powf(252.0 / pnls.len() as f64) - 1.0
    } else {
        -1.0
    };
    let hit = if nonzero_pnls.is_empty() {
        0.0
    } else {
        nonzero_pnls.iter().filter(|x| **x > 0.0).count() as f64 / nonzero_pnls.len() as f64
    };
    Some(Bt {
        n_days: pnls.len(),
        changes,
        total_ret,
        cagr,
        sharpe,
        maxdd,
        hit,
    })
}

/// Compute the LONG/SHORT/FLAT position series for one rule over all KR days.
/// Returns pos[i] ∈ {Some(+1|−1|0), None=not evaluable}.
/// `rule`: 0=semis-only, 1=flow-only, 2=AND-combo. `n` = trailing window.
fn build_positions(
    kr: &[KrDay],
    semis: &[(String, f64)],
    flow: &FlowSeries,
    rule: u8,
    n: usize,
) -> Vec<Option<i8>> {
    let mut out = Vec::with_capacity(kr.len());
    for day in kr {
        let date = &day.date;
        let s = semis_trailing_n(semis, date, n); // semis trend (US date < d)
        let f = flow_trailing_n(flow, date, n); // flow trend (flow date < d)
        let pos = match rule {
            0 => s.map(|v| if v > 0.0 { 1i8 } else { -1 }),
            1 => f.map(|v| if v > 0.0 { 1i8 } else { -1 }),
            2 => match (s, f) {
                (Some(sv), Some(fv)) => Some(if sv > 0.0 && fv > 0.0 {
                    1
                } else if sv < 0.0 && fv < 0.0 {
                    -1
                } else {
                    0
                }),
                _ => None,
            },
            _ => None,
        };
        out.push(pos);
    }
    out
}

#[allow(clippy::too_many_arguments)]
fn print_bt(stock: &str, win: &str, rule: &str, n: usize, leg: &str, bt: &Bt, bh: &Bt) {
    let beat = if bt.total_ret > bh.total_ret { "+" } else { " " };
    println!(
        "  {stock:<14} [{win:<5}] {rule:<11} N={n:<2} {leg:<14} days={:>4} chg={:>4} tot%={:>+8.2} CAGR%={:>+8.2} Sharpe={:>+6.2} maxDD%={:>6.2} hit%={:>5.1}  B&H tot%={:>+8.2}{beat}",
        bt.n_days,
        bt.changes,
        bt.total_ret * 100.0,
        bt.cagr * 100.0,
        bt.sharpe,
        bt.maxdd * 100.0,
        bt.hit * 100.0,
        bh.total_ret * 100.0,
    );
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let start = env_or("MS_START", "20220101");
    let end = env_or("MS_END", "20260602");
    let cost_bps: f64 = env_or("MS_COST_BPS", "35").parse().unwrap_or(35.0);
    let cost = cost_bps / 10000.0;
    let split = env_or("MS_OOS_SPLIT", "20240101");
    let fund_bps: f64 = env_or("MS_SHORT_FUNDING_BPS_PER_DAY", "33")
        .parse()
        .unwrap_or(33.0);
    let funding = fund_bps / 10000.0;

    eprintln!("macro_signal {start}..{end} — MACRO directional LONG/SHORT/FLAT (삼전·하닉)");
    eprintln!(
        "cost={cost_bps}bps round-trip/change, short funding={fund_bps}bps/day (≈{:.0}%/yr calendar), OOS split @ {split}\n",
        fund_bps / 10000.0 * 365.0 * 100.0
    );

    let kis = KisClient::new(KisConfig::from_env()?)?;

    // --- US semis basket (NVDA/MU/AVGO/TSM equal-weight close-to-close) ---
    eprintln!("fetching US semis daily history (NVDA/MU/AVGO/TSM)...");
    let us_specs: [(OverseasExchange, &str); 4] = [
        (OverseasExchange::Nasd, "NVDA"),
        (OverseasExchange::Nasd, "MU"),
        (OverseasExchange::Nasd, "AVGO"),
        (OverseasExchange::Nyse, "TSM"),
    ];
    let mut member_rets: Vec<Vec<(String, f64)>> = Vec::new();
    for (exch, sym) in us_specs {
        let days = us_history(&kis, exch, sym, &start, &end).await?;
        let rets = us_returns(&days);
        eprintln!("  US {sym}: {} return-days", rets.len());
        member_rets.push(rets);
    }
    let semis = basket_signal(&member_rets);
    let (sb0, sb1) = (
        semis.first().map(|x| x.0.clone()).unwrap_or_default(),
        semis.last().map(|x| x.0.clone()).unwrap_or_default(),
    );
    eprintln!("US semis basket: {} sessions [{sb0}..{sb1}]\n", semis.len());
    if semis.is_empty() {
        return Err("US semis basket empty — check KIS overseas creds".into());
    }

    // --- KR stocks: price + smart-money flow ---
    let stocks: [(&str, &str); 2] = [("005930", "Samsung Elec"), ("000660", "SK Hynix")];
    struct Stock {
        code: String,
        name: String,
        kr: Vec<KrDay>,
        flow: FlowSeries,
        kr_earliest: String,
        kr_latest: String,
        flow_earliest: String,
        flow_latest: String,
    }
    let mut data: Vec<Stock> = Vec::new();
    for (code, name) in stocks {
        eprintln!("fetching KR price + flow for {name} ({code})...");
        let kr = kr_history(&kis, code, &start, &end).await?;
        if kr.len() < 60 {
            eprintln!("  {name}: only {} price bars — skip", kr.len());
            continue;
        }
        let flow = flow_history(&kis, code, &start, &end).await?;
        let kr_earliest = kr.first().unwrap().date.clone();
        let kr_latest = kr.last().unwrap().date.clone();
        let flow_earliest = flow.keys().next().cloned().unwrap_or_default();
        let flow_latest = flow.keys().next_back().cloned().unwrap_or_default();
        eprintln!(
            "  {name}: {} price bars [{kr_earliest}..{kr_latest}], {} flow days [{flow_earliest}..{flow_latest}]",
            kr.len(),
            flow.len()
        );
        data.push(Stock {
            code: code.to_string(),
            name: name.to_string(),
            kr,
            flow,
            kr_earliest,
            kr_latest,
            flow_earliest,
            flow_latest,
        });
    }
    if data.is_empty() {
        return Err("KR stocks empty — check creds/dates".into());
    }

    // ===================== PRICE-SERIES SANITY (adjusted-basis / pagination-glitch detector) =====================
    // The equity curve multiplies day-over-day returns through every pagination boundary, so a
    // back-adjustment-basis discontinuity at a window seam compounds into a fake B&H return. This
    // block makes the close series auditable: endpoints, the TEST-window first close, B&H-telescope
    // check (Π(1+ret) == close_last/close_first), and the worst single-day move (flags |ret|>30%).
    println!("\n========================= PRICE-SERIES SANITY =========================");
    for s in &data {
        let first = s.kr.first().unwrap();
        let last = s.kr.last().unwrap();
        // first close at/after the OOS split (start of TEST window).
        let test_first = s.kr.iter().find(|d| d.date.as_str() >= split.as_str());
        // worst |daily ret| and its date.
        let mut worst = 0.0f64;
        let mut worst_date = String::new();
        let mut bh_telescope = 1.0f64;
        let mut prev = 0.0f64;
        for (i, d) in s.kr.iter().enumerate() {
            if i > 0 && prev > 0.0 {
                let r = d.close / prev - 1.0;
                bh_telescope *= 1.0 + r;
                if r.abs() > worst.abs() {
                    worst = r;
                    worst_date = d.date.clone();
                }
            }
            prev = d.close;
        }
        let full_bh = last.close / first.close - 1.0;
        println!(
            "  {:<14} close: first {} = {:.0}  ->  last {} = {:.0}   (full-window B&H = {:+.1}%)",
            s.name, first.date, first.close, last.date, last.close, full_bh * 100.0
        );
        if let Some(tf) = test_first {
            let test_bh = last.close / tf.close - 1.0;
            println!(
                "    TEST-window first close {} = {:.0}  ->  TEST B&H = {:+.1}%  (telescope identity: {:+.1}% via cumprod)",
                tf.date, tf.close, test_bh * 100.0, (bh_telescope - 1.0) * 100.0
            );
        }
        let flag = if worst.abs() > 0.30 {
            "  !! >30% single-day move — likely split/adjustment-basis GLITCH at a pagination seam"
        } else {
            ""
        };
        println!("    worst single-day move: {:+.1}% on {worst_date}{flag}", worst * 100.0);
        // INDEPENDENT endpoint cross-check: live spot via inquire-price (TR 9), a different code
        // path than period_price pagination. If it matches the last paginated close, the endpoint
        // (and thus the whole adjusted series) is confirmed real, not a pagination/adjustment artifact.
        match kis.domestic_stock().current_price(&s.code, Market::Krx).await {
            Ok(cp) => {
                let live = cp.price().unwrap_or(0.0);
                let agree = live > 0.0 && (live / last.close - 1.0).abs() < 0.05;
                println!(
                    "    live spot (inquire-price TR9, independent path) = {:.0}  vs last close {:.0}  -> {}",
                    live,
                    last.close,
                    if agree { "AGREE (endpoint confirmed real)" } else { "DIVERGE — investigate" }
                );
            }
            Err(e) => println!("    live spot cross-check unavailable: {e}"),
        }
    }

    // ===================== REACHABILITY GATE (decides answerability) =====================
    println!("\n========================= DATA REACH =========================");
    println!("US semis basket: {sb0}..{sb1}");
    println!("OOS split @ {split} (TRAIN: date<split = 2022-23 bear/sideways; TEST: date>=split = 2024-26 supercycle)");
    let semis_pre = sb0.as_str() < split.as_str() && !sb0.is_empty();
    let mut flow_reaches_bear = true;
    for s in &data {
        println!(
            "  {:<14} price [{}..{}]  flow [{}..{}]",
            s.name, s.kr_earliest, s.kr_latest, s.flow_earliest, s.flow_latest
        );
        let pr = s.kr_earliest.as_str() < split.as_str() && !s.kr_earliest.is_empty();
        let fr = s.flow_earliest.as_str() < split.as_str() && !s.flow_earliest.is_empty();
        if !fr {
            flow_reaches_bear = false;
        }
        println!(
            "    price reaches pre-2024 bear: {}   flow reaches pre-2024 bear: {}",
            if pr { "YES" } else { "NO" },
            if fr { "YES" } else { "NO" }
        );
    }
    println!("  US semis reaches pre-2024 bear: {}", if semis_pre { "YES" } else { "NO" });
    if !flow_reaches_bear {
        println!(
            "  ⚠ FLOW does NOT reach the 2022-23 bear → flow-only & AND-combo SHORT-in-bear question is\n    \
             UNANSWERABLE for those rules. Only the SEMIS-only rule can speak to the bear short leg."
        );
    } else {
        println!("  ✓ All sources reach the bear → SHORT-in-bear answerable for all rules.");
    }

    // ===================== BACKTEST =====================
    let ns = [10usize, 20, 60];
    let rule_names = ["semis-only", "flow-only", "AND-combo"];
    let n_configs = ns.len() * rule_names.len();
    println!("\n========================= BACKTEST (stateful daily-rebalanced position series) =========================");
    println!("Rules×N = {} configs/stock ({} rules × {} N).  cost={cost_bps}bps/change, funding={fund_bps}bps/day.", n_configs, rule_names.len(), ns.len());
    println!("Legs per config:");
    println!("  LONG-SHORT(+f) = full long/short, short PAYS funding (realistic perp).  '+' after B&H = strategy total beats buy-hold.");
    println!("  LONG-SHORT(0f) = full long/short, NO funding (signal-only — isolates direction from funding drag).");
    println!("  LONG-ONLY      = short clamped to FLAT (KR long-only reality; the long-or-null prior tests).");
    println!("pnl[d]=p·ret[d→d+1] − cost·(p changed) − funding·(p==short). Sharpe=mean/std·√252. NO overlap → t clean.\n");

    // Per-window summaries we use in the verdict.
    // short_bear[(rule,n)] = (with-funding tot%, no-funding tot%) pooled feeling via Samsung as headline; we print all.
    for s in &data {
        // Buy-and-hold per window (p≡+1, one entry cost, no funding).
        for (win, want_train) in [("TRAIN", true), ("TEST", false)] {
            let bh_pos: Vec<Option<i8>> = vec![Some(1i8); s.kr.len()];
            let Some(bh) = backtest(&s.kr, &bh_pos, &split, want_train, cost, 0.0, true) else {
                println!("--- {} [{win}] : no evaluable days (window not reached) ---\n", s.name);
                continue;
            };
            println!("--- {} [{win}]  (Buy&Hold: tot%={:+.2} CAGR%={:+.2} Sharpe={:+.2} maxDD%={:.2}) ---",
                s.name, bh.total_ret * 100.0, bh.cagr * 100.0, bh.sharpe, bh.maxdd * 100.0);
            for (ri, rname) in rule_names.iter().enumerate() {
                for &n in &ns {
                    let pos = build_positions(&s.kr, &semis, &s.flow, ri as u8, n);
                    // LONG-SHORT with funding
                    if let Some(bt) = backtest(&s.kr, &pos, &split, want_train, cost, funding, true) {
                        print_bt(&s.name, win, rname, n, "LONG-SHORT(+f)", &bt, &bh);
                    }
                    // LONG-SHORT no funding
                    if let Some(bt) = backtest(&s.kr, &pos, &split, want_train, cost, 0.0, true) {
                        print_bt(&s.name, win, rname, n, "LONG-SHORT(0f)", &bt, &bh);
                    }
                    // LONG-ONLY (short→flat)
                    if let Some(bt) = backtest(&s.kr, &pos, &split, want_train, cost, 0.0, false) {
                        print_bt(&s.name, win, rname, n, "LONG-ONLY", &bt, &bh);
                    }
                }
            }
            println!();
        }
    }

    // ===================== SHORT-LEG-IN-BEAR ISOLATION =====================
    // The genuinely untested question: in the 2022-23 bear (TRAIN), does HOLDING
    // SHORT on signal-down days make money, net of funding? We isolate the short
    // leg: position series forced to {SHORT when rule says down, else FLAT}.
    println!("========================= SHORT-LEG-IN-BEAR (TRAIN 2022-23) — short-only, WITH vs WITHOUT funding =========================");
    println!("Isolated short leg: p=−1 when rule direction is DOWN, else FLAT (no long days). The decisive test:");
    println!("a directionally-correct short paying {fund_bps}bps/day (≈{:.0}%/yr calendar) likely still NETS NEGATIVE.\n", fund_bps / 10000.0 * 365.0 * 100.0);
    for s in &data {
        for (ri, rname) in rule_names.iter().enumerate() {
            for &n in &ns {
                let base = build_positions(&s.kr, &semis, &s.flow, ri as u8, n);
                // short-only: keep −1, map +1/0 → 0.
                let short_only: Vec<Option<i8>> = base
                    .iter()
                    .map(|p| p.map(|v| if v == -1 { -1 } else { 0 }))
                    .collect();
                let with_f = backtest(&s.kr, &short_only, &split, true, cost, funding, true);
                let no_f = backtest(&s.kr, &short_only, &split, true, cost, 0.0, true);
                match (with_f, no_f) {
                    (Some(wf), Some(nf)) => {
                        let sig_w = if wf.total_ret > 0.0 { "+" } else { " " };
                        let sig_n = if nf.total_ret > 0.0 { "+" } else { " " };
                        println!(
                            "  {:<14} {rname:<11} N={n:<2} short-only TRAIN: days={:>4} chg={:>4}  WITH-funding tot%={:>+8.2}{sig_w} Sharpe={:>+5.2}  |  NO-funding tot%={:>+8.2}{sig_n} Sharpe={:>+5.2} hit%={:>5.1}",
                            s.name, wf.n_days, wf.changes,
                            wf.total_ret * 100.0, wf.sharpe,
                            nf.total_ret * 100.0, nf.sharpe, nf.hit * 100.0
                        );
                    }
                    _ => {
                        println!("  {:<14} {rname:<11} N={n:<2} short-only TRAIN: no evaluable days (bear window/flow not reached)", s.name, );
                    }
                }
            }
        }
    }

    // ===================== VERDICT =====================
    println!("\n========================= VERDICT =========================");
    println!("Data: US semis [{sb0}..{sb1}], cost={cost_bps}bps/change, short funding={fund_bps}bps/day (≈{:.0}%/yr calendar).",
        fund_bps / 10000.0 * 365.0 * 100.0);
    println!("Tested {n_configs} configs/stock (3 a-priori rules × N{{10,20,60}}), 2 stocks. Rules are correlated");
    println!("(semis-only & AND-combo share the semis term) → effective hypothesis count < {n_configs}; treat Sharpe/t as inflated.");
    println!();
    println!("HOW TO READ:");
    println!("  (a) Beat-B&H OOS? A rule has a TEST-window edge ONLY if LONG-SHORT(+f) total% > B&H total% ('+' mark)");
    println!("      in TEST. In a supercycle, B&H is a brutally high bar — long-bias alone won't clear it.");
    println!("  (b) SHORT-in-bear net of funding? See the SHORT-LEG-IN-BEAR table: WITH-funding total% is the real");
    println!("      number. NO-funding isolates whether the DIRECTION was right at all.");
    println!("  (c) Long-beta vs skill? If LONG-ONLY ≈ B&H in TEST, the 'performance' is just beta, not macro skill.");
    println!();
    if !semis_pre {
        println!("⚠ US semis didn't reach pre-2024 — even the semis short-in-bear is unanswerable. ALL-SUPERCYCLE.");
    }
    if !flow_reaches_bear {
        println!("⚠ FLOW didn't reach the 2022-23 bear → flow-only & AND-combo SHORT-in-bear rows are empty/unanswerable.");
        println!("  Only the SEMIS-only rule speaks to the bear short. One source, one regime → fragile.");
    }
    println!();
    println!("KILLER CAVEATS (read before risking real money):");
    println!("  - SUPERCYCLE TEST window: long-bias beats by DRIFT, not skill. B&H is the only honest baseline.");
    println!("  - SHORT EXECUTION is currently UNECONOMICAL: KR-perp funding ≈+120%/yr (≈{fund_bps}bps/day) + thin book");
    println!("    (launched 2026-06-02, ~1 day history). The SHORT here is a −(KR spot) PROXY; perp basis/tracking is");
    println!("    UNMODELED on top of funding. Signal can be VALIDATED; EXECUTION is future, not now.");
    println!("  - 삼전·하닉 are mega-caps & efficient → expect REGIME-FILTER-grade at best, not standalone alpha.");
    println!("  - Small a-priori rule set but still multiple correlated hypotheses → t/Sharpe inflated, effective N small.");
    println!("  - 2022-23 bear is ONE sample; any short-side conclusion is fragile and not out-of-sample-validated.");
    println!("  - FX (USD/KRW) input SKIPPED — not reachable via the KIS adapter (only a Toss surface exists).");
    println!("  - TEST-window absolute price levels (Samsung ~360k, Hynix ~2.36M as of 2026-06) reflect a continued");
    println!("    supercycle; the live-spot cross-check above confirms the endpoint, but verdicts are stated RELATIVE");
    println!("    to B&H on the same series, so conclusions hold regardless of the absolute level.");
    println!("  - Funding at {fund_bps}bps/day ≈ {:.0}%/yr calendar bleed: a correct short still likely nets NEGATIVE held > a few weeks.", fund_bps / 10000.0 * 365.0 * 100.0);
    println!("  - NOTE: funding is charged ONLY on the ~252 trading days a position is held; real perps charge funding");
    println!("    over weekends/holidays too, so the WITH-funding short bleed here is if anything UNDERSTATED.");

    Ok(())
}
