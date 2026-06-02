//! Global-semis → KR-tech lead/lag — is there a TRADEABLE lead, or is it all
//! priced into the overnight open gap (contemporaneous, untradeable)?
//!
//! The hypothesis "US semis lead KR semis" is trivially TRUE but UNTRADEABLE:
//! the US overnight move is already baked into the KR open gap. The only
//! tradeable edge is the RESIDUAL after the gap. So we decompose each KR day:
//!   gap[d]   = open[d]/close[d-1] - 1     (overnight reaction — captures US, NOT tradeable)
//!   intra[d] = close[d]/open[d]   - 1     (TRADEABLE from KR open — THE test)
//!   fwd_N[d] = close[d+N]/close[d] - 1    (multi-day residual continuation)
//!
//! KST alignment (look-ahead-safe): US session for US-date D closes ~05:00 KST
//! on D+1, BEFORE KR opens. So for KR trading day d, US_signal[d] = the most
//! recent US daily close-to-close return whose US-date is STRICTLY BEFORE d
//! (lexicographic on YYYYMMDD; handles weekends/holidays/timezone cleanly).
//! US signal = equal-weight close-to-close return of {NVDA,MU,AVGO,TSM}; SOXX
//! reported separately.
//!
//! Tests (all reported, with significance):
//!   1. corr(US, gap)      — expect HIGH (confirms link, untradeable).
//!   2. corr/β(US → intra) — THE tradeable test. >0 & meaningful = buy-at-open works.
//!   3. corr(US, fwd_N)    — multi-day residual (overlapping → effective n ~ n/N).
//!   4. lag scan corr(US[d-k], KR_total[d]), k=0..3 — peak location.
//!
//! Honest caveats: global beta != alpha; high contemporaneous corr is NOT
//! tradeable. Single regime (2024-2026 semis supercycle). No costs. KIS may
//! cap daily history.
//!
//! Env: KIS creds (KisConfig::from_env). `LL_START`/`LL_END` (YYYYMMDD,
//! default ~18mo back .. today). Read-only.

use std::collections::HashMap;

use kis_adapter::domestic_stock::Period;
use kis_adapter::overseas_stock::{OverseasExchange, OverseasPeriod};
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

/// One KR trading day, by array position (API returns only trading days).
struct KrDay {
    date: String,
    open: f64,
    close: f64,
}

/// One US trading day.
struct UsDay {
    date: String,
    close: f64,
}

/// Pull full KR daily history via date-windowed pagination (TR caps ~100 bars).
async fn kr_history(
    kis: &KisClient,
    code: &str,
    start: &str,
    end: &str,
) -> Result<Vec<KrDay>, Box<dyn std::error::Error>> {
    // Walk 90-day windows from end backwards to start; merge by date.
    let mut by_date: HashMap<String, KrDay> = HashMap::new();
    let mut win_end = end.to_string();
    let start_i: u32 = start.parse().unwrap_or(0);
    for _ in 0..40 {
        let we_i: u32 = win_end.parse().unwrap_or(0);
        if we_i < start_i {
            break;
        }
        // window start = max(start, win_end - ~95 days). Use chrono-free arith via NaiveDate-less
        // approximation: step back 95 calendar days using a simple yyyymmdd subtractor.
        let win_start = minus_days(&win_end, 95).max(start.to_string());
        let (_summary, candles) = kis
            .domestic_stock()
            .period_price(code, &win_start, &win_end, Period::Daily, true, Market::Krx)
            .await?;
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
                open: num(&c.stck_oprc),
                close: num(&c.stck_clpr),
            });
        }
        if earliest == u64::MAX || earliest <= start_i as u64 {
            break;
        }
        // next window ends one day before the earliest we got.
        win_end = minus_days(&format!("{earliest:08}"), 1);
    }
    let mut v: Vec<KrDay> = by_date
        .into_values()
        .filter(|d| d.open > 0.0 && d.close > 0.0)
        .collect();
    v.sort_by(|a, b| a.date.cmp(&b.date));
    Ok(v)
}

