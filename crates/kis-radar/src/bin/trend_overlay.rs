//! 추세 오버레이 vs 매수보유 — 타이밍이 그냥 보유를 이기는가? (사전등록 검증)
//!
//! 단일 자산 일봉. close>SMA(N) → 풀투자, 아니면 현금. vs 매수보유. OOS train/test 분리.
//! 슈퍼사이클 레짐에서 "돈은 포지셔널 베타" 결론을 직접 시험. 읽기전용.
//!
//! 사용: TREND_CODES=069500,005930,000660 TREND_START=20210101 TREND_END=20260602 \
//!       TREND_COST_BPS=35 cargo run -p kis-radar --features edgecheck --bin trend_overlay

use kis_adapter::kis::domestic_stock::{Period, PeriodCandle};
use kis_adapter::{KisClient, KisConfig, Market};

fn num(s: &str) -> f64 {
    s.chars().filter(|c| *c != ',' && !c.is_whitespace()).collect::<String>().parse().unwrap_or(0.0)
}

/// YYYYMMDD + n일. 단순 calendar 산술용(거래일 아님) — chunk 경계 이동에만 사용.
fn add_days(yyyymmdd: &str, days: i64) -> String {
    // 간이 변환: 윤년 대충 무시하고 epoch 근사 대신 chrono 사용.
    use std::str::FromStr;
    let y = i32::from_str(&yyyymmdd[0..4]).unwrap();
    let m = u32::from_str(&yyyymmdd[4..6]).unwrap();
    let d = u32::from_str(&yyyymmdd[6..8]).unwrap();
    let date = chrono::NaiveDate::from_ymd_opt(y, m, d).unwrap() + chrono::Duration::days(days);
    date.format("%Y%m%d").to_string()
}

/// 전 구간 일봉 — period_price 호출당 100건 → start를 앞으로 밀며 페이징.
async fn full_daily(
    kis: &KisClient,
    code: &str,
    start: &str,
    end: &str,
) -> Result<Vec<(String, f64)>, Box<dyn std::error::Error>> {
    use std::collections::BTreeMap;
    let mut out: BTreeMap<String, f64> = BTreeMap::new();
    let mut cur = start.to_string();
    for _ in 0..40 {
        if cur.as_str() > end {
            break;
        }
        // ~100거래일 ≈ 140 calendar days chunk.
        let chunk_end = {
            let e = add_days(&cur, 140);
            if e.as_str() > end { end.to_string() } else { e }
        };
        let (_s, candles): (_, Vec<PeriodCandle>) = kis
            .domestic_stock()
            .period_price(code, &cur, &chunk_end, Period::Daily, true, Market::Krx)
            .await?;
        let before = out.len();
        for c in &candles {
            if !c.stck_bsop_date.is_empty() {
                out.insert(c.stck_bsop_date.clone(), num(&c.stck_clpr));
            }
        }
        if chunk_end.as_str() >= end && out.len() == before {
            break;
        }
        cur = add_days(&chunk_end, 1);
    }
    Ok(out.into_iter().filter(|(_, p)| *p > 0.0).collect())
}

#[derive(Default)]
struct Stats {
    total_ret: f64,
    cagr: f64,
    sharpe: f64,
    max_dd: f64,
    exposure: f64,
    trades: usize,
    worst_year: (String, f64), // (year, max_dd_in_year)
}

/// 일별 수익 시계열 → 지표. ret[i] = 그날 전략 수익률(현금이면 0).
fn stats(dates: &[String], rets: &[f64], invested: &[bool]) -> Stats {
    let n = rets.len();
    if n == 0 {
        return Stats::default();
    }
    let mut eq = 1.0f64;
    let mut peak = 1.0f64;
    let mut max_dd = 0.0f64;
    // 연도별 DD
    use std::collections::BTreeMap;
    let mut year_eq: BTreeMap<String, (f64, f64, f64)> = BTreeMap::new(); // year -> (eq, peak, maxdd)
    for i in 0..n {
        eq *= 1.0 + rets[i];
        peak = peak.max(eq);
        let dd = eq / peak - 1.0;
        max_dd = max_dd.min(dd);
        let yr = dates[i][0..4].to_string();
        let e = year_eq.entry(yr).or_insert((1.0f64, 1.0f64, 0.0f64));
        e.0 *= 1.0 + rets[i];
        e.1 = e.1.max(e.0);
        e.2 = e.2.min(e.0 / e.1 - 1.0);
    }
    let mean = rets.iter().sum::<f64>() / n as f64;
    let var = rets.iter().map(|r| (r - mean).powi(2)).sum::<f64>() / (n as f64 - 1.0).max(1.0);
    let sd = var.sqrt();
    let sharpe = if sd > 0.0 { mean / sd * (252f64).sqrt() } else { 0.0 };
    let years = n as f64 / 252.0;
    let cagr = if years > 0.0 && eq > 0.0 { eq.powf(1.0 / years) - 1.0 } else { 0.0 };
    let exposure = invested.iter().filter(|x| **x).count() as f64 / n as f64;
    let mut trades = 0;
    for i in 1..n {
        if invested[i] != invested[i - 1] {
            trades += 1;
        }
    }
    let worst = year_eq.iter().min_by(|a, b| a.1 .2.total_cmp(&b.1 .2)).map(|(y, v)| (y.clone(), v.2)).unwrap_or_default();
    Stats { total_ret: eq - 1.0, cagr, sharpe, max_dd, exposure, trades, worst_year: worst }
}

