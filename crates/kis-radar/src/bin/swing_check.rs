//! 하루텀(1-day-to-few-day HOLD) swing 신호 검증 — 삼전(005930)·하닉(000660) 일봉.
//!
//! 호라이즌: 단타(intraday, 이미 검증=fade)와 포지셔널(주/월, 이미 검증=null) 사이.
//! 일봉으로 forward M거래일 close-to-close를 예측하는 신호 4종을 시험.
//!
//! ⚠️ CRITICAL TRAP: 슈퍼사이클 UPTREND에서는 daily momentum이 drift를 타고
//! "그냥 된다". 따라서 유일하게 의미있는 결과는 (a) buy-and-hold 베이스라인을
//! 초과하는 edge AND (b) out-of-sample(레짐 분리). drift-riding을 alpha로 팔지 말 것.
//! 자매 검증 `trend_overlay`는 daily SMA 타이밍 = risk-mgmt 베타이지 alpha 아님 결론.
//!
//! 방법론(advisor 제약 반영):
//! - **진입 = close[d] 통일** (gap 부호는 장중 known, look-ahead-safe). forward = close[d+M].
//! - ret_fwd[d] = close[d+M]/close[d] − 1 (gross, close-to-close).
//! - **baseline = 신호와 동일한 평가가능 날짜집합 위의 mean(ret_fwd)** = M일 buy-hold drift.
//!   (룩백 N일 존재 + d+M 존재하는 날만 — edge가 표본창 아티팩트가 아니라 순수 selection이도록.)
//! - **edge_gross = mean(ret_fwd | signal-long) − baseline** (selection skill, 비용무관).
//! - **비용은 대칭상쇄되므로 edge에서 빼지 않음. 대신 HURDLE로 재도입**:
//!   edge_net = edge_gross − cost. buy-hold는 전 구간 왕복 ~1회(/period≈0), active는
//!   매 트레이드 왕복 → "B&H를 비용후 이긴다" ⇔ edge_net>0 (보수적·worst-case). cost_bps env.
//! - **t-stat: 헤드라인은 step=M(비중첩)** per-period series로. 중첩 M일 윈도는 t 부풀림.
//!   참고로 step=1(중첩) t도 병기하되 effective N << n 명시.
//! - **OOS split**: train(~2022–2023, 도달 가능하면) vs test(2024–2026 슈퍼사이클).
//!   ⚠️ KIS가 pre-2024 일봉을 안 주면 진짜 train 윈도가 없음 → "all-supercycle,
//!   cannot separate alpha from drift"를 크게 선언. 실제 도달 최earliest 날짜 보고.
//!
//! 신호(각각 long·short leg 보고; short는 2025-03-31 이전 execution-constrained 플래그):
//!   1. momentum   — trailing N일 수익률>0 → long (continuation).
//!   2. reversion  — momentum의 reverse (어제 down → buy). = momentum-short와 동치 (독립 증거 아님).
//!   3. us_trend   — US 반도체 바스켓 trailing N일 수익률 부호 → KR forward.
//!   4. gap        — 오늘 시가갭 부호 → forward (gap-up day가 continuation을 예측하나).
//!
//! 환경: KIS creds(KisConfig::from_env). `SW_START`(YYYYMMDD, 기본 20220101),
//! `SW_END`(기본 20260602), `SW_COST_BPS`(왕복 비용 bps, 기본 35),
//! `SW_OOS_SPLIT`(train/test 경계 YYYYMMDD, 기본 20240101). 읽기전용.

use std::collections::HashMap;

use kis_adapter::kis::domestic_stock::Period;
use kis_adapter::kis::overseas_stock::{OverseasExchange, OverseasPeriod};
use kis_adapter::{KisClient, KisConfig, Market};

/// KIS 콤마 문자열 → f64.
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

/// 한 KR 거래일 (API는 거래일만 반환).
struct KrDay {
    date: String,
    open: f64,
    close: f64,
}

/// 한 US 거래일.
struct UsDay {
    date: String,
    close: f64,
}