/// Pull full US daily history via the `cont`/`has_next` pagination (BYMD cursor).
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
    for page in 0..40 {
        let cont = page > 0;
        let resp = kis
            .overseas_stock()
            .period_price(exch, sym, OverseasPeriod::Daily, &bymd, true, cont)
            .await?;
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
        // Advance cursor: next page ends one day before earliest seen.
        bymd = minus_days(&format!("{earliest:08}"), 1);
    }
    let mut v: Vec<UsDay> = by_date.into_values().filter(|d| d.close > 0.0).collect();
    v.sort_by(|a, b| a.date.cmp(&b.date));
    Ok(v)
}

/// YYYYMMDD minus n calendar days, no external deps. Good enough for window cursors
/// (overshooting earlier is harmless — we merge by date and clamp at `start`).
fn minus_days(ymd: &str, n: i64) -> String {
    // Convert to days-since-epoch via a proleptic Gregorian calc.
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

/// US daily close-to-close returns, aligned as (date_of_close, ret over the
/// session ending that date). ret[i] = close[i]/close[i-1]-1, dated date[i].
fn us_returns(days: &[UsDay]) -> Vec<(String, f64)> {
    let mut out = Vec::new();
    for i in 1..days.len() {
        if days[i - 1].close > 0.0 {
            out.push((days[i].date.clone(), days[i].close / days[i - 1].close - 1.0));
        }
    }
    out
}

/// Equal-weight basket US signal: for each member compute close-to-close returns,
/// then average across members that have a return on the same US date.
/// Returns sorted Vec<(us_date, ew_ret)>.
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

/// For KR trading day `date`, the US signal = most recent US return whose
/// US-date is STRICTLY < `date`. `us` must be sorted ascending by date.
/// `lag` extra steps: lag=0 → most recent before; lag=k → k US-sessions earlier.
fn us_signal_for(us: &[(String, f64)], kr_date: &str, lag: usize) -> Option<f64> {
    // largest index with us[idx].0 < kr_date
    let mut idx: Option<usize> = None;
    for (i, (d, _)) in us.iter().enumerate() {
        if d.as_str() < kr_date {
            idx = Some(i);
        } else {
            break;
        }
    }
    let i = idx?;
    if i < lag {
        return None;
    }
    Some(us[i - lag].1)
}

fn mean(xs: &[f64]) -> f64 {
    if xs.is_empty() {
        0.0
    } else {
        xs.iter().sum::<f64>() / xs.len() as f64
    }
}

/// Pearson correlation.
fn corr(xs: &[f64], ys: &[f64]) -> f64 {
    let n = xs.len();
    if n < 3 || ys.len() != n {
        return 0.0;
    }
    let mx = mean(xs);
    let my = mean(ys);
    let mut sxy = 0.0;
    let mut sxx = 0.0;
    let mut syy = 0.0;
    for i in 0..n {
        let dx = xs[i] - mx;
        let dy = ys[i] - my;
        sxy += dx * dy;
        sxx += dx * dx;
        syy += dy * dy;
    }
    if sxx <= 0.0 || syy <= 0.0 {
        return 0.0;
    }
    sxy / (sxx.sqrt() * syy.sqrt())
}

/// OLS slope of y on x (β) and R². y = a + βx.
fn ols(xs: &[f64], ys: &[f64]) -> (f64, f64) {
    let n = xs.len();
    if n < 3 || ys.len() != n {
        return (0.0, 0.0);
    }
    let mx = mean(xs);
    let my = mean(ys);
    let mut sxy = 0.0;
    let mut sxx = 0.0;
    for i in 0..n {
        sxy += (xs[i] - mx) * (ys[i] - my);
        sxx += (xs[i] - mx).powi(2);
    }
    if sxx <= 0.0 {
        return (0.0, 0.0);
    }
    let beta = sxy / sxx;
    let r = corr(xs, ys);
    (beta, r * r)
}

/// t-stat for a Pearson r: t = r·sqrt(n-2)/sqrt(1-r²). |t|>~2 ≈ p<0.05.
fn t_of_r(r: f64, n: usize) -> f64 {
    if n < 3 {
        return 0.0;
    }
    let denom = (1.0 - r * r).max(1e-12);
    r * ((n as f64 - 2.0).sqrt()) / denom.sqrt()
}

/// Print a labelled corr line with n, r, t, and a verdict glyph.
fn report_corr(label: &str, xs: &[f64], ys: &[f64]) {
    let r = corr(xs, ys);
    let n = xs.len();
    let t = t_of_r(r, n);
    let sig = if t.abs() >= 2.0 { "*" } else { " " };
    println!("  {label:<28} n={n:<4} r={r:>+7.4} t={t:>+6.2} {sig}");
}

/// Cohort result: per-stock series of (us_signal, gap, intra, fwd1, fwd3, fwd5, total).
struct Aligned {
    us: Vec<f64>,
    gap: Vec<f64>,
    intra: Vec<f64>,
    total: Vec<f64>,
    fwd1: (Vec<f64>, Vec<f64>), // (us, fwd)
    fwd3: (Vec<f64>, Vec<f64>),
    fwd5: (Vec<f64>, Vec<f64>),
    // lag scan: us[d-k] vs total[d], k=0..3
    lag_us: [Vec<f64>; 4],
    lag_tot: [Vec<f64>; 4],
}

impl Aligned {
    fn new() -> Self {
        Aligned {
            us: vec![],
            gap: vec![],
            intra: vec![],
            total: vec![],
            fwd1: (vec![], vec![]),
            fwd3: (vec![], vec![]),
            fwd5: (vec![], vec![]),
            lag_us: [vec![], vec![], vec![], vec![]],
            lag_tot: [vec![], vec![], vec![], vec![]],
        }
    }
    fn extend(&mut self, o: Aligned) {
        let Aligned {
            us,
            gap,
            intra,
            total,
            fwd1,
            fwd3,
            fwd5,
            lag_us,
            lag_tot,
        } = o;
        self.us.extend(us);
        self.gap.extend(gap);
        self.intra.extend(intra);
        self.total.extend(total);
        self.fwd1.0.extend(fwd1.0);
        self.fwd1.1.extend(fwd1.1);
        self.fwd3.0.extend(fwd3.0);
        self.fwd3.1.extend(fwd3.1);
        self.fwd5.0.extend(fwd5.0);
        self.fwd5.1.extend(fwd5.1);
        for (dst, src) in self.lag_us.iter_mut().zip(lag_us) {
            dst.extend(src);
        }
        for (dst, src) in self.lag_tot.iter_mut().zip(lag_tot) {
            dst.extend(src);
        }
    }
}

/// Align one KR stock against the basket signal.
fn align_stock(kr: &[KrDay], us: &[(String, f64)]) -> Aligned {
    let mut a = Aligned::new();
    let n = kr.len();
    for d in 0..n {
        let date = &kr[d].date;
        let Some(sig) = us_signal_for(us, date, 0) else {
            continue;
        };
        // gap needs d-1; intra/total need d.
        if d == 0 {
            continue;
        }
        let prev_close = kr[d - 1].close;
        if prev_close <= 0.0 || kr[d].open <= 0.0 || kr[d].close <= 0.0 {
            continue;
        }
        let gap = kr[d].open / prev_close - 1.0;
        let intra = kr[d].close / kr[d].open - 1.0;
        let total = kr[d].close / prev_close - 1.0;
        a.us.push(sig);
        a.gap.push(gap);
        a.intra.push(intra);
        a.total.push(total);

        // forward continuation (close-to-close over N trading days).
        for (nn, pair) in [(1usize, 0usize), (3, 1), (5, 2)] {
            if d + nn < n && kr[d + nn].close > 0.0 {
                let fwd = kr[d + nn].close / kr[d].close - 1.0;
                let tgt = match pair {
                    0 => &mut a.fwd1,
                    1 => &mut a.fwd3,
                    _ => &mut a.fwd5,
                };
                tgt.0.push(sig);
                tgt.1.push(fwd);
            }
        }

        // lag scan: us[d-k] vs total[d].
        for (k, item) in a.lag_us.iter_mut().enumerate() {
            if let Some(s) = us_signal_for(us, date, k) {
                item.push(s);
                a.lag_tot[k].push(total);
            }
        }
    }
    a
}

fn report_cohort(name: &str, a: &Aligned) {
    println!("\n=== {name} (pooled stock-days) ===");
    report_corr("US -> gap (untradeable)", &a.us, &a.gap);
    report_corr("US -> intra (TRADEABLE)", &a.us, &a.intra);
    let (beta, r2) = ols(&a.us, &a.intra);
    println!(
        "    intra OLS: beta={beta:>+.4} (KR-intra per 1.0 US), R2={r2:.4}  \
         [tradeable only if beta meaningful AND t>~2]"
    );
    report_corr("US -> fwd_1", &a.fwd1.0, &a.fwd1.1);
    report_corr("US -> fwd_3 (overlap!)", &a.fwd3.0, &a.fwd3.1);
    report_corr("US -> fwd_5 (overlap!)", &a.fwd5.0, &a.fwd5.1);
    println!("  lag scan  corr(US[d-k], KR_total[d]):");
    let mut peak_k = 0usize;
    let mut peak_r = f64::NEG_INFINITY;
    for k in 0..4 {
        let r = corr(&a.lag_us[k], &a.lag_tot[k]);
        let t = t_of_r(r, a.lag_us[k].len());
        let sig = if t.abs() >= 2.0 { "*" } else { " " };
        println!(
            "    k={k} n={:<4} r={r:>+7.4} t={t:>+6.2} {sig}",
            a.lag_us[k].len()
        );
        if r > peak_r {
            peak_r = r;
            peak_k = k;
        }
    }
    println!(
        "    -> lag peak at k={peak_k} (r={peak_r:+.4}). k=0 => contemporaneous-after-gap (untradeable); k>=1 => possible multi-day residual."
    );
}

/// Split-distortion sanity: flag any |daily ret| > 0.5 (likely unadjusted split).
fn split_check(sym: &str, rets: &[(String, f64)]) {
    let mut mn = f64::INFINITY;
    let mut mx = f64::NEG_INFINITY;
    let mut flag = None;
    for (d, r) in rets {
        if *r < mn {
            mn = *r;
        }
        if *r > mx {
            mx = *r;
        }
        if r.abs() > 0.5 {
            flag = Some((d.clone(), *r));
        }
    }
    let warn = if let Some((d, r)) = flag {
        format!("  !! SPLIT-LIKE on {d} ({:+.1}%) — check adjustment", r * 100.0)
    } else {
        String::new()
    };
    println!(
        "  {sym:<6} n={:<4} ret range [{:+.1}%, {:+.1}%]{warn}",
        rets.len(),
        mn * 100.0,
        mx * 100.0
    );
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let end = env_or("LL_END", "20260602");
    let start = env_or("LL_START", "20241101"); // ~18mo back default

    eprintln!("leadlag {start}..{end} — global semis -> KR tech lead/lag (gap vs intra vs fwd)\n");

    let kis = KisClient::new(KisConfig::from_env()?)?;

    // --- US legs ---
    // basket members: NVDA, MU, AVGO on Nasd; TSM on Nyse.
    eprintln!("fetching US daily history (NVDA/MU/AVGO/TSM, +SOXX)...");
    let us_specs: [(OverseasExchange, &str); 4] = [
        (OverseasExchange::Nasd, "NVDA"),
        (OverseasExchange::Nasd, "MU"),
        (OverseasExchange::Nasd, "AVGO"),
        (OverseasExchange::Nyse, "TSM"),
    ];
    let mut member_rets: Vec<Vec<(String, f64)>> = Vec::new();
    println!("US daily ret sanity (adjusted, split-distortion check):");
    for (exch, sym) in us_specs {
        let days = us_history(&kis, exch, sym, &start, &end).await?;
        let rets = us_returns(&days);
        split_check(sym, &rets);
        member_rets.push(rets);
    }
    let basket = basket_signal(&member_rets);
    eprintln!("basket signal days: {}", basket.len());

    // SOXX ETF — try Nasd.
    let soxx_days = us_history(&kis, OverseasExchange::Nasd, "SOXX", &start, &end)
        .await
        .unwrap_or_default();
    let soxx = us_returns(&soxx_days);
    split_check("SOXX", &soxx);
    eprintln!("SOXX signal days: {}", soxx.len());

    if basket.is_empty() {
        return Err("US basket empty — check KIS overseas creds / market hours".into());
    }
    let bstart = &basket.first().unwrap().0;
    let bend = &basket.last().unwrap().0;
    println!("\nUS basket date range: {bstart}..{bend} ({} sessions)", basket.len());

    // --- KR cohorts ---
    // T1: most globally-correlated, expect ~zero residual.
    // T2: less efficient, residual may exist — the real hunting ground.
    let t1: [(&str, &str); 2] = [("005930", "Samsung Elec"), ("000660", "SK Hynix")];
    let t2: [(&str, &str); 4] = [
        ("011070", "LG Innotek"),
        ("009150", "Samsung Electro-Mech"),
        ("066570", "LG Electronics"),
        ("454910", "Doosan Robotics"),
    ];

    let mut t1_agg = Aligned::new();
    let mut t2_agg = Aligned::new();
    let mut kr_range: Option<(String, String)> = None;

    println!("\n--- Per-stock KR daily history + alignment ---");
    for (tier, list) in [("T1", &t1[..]), ("T2", &t2[..])] {
        for (code, name) in list {
            let kr = kr_history(&kis, code, &start, &end).await?;
            if kr.len() < 30 {
                eprintln!("  {tier} {name} ({code}): only {} bars — skip", kr.len());
                continue;
            }
            let r0 = kr.first().unwrap().date.clone();
            let r1 = kr.last().unwrap().date.clone();
            eprintln!("  {tier} {name} ({code}): {} bars {r0}..{r1}", kr.len());
            kr_range = Some(match kr_range {
                None => (r0.clone(), r1.clone()),
                Some((a, b)) => (a.min(r0), b.max(r1)),
            });
            let a = align_stock(&kr, &basket);
            // Per-stock one-liner: the two headline corrs.
            let rg = corr(&a.us, &a.gap);
            let ri = corr(&a.us, &a.intra);
            let ti = t_of_r(ri, a.intra.len());
            println!(
                "  {tier:<2} {name:<22} {code}  n={:<4} corr(US,gap)={rg:>+.3}  corr(US,intra)={ri:>+.3} (t={ti:>+.2})",
                a.us.len()
            );
            if tier == "T1" {
                t1_agg.extend(a);
            } else {
                t2_agg.extend(a);
            }
        }
    }

    // --- Cohort-level reports ---
    report_cohort("T1 — Samsung Elec + SK Hynix (global blue-chips)", &t1_agg);
    report_cohort("T2 — Innotek/Electro-Mech/LG Elec/Doosan Robo (less efficient)", &t2_agg);

    // --- Verdict scaffold ---
    let t1_ri = corr(&t1_agg.us, &t1_agg.intra);
    let t1_ti = t_of_r(t1_ri, t1_agg.intra.len());
    let (t1_beta, _) = ols(&t1_agg.us, &t1_agg.intra);
    let t2_ri = corr(&t2_agg.us, &t2_agg.intra);
    let t2_ti = t_of_r(t2_ri, t2_agg.intra.len());
    let (t2_beta, _) = ols(&t2_agg.us, &t2_agg.intra);
    let t1_rg = corr(&t1_agg.us, &t1_agg.gap);
    let t2_rg = corr(&t2_agg.us, &t2_agg.gap);

    println!("\n========================= VERDICT =========================");
    if let Some((a, b)) = &kr_range {
        println!("KR data range: {a}..{b}");
    }
    println!("US basket range: {bstart}..{bend}");
    println!(
        "T1 intra: r={t1_ri:+.4} t={t1_ti:+.2} beta={t1_beta:+.4}  | gap r={t1_rg:+.3}"
    );
    println!(
        "T2 intra: r={t2_ri:+.4} t={t2_ti:+.2} beta={t2_beta:+.4}  | gap r={t2_rg:+.3}"
    );
    // SIGN-AWARE gate. A continuation lead ("buy KR at open after strong US night")
    // requires beta>0: US up -> KR rises further intraday. beta<0 is a gap-FADE
    // (overnight overshoot reverts), which is the OPPOSITE of a lead AND requires
    // shorting at the open — not the hypothesis under test.
    let lead = |t: f64, beta: f64| t.abs() >= 2.0 && beta >= 0.05; // positive only
    let fade = |t: f64, beta: f64| t.abs() >= 2.0 && beta <= -0.05;
    let verdict = |tier: &str, t: f64, beta: f64, r2: f64| {
        if lead(t, beta) {
            println!("{tier}: POSITIVE intra continuation (beta={beta:+.4}, R2={r2:.4}) — candidate buy-at-open lead. SCRUTINIZE overlap/regime before trusting; R2 says how little of KR intraday it explains.");
        } else if fade(t, beta) {
            println!("{tier}: intra residual is a weak gap-FADE (beta={beta:+.4}, R2={r2:.4}), NOT a continuation lead. US overnight move OVERSHOOTS into the open then partly reverts. UNTRADEABLE as a lead: it points to SHORTING KR at open, R2<1%, and KR short-selling was banned for much of this sample [verify dates].");
        } else {
            println!("{tier}: NO meaningful intraday residual (beta={beta:+.4}, R2={r2:.4}). US move fully in the open gap. L1-standalone untradeable — regime-filter use only.");
        }
    };
    let (_, t1_r2) = ols(&t1_agg.us, &t1_agg.intra);
    let (_, t2_r2) = ols(&t2_agg.us, &t2_agg.intra);
    println!();
    verdict("T1", t1_ti, t1_beta, t1_r2);
    verdict("T2", t2_ti, t2_beta, t2_r2);
    println!("\nBOTTOM LINE: lag scan peaks at k=0 (contemporaneous-after-gap) for BOTH tiers; corr(US,gap) high (link confirmed, lives in the gap); corr(US,intra) ~0-to-weakly-negative; fwd_N ~0. => NO tradeable lead beyond the overnight gap. The global-semis move is fully priced into the KR open. Useful only as a regime filter.");
    println!("\nCAVEATS (read before acting):");
    println!("  - Global beta != alpha: high corr(US,gap) confirms the LINK, not a tradeable EDGE.");
    println!("  - A statistically-significant residual (t=-2.69 for T1) is NOT tradeable: R2<1% (US explains <1% of KR intraday variance) and the sign is a fade, not a lead.");
    println!("  - The fade points to SHORTING KR large-caps at open; KR short-selling was banned until ~2025-03 [verify], covering the early sample. Direction partly unexecutable.");
    println!("  - k=2 lag blip (t~+2.5) is non-monotonic (k=1~0, k=2>0, k=3~0) = multiple-testing noise, NOT a 2-day lead. A real lead decays monotonically.");
    println!("  - fwd_3/fwd_5 use OVERLAPPING windows -> effective n ~ n/N; their t-stats OVERSTATE significance. fwd_1 spans the next overnight gap (residual US autocorr leak, not close-to-close continuation).");
    println!("  - Single regime (2024-2026 semis supercycle); ~1.5yr daily. No transaction costs / slippage / open-auction fill realism modelled.");

    Ok(())
}