/// SMA(N) 오버레이 + 매수보유 수익 시계열 생성. cost_bps = 전환당 왕복비용.
fn run_overlay(closes: &[f64], n: usize, cost: f64) -> (Vec<f64>, Vec<bool>, Vec<f64>) {
    let len = closes.len();
    let mut strat = vec![0.0; len];
    let mut invested = vec![false; len];
    let mut hold = vec![0.0; len];
    for i in 1..len {
        hold[i] = closes[i] / closes[i - 1] - 1.0;
        // 어제(i-1) 종가가 어제까지 N일 SMA 위면 오늘(i) 풀투자. 신호는 i-1까지만 사용 → 룩어헤드 없음.
        if i >= n {
            let sma = closes[i - n..i].iter().sum::<f64>() / n as f64; // closes[i-n..i] = i-1까지 N개
            invested[i] = closes[i - 1] > sma;
        }
    }
    for i in 1..len {
        strat[i] = if invested[i] { hold[i] } else { 0.0 };
        if invested[i] != invested[i - 1] {
            strat[i] -= cost / 10000.0; // 전환 비용
        }
    }
    (strat, invested, hold)
}

/// 전략별 (라벨, 일별수익, 투자여부) — 전체 시계열에서 1회 계산. SMA warmup은 진짜 시작(2021)에만.
struct StratSeries {
    label: String,
    strat: Vec<f64>,
    invested: Vec<bool>,
}

/// 매수보유 + SMA{20,50,200} 오버레이를 **전체 closes**에서 계산. 슬라이스는 stats에서만.
fn build_strats(closes: &[f64], cost: f64) -> Vec<StratSeries> {
    let len = closes.len();
    let hold: Vec<f64> = (0..len).map(|i| if i == 0 { 0.0 } else { closes[i] / closes[i - 1] - 1.0 }).collect();
    let mut out = vec![StratSeries { label: "buy&hold".into(), strat: hold, invested: vec![true; len] }];
    for n in [20usize, 50, 200] {
        let (strat, invested, _) = run_overlay(closes, n, cost);
        out.push(StratSeries { label: format!("SMA{n}"), strat, invested });
    }
    out
}

/// [a,b] 범위만 슬라이스해 지표 출력. 신호(invested)는 전체 기준 — warmup 경계 재시작 없음.
fn report_range(title: &str, dates: &[String], strats: &[StratSeries], a: usize, b: usize) {
    println!("\n##### {title}  ({} bars, {}~{}) #####", b - a + 1, &dates[a], &dates[b]);
    println!("{:>14} {:>9} {:>8} {:>8} {:>8} {:>8} {:>6} {:>14}", "strat", "totRet%", "CAGR%", "Sharpe", "MaxDD%", "expo%", "trades", "worstYr(DD%)");
    for ss in strats {
        let s = stats(&dates[a..=b], &ss.strat[a..=b], &ss.invested[a..=b]);
        println!("{:>14} {:>9.1} {:>8.1} {:>8.2} {:>8.1} {:>8.0} {:>6} {:>8}({:>5.1})", ss.label, s.total_ret * 100.0, s.cagr * 100.0, s.sharpe, s.max_dd * 100.0, s.exposure * 100.0, s.trades, s.worst_year.0, s.worst_year.1 * 100.0);
    }
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let codes: Vec<String> = std::env::var("TREND_CODES").unwrap_or_else(|_| "069500,005930,000660".into()).split(',').map(|s| s.trim().to_string()).collect();
    let start = std::env::var("TREND_START").unwrap_or_else(|_| "20210101".into());
    let end = std::env::var("TREND_END").unwrap_or_else(|_| "20260602".into());
    let cost: f64 = std::env::var("TREND_COST_BPS").ok().and_then(|s| s.parse().ok()).unwrap_or(35.0);
    // OOS 경계: 이 날짜 미만 = train, 이상 = test.
    let split = std::env::var("TREND_SPLIT").unwrap_or_else(|_| "20240101".into());

    let kis = KisClient::new(KisConfig::from_env()?)?;
    println!("추세 오버레이 vs 매수보유 | cost={cost}bps | split={split} | {start}~{end}");
    for code in &codes {
        let series = match full_daily(&kis, code, &start, &end).await {
            Ok(s) if s.len() > 220 => s,
            Ok(s) => { eprintln!("{code}: bars 부족({}) — skip", s.len()); continue; }
            Err(e) => { eprintln!("{code}: error — {e}"); continue; }
        };
        let dates: Vec<String> = series.iter().map(|(d, _)| d.clone()).collect();
        let closes: Vec<f64> = series.iter().map(|(_, p)| *p).collect();
        // 오버레이를 전체 시계열에서 1회 계산 → SMA warmup은 진짜 시작(2021)에만, 슬라이스 경계 재시작 없음.
        let strats = build_strats(&closes, cost);
        let tr: Vec<usize> = (0..dates.len()).filter(|&i| dates[i].as_str() < split.as_str()).collect();
        let te: Vec<usize> = (0..dates.len()).filter(|&i| dates[i].as_str() >= split.as_str()).collect();
        report_range(&format!("{code} FULL"), &dates, &strats, 0, dates.len() - 1);
        if let (Some(&a), Some(&b)) = (tr.first(), tr.last()) {
            report_range(&format!("{code} TRAIN"), &dates, &strats, a, b);
        }
        if let (Some(&a), Some(&b)) = (te.first(), te.last()) {
            report_range(&format!("{code} TEST"), &dates, &strats, a, b);
        }
    }
    Ok(())
}