/// YYYYMMDD − n calendar days (외부 deps 없음). 윈도 커서용 (앞으로 overshoot 무해 — by-date merge + start clamp).
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

/// KR 일봉 전구간 — period_price 호출당 ~100건 → end를 뒤로 밀며 페이징.
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
                open: num(&c.stck_oprc),
                close: num(&c.stck_clpr),
            });
        }
        if earliest == u64::MAX || earliest <= start_i as u64 {
            break;
        }
        // 진전 가드 — earliest가 줄지 않으면(KIS history cap 도달) 무한루프 방지.
        if earliest >= prev_earliest {
            break;
        }
        prev_earliest = earliest;
        win_end = minus_days(&format!("{earliest:08}"), 1);
        tokio::time::sleep(std::time::Duration::from_millis(120)).await;
    }
    let mut v: Vec<KrDay> = by_date
        .into_values()
        .filter(|d| d.open > 0.0 && d.close > 0.0)
        .collect();
    v.sort_by(|a, b| a.date.cmp(&b.date));
    Ok(v)
}

/// US 일봉 전구간 — cont/has_next 페이징 (BYMD 커서).
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

/// US 일별 close-to-close 수익률 (date_of_close, ret). ret[i]=close[i]/close[i-1]-1, dated date[i].
fn us_returns(days: &[UsDay]) -> Vec<(String, f64)> {
    let mut out = Vec::new();
    for i in 1..days.len() {
        if days[i - 1].close > 0.0 {
            out.push((days[i].date.clone(), days[i].close / days[i - 1].close - 1.0));
        }
    }
    out
}

/// Equal-weight 바스켓 신호: 멤버별 close-to-close 수익률 평균(같은 US date에 ret 있는 멤버만).
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

/// US 바스켓의 trailing-N일 누적 수익률, KR date에 KST-align(US date STRICTLY < kr_date).
/// `us` 정렬된 (date, daily_ret). 가장 최근 US date < kr_date에서 N일 backward 누적.
fn us_trailing_n(us: &[(String, f64)], kr_date: &str, n: usize) -> Option<f64> {
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
    if i + 1 < n {
        return None; // 충분한 trailing history 없음
    }
    let mut acc = 1.0;
    for (_, r) in &us[(i + 1 - n)..=i] {
        acc *= 1.0 + r;
    }
    Some(acc - 1.0)
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

/// 한 (long-leg net_fwd 시계열, baseline-net_fwd 시계열) → edge mean + t-stat.
/// edge_d = ret_long[d] − baseline_mean (paired-vs-mean이 아니라, long days만의 ret − all-days mean).
/// t-stat: long-leg ret 시계열의 mean vs baseline mean의 차이를 long-leg sd로. (간이 1-sample on long − base.)
struct Leg {
    n: usize,
    sig_mean: f64,  // mean ret_fwd on signal-active days (long leg)
    edge_gross: f64, // sig_mean − baseline
    t: f64,         // t-stat of (ret_long − baseline) treated as 1-sample
    win: f64,       // share of long days with ret_fwd>0
}

/// signal-long days의 net_fwd 분포에서 edge와 t. baseline은 외부에서 계산된 전체 mean.
fn leg_stats(long_rets: &[f64], baseline: f64) -> Leg {
    let n = long_rets.len();
    if n == 0 {
        return Leg { n: 0, sig_mean: 0.0, edge_gross: 0.0, t: 0.0, win: 0.0 };
    }
    let sig_mean = mean(long_rets);
    let edge = sig_mean - baseline;
    // t on the demeaned-by-baseline series: (ret - baseline) per long day, 1-sample t vs 0.
    let dev: Vec<f64> = long_rets.iter().map(|r| r - baseline).collect();
    let dm = mean(&dev);
    let sd = std(&dev, dm);
    let t = if sd > 0.0 && n >= 2 {
        dm / (sd / (n as f64).sqrt())
    } else {
        0.0
    };
    let win = long_rets.iter().filter(|r| **r > 0.0).count() as f64 / n as f64;
    Leg { n, sig_mean, edge_gross: edge, t, win }
}

/// 평가 1회: 신호값 vector + 동일 평가집합의 forward ret vector + 날짜.
/// step: 헤드라인 t용 비중첩 샘플링 간격(=M이면 비중첩).
struct EvalRow {
    name: String,
    signal: String,
    n: usize, // M
    edge_l: Leg, // CONTINUATION-LONG: signal>0 → long, +ret. baseline=+drift. executable.
    edge_r: Leg, // REVERSION-LONG: signal<0 → long, +ret. baseline=+drift. executable (no short ban).
    edge_s: Leg, // CONTINUATION-SHORT: signal<0 → hold short, pnl=-ret. baseline=-drift. exec-constrained.
    baseline: f64,
    n_eval: usize,
}

/// 한 종목·한 신호·(N,M)·OOS-window에서 long/short leg 평가.
/// sig_fn(d)는 룩백 신호값(없으면 None). step으로 비중첩 샘플링.
#[allow(clippy::too_many_arguments)]
fn evaluate<F>(
    name: &str,
    signal: &str,
    kr: &[KrDay],
    m: usize,
    step: usize,
    split: &str,
    want_train: bool, // true=train(date<split), false=test(date>=split)
    sig_fn: F,
) -> Option<EvalRow>
where
    F: Fn(usize) -> Option<f64>,
{
    let n = kr.len();
    let mut all_rets = Vec::new(); // baseline: 평가가능 모든 날 ret_fwd
    let mut long_rets = Vec::new(); // continuation-LONG: signal>0 → +ret
    let mut rev_rets = Vec::new(); // reversion-LONG: signal<0 → +ret (executable, no short ban)
    let mut short_rets = Vec::new(); // continuation-SHORT: signal<0 → hold short → pnl=-ret
    let mut i = 0usize;
    while i < n {
        // forward 존재?
        if i + m >= n {
            break;
        }
        let date = &kr[i].date;
        // OOS 윈도 필터 (진입일 기준).
        let in_train = date.as_str() < split;
        if in_train != want_train {
            i += step;
            continue;
        }
        let c0 = kr[i].close;
        let cf = kr[i + m].close;
        if c0 <= 0.0 || cf <= 0.0 {
            i += step;
            continue;
        }
        let Some(sig) = sig_fn(i) else {
            i += step;
            continue;
        };
        let ret = cf / c0 - 1.0;
        all_rets.push(ret);
        if sig > 0.0 {
            long_rets.push(ret); // continuation-long
        } else if sig < 0.0 {
            rev_rets.push(ret); // reversion-long: buy the down day, +ret (executable)
            short_rets.push(-ret); // continuation-short: hold short, pnl=-ret (exec-constrained)
        }
        i += step;
    }
    if all_rets.len() < 5 {
        return None;
    }
    let baseline = mean(&all_rets);
    let edge_l = leg_stats(&long_rets, baseline);
    let edge_r = leg_stats(&rev_rets, baseline); // long-side baseline = +drift
    let edge_s = leg_stats(&short_rets, -baseline); // short the universe == -drift
    Some(EvalRow {
        name: name.to_string(),
        signal: signal.to_string(),
        n: m,
        edge_l,
        edge_r,
        edge_s,
        baseline,
        n_eval: all_rets.len(),
    })
}

/// KR trailing-N일 close-to-close 수익률 (룩백, look-ahead-safe: close[i-N..i] 사용).
fn kr_trailing_n(kr: &[KrDay], i: usize, n: usize) -> Option<f64> {
    if i < n {
        return None;
    }
    let c0 = kr[i - n].close;
    let cn = kr[i].close;
    if c0 <= 0.0 || cn <= 0.0 {
        return None;
    }
    Some(cn / c0 - 1.0)
}

/// 오늘 시가갭 부호: open[i]/close[i-1]-1. close[i] 진입이므로 갭은 진입 전 known.
fn gap_sign(kr: &[KrDay], i: usize) -> Option<f64> {
    if i < 1 {
        return None;
    }
    let pc = kr[i - 1].close;
    if pc <= 0.0 || kr[i].open <= 0.0 {
        return None;
    }
    Some(kr[i].open / pc - 1.0)
}

#[allow(clippy::too_many_arguments)]
fn print_row(r: &EvalRow, cost: f64, leg: &str, l: &Leg, base: f64, short_flag: bool) {
    let edge_net = l.edge_gross - cost;
    let sig = if l.t.abs() >= 2.0 { "*" } else { " " };
    let net_mark = if edge_net > 0.0 { "+" } else { " " };
    let flag = if short_flag { " (short:exec-constrained<2025-03-31)" } else { "" };
    println!(
        "  {:<16} {:<10} M={} {leg:<5} n={:>4} sig%={:>+7.3} base%={:>+7.3} edge_g%={:>+7.3} edge_net%={:>+7.3}{net_mark} t={:>+6.2}{sig} win%={:>5.1}{flag}",
        r.name,
        r.signal,
        r.n,
        l.n,
        l.sig_mean * 100.0,
        base * 100.0,
        l.edge_gross * 100.0,
        edge_net * 100.0,
        l.t,
        l.win * 100.0,
    );
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let start = env_or("SW_START", "20220101");
    let end = env_or("SW_END", "20260602");
    let cost_bps: f64 = env_or("SW_COST_BPS", "35").parse().unwrap_or(35.0);
    let cost = cost_bps / 10000.0;
    let split = env_or("SW_OOS_SPLIT", "20240101");

    eprintln!(
        "swing_check {start}..{end} — 하루텀(1-day-to-few-day HOLD) swing 신호 (삼전·하닉 일봉)"
    );
    eprintln!("cost={cost_bps}bps round-trip (hurdle), OOS split @ {split}\n");

    let kis = KisClient::new(KisConfig::from_env()?)?;

    // --- US 반도체 바스켓 (신호 3용) ---
    eprintln!("fetching US daily history (NVDA/MU/AVGO/TSM)...");
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
    let basket = basket_signal(&member_rets);
    let (ub0, ub1) = (
        basket.first().map(|x| x.0.clone()).unwrap_or_default(),
        basket.last().map(|x| x.0.clone()).unwrap_or_default(),
    );
    eprintln!("US basket: {} sessions [{ub0}..{ub1}]\n", basket.len());

    // --- KR 종목 일봉 ---
    let stocks: [(&str, &str); 2] = [("005930", "Samsung Elec"), ("000660", "SK Hynix")];
    let mut kr_data: Vec<(String, String, Vec<KrDay>)> = Vec::new();
    let mut earliest_reached = String::new();
    let mut latest_reached = String::new();
    for (code, name) in stocks {
        let kr = kr_history(&kis, code, &start, &end).await?;
        if kr.len() < 60 {
            eprintln!("  {name} ({code}): only {} bars — skip", kr.len());
            continue;
        }
        let r0 = kr.first().unwrap().date.clone();
        let r1 = kr.last().unwrap().date.clone();
        eprintln!("  {name} ({code}): {} bars [{r0}..{r1}]", kr.len());
        if earliest_reached.is_empty() || r0 < earliest_reached {
            earliest_reached = r0.clone();
        }
        if r1 > latest_reached {
            latest_reached = r1.clone();
        }
        kr_data.push((code.to_string(), name.to_string(), kr));
    }
    if kr_data.is_empty() {
        return Err("KR 종목 0개 수집 — 크레덴셜/날짜 확인".into());
    }

    // --- OOS reachability gate (advisor point 2: 가장 먼저 확인) ---
    println!("\n========================= DATA REACH =========================");
    println!("KR daily earliest reached: {earliest_reached}  latest: {latest_reached}");
    println!("US basket range: {ub0}..{ub1}");
    println!("OOS split @ {split}  (train: date<split, test: date>=split)");
    let train_reaches_pre2024 =
        earliest_reached.as_str() < split.as_str() && !earliest_reached.is_empty();
    if train_reaches_pre2024 {
        println!(
            "✓ Train window reaches pre-split data ({earliest_reached} < {split}) — real OOS separation possible."
        );
    } else {
        println!(
            "⚠ Train window does NOT reach pre-split ({earliest_reached} >= {split}). \
             ALL-SUPERCYCLE sample — cannot separate alpha from drift. Train numbers will be empty/weak; \
             treat any 'edge' as drift-riding, NOT alpha."
        );
    }

    // --- 신호 정의 ---
    // (signal_name, N) 그리드. N=0은 gap/us에서 무시.
    let mom_ns = [1usize, 3, 5, 10];
    let us_ns = [1usize, 3, 5, 10];
    let ms = [1usize, 2, 5];

    // 결과 출력: 종목별 × 신호 × N × M × OOS-window.
    // 헤드라인 t = step=M (비중첩). 병기로 step=1(중첩) t도.
    println!("\n========================= RESULTS (headline: step=M, non-overlapping) =========================");
    println!("edge_g% = sig_mean − baseline (selection, cost-free).  edge_net% = edge_g − cost (HURDLE).");
    println!("'beats B&H after cost' ⇔ edge_net%>0 (marked '+') AND in BOTH train&test.  '*' = |t|>=2 (non-overlap).");
    println!("legs: CLONG=continuation-long(sig>0 buy). RLONG=reversion-long(sig<0 buy, executable). CSHORT=continuation-short");
    println!("      (sig<0 hold-short pnl=-ret, exec-constrained <2025-03-31). RLONG & CSHORT are sign-flipped views of the");
    println!("      same sig<0 days — judge reversion off RLONG's own edge_net, NOT by flipping CSHORT.\n");

    for (code, name, kr) in &kr_data {
        let _ = code;
        for (win_name, want_train) in [("TRAIN", true), ("TEST", false)] {
            println!("--- {name} [{win_name}] ---");
            // 1. momentum (continuation: sig>0 → long) & 2. reversion (= momentum-short, 동일 데이터)
            for &nn in &mom_ns {
                for &m in &ms {
                    let row = evaluate(
                        name,
                        &format!("mom_N{nn}"),
                        kr,
                        m,
                        m, // step=M 비중첩
                        &split,
                        want_train,
                        |i| kr_trailing_n(kr, i, nn),
                    );
                    if let Some(r) = row {
                        // CONT-LONG (continuation): trailing>0 → buy.
                        print_row(&r, cost, "CLONG", &r.edge_l, r.baseline, false);
                        // REV-LONG (reversion, signal #2): trailing<0 → buy. Executable LONG, no short ban.
                        print_row(&r, cost, "RLONG", &r.edge_r, r.baseline, false);
                        // CONT-SHORT: trailing>0... no — hold short on trailing<0 days; exec-constrained.
                        print_row(&r, cost, "CSHORT", &r.edge_s, -r.baseline, true);
                        let _ = r.n_eval;
                    }
                }
            }
            // 3. us_trend (US 바스켓 trailing-N → KR forward)
            for &nn in &us_ns {
                for &m in &ms {
                    let row = evaluate(
                        name,
                        &format!("us_N{nn}"),
                        kr,
                        m,
                        m,
                        &split,
                        want_train,
                        |i| us_trailing_n(&basket, &kr[i].date, nn),
                    );
                    if let Some(r) = row {
                        print_row(&r, cost, "CLONG", &r.edge_l, r.baseline, false);
                        print_row(&r, cost, "RLONG", &r.edge_r, r.baseline, false);
                        print_row(&r, cost, "CSHORT", &r.edge_s, -r.baseline, true);
                    }
                }
            }
            // 4. gap (오늘 시가갭 부호 → forward continuation)
            for &m in &ms {
                let row = evaluate(
                    name,
                    "gap",
                    kr,
                    m,
                    m,
                    &split,
                    want_train,
                    |i| gap_sign(kr, i),
                );
                if let Some(r) = row {
                    print_row(&r, cost, "CLONG", &r.edge_l, r.baseline, false);
                    print_row(&r, cost, "RLONG", &r.edge_r, r.baseline, false);
                    print_row(&r, cost, "CSHORT", &r.edge_s, -r.baseline, true);
                }
            }
            println!();
        }
    }

    // --- 중첩(step=1) t 병기: 헤드라인 momentum N=5,M=5만 샘플로 (overlap이 t를 얼마나 부풀리는지) ---
    println!("========================= OVERLAP t DIAGNOSTIC (step=1, inflated) =========================");
    println!("step=1 중첩 윈도 → effective N << n. 아래 t는 OVERSTATED — 헤드라인(step=M)과 비교용.\n");
    for (_, name, kr) in &kr_data {
        for (win_name, want_train) in [("TRAIN", true), ("TEST", false)] {
            for &(nn, m) in &[(5usize, 5usize), (3, 2)] {
                let overlap = evaluate(
                    name,
                    &format!("mom_N{nn}_M{m}"),
                    kr,
                    m,
                    1, // step=1 중첩
                    &split,
                    want_train,
                    |i| kr_trailing_n(kr, i, nn),
                );
                let nonover = evaluate(
                    name,
                    &format!("mom_N{nn}_M{m}"),
                    kr,
                    m,
                    m,
                    &split,
                    want_train,
                    |i| kr_trailing_n(kr, i, nn),
                );
                if let (Some(o), Some(no)) = (overlap, nonover) {
                    println!(
                        "  {name:<14} [{win_name}] mom_N{nn} M={m} LONG: step=1 t={:+.2} (n={}) vs step={m} t={:+.2} (n={}) — overlap inflates",
                        o.edge_l.t, o.edge_l.n, no.edge_l.t, no.edge_l.n
                    );
                }
            }
        }
    }

    // --- VERDICT ---
    println!("\n========================= VERDICT =========================");
    println!("Data: KR [{earliest_reached}..{latest_reached}], US basket [{ub0}..{ub1}], cost={cost_bps}bps round-trip.");
    if !train_reaches_pre2024 {
        println!(
            "⚠ NO real OOS train window (earliest {earliest_reached} >= split {split}). \
             Whole sample is the 2024-26 supercycle uptrend."
        );
        println!(
            "  In an uptrend, momentum-long edge_gross is ~mechanically positive (drift). Without a \
             non-uptrend train window to falsify it, ANY positive edge here is INDISTINGUISHABLE from \
             drift-riding. This is NOT evidence of alpha."
        );
    } else {
        println!(
            "Real train/test split available. Honest test: a signal is alpha ONLY if edge_net%>0 \
             (marked '+') in BOTH the TRAIN and TEST windows with consistent sign. A signal positive \
             only in TEST (2024-26 uptrend) and flat/negative in TRAIN is DRIFT-RIDING, not alpha."
        );
    }
    println!("\nHonest caveats (read before acting):");
    println!("  - Supercycle uptrend → momentum-long 'works' trivially via drift. Only edge-over-baseline");
    println!("    AND train-window survival count. edge_net = edge_gross − cost (cost as HURDLE; B&H pays");
    println!("    ~0/period, active pays every trade — conservative/worst-case charge).");
    println!("  - 삼전·하닉 are mega-caps & efficient; daily close-to-close edge likely small/null after cost.");
    println!("  - US-semi single-day link lives in the open GAP (per leadlag, null residual). This tests");
    println!("    whether the multi-day US TREND adds beyond that single-day gap — expect ~null.");
    println!("  - reversion-LONG (RLONG, signal #2) IS executable (plain long the down day) — judged on its own");
    println!("    edge_net. CSHORT is the sign-flipped continuation-short on the same days; the two are not");
    println!("    independent evidence, so don't double-count, but RLONG's hurdle is real (cost charged once).");
    println!("  - CSHORT legs flagged: KR short-selling was banned until ~2025-03-31 for these — most of the");
    println!("    sample's short pnl is UNEXECUTABLE. Treat continuation-short edge as theoretical only.");
    println!("  - t inflated by overlapping windows + correlated days. Headline t uses step=M (non-overlap);");
    println!("    effective N = #non-overlapping periods. See overlap diagnostic above for the inflation.");
    println!("  - stck_clpr adjusted=true requested; mega-cap splits rare in window (삼전 50:1 was 2018).");

    Ok(())
}
