//! 하루치 인트라데이 순환매 진단 + velocity forward-return 검증.
//!
//! 특정일들 1분봉 재구성 → (1) 시간대별 top-share 타임라인(순환매 존재 진단),
//! (2) share-velocity 엔진이 고른 리더가 forward H분 동안 유니버스를 아웃퍼폼하는지
//! 부호검정(파라미터 피팅 없음). 통계적 검증은 일수가 많아야 의미 있음.
//!
//! 수급(외국인/기관)은 분봉에 없음 → 거래대금-rotation만. 읽기전용.
//!
//! 사용: `INTRADAY_DATES=20260526,..,20260602 INTRADAY_MKT=KSQ INTRADAY_TOPN=20 \
//!        cargo run -p kis-radar --features edgecheck --bin intraday_check`

use std::collections::{BTreeMap, HashMap};

use kis_adapter::kis::domestic_stock::MinuteCandle;
use kis_adapter::kis::external::KrxClient;
use kis_adapter::{KisClient, KisConfig, Market};
use kis_radar::{NameSnap, RotationConfig, RotationEngine, RotationSnapshot};

/// code → (종목명, 분(자정기준) → (누적거래대금, 가격)).
type Series = HashMap<String, (String, BTreeMap<u32, (f64, f64)>)>;

fn num(s: &str) -> f64 {
    s.chars()
        .filter(|c| *c != ',' && !c.is_whitespace())
        .collect::<String>()
        .parse()
        .unwrap_or(0.0)
}

/// "133000" → 810 (자정 기준 분). +H 산술이 시(hour) 경계에서 정확하도록.
fn mins(hhmmss: &str) -> u32 {
    let v: u32 = hhmmss.parse().unwrap_or(0);
    (v / 10000) * 60 + (v / 100) % 100
}

/// 810 → "13:30".
fn fmt_hm(m: u32) -> String {
    format!("{:02}:{:02}", m / 60, m % 60)
}

/// HHMMSS 1분 감소. "100000" → "095900".
fn dec_minute(hhmmss: &str) -> String {
    let total = mins(hhmmss);
    let t = total.saturating_sub(1);
    format!("{:02}{:02}00", t / 60, t % 60)
}

/// 한 종목의 그날 전 세션 1분봉 — time을 내려가며 페이징.
async fn full_day(
    kis: &KisClient,
    code: &str,
    date: &str,
) -> Result<Vec<MinuteCandle>, Box<dyn std::error::Error>> {
    let mut all: BTreeMap<String, MinuteCandle> = BTreeMap::new();
    let mut time = "153000".to_string();
    for _ in 0..10 {
        let before = all.len();
        let (_summary, candles) = kis
            .domestic_stock()
            .minute_chart_by_date(code, date, &time, true, Market::Krx)
            .await?;
        if candles.is_empty() {
            break;
        }
        let mut earliest = u32::MAX;
        for c in candles {
            let t: u32 = c.stck_cntg_hour.parse().unwrap_or(0);
            earliest = earliest.min(t);
            all.insert(c.stck_cntg_hour.clone(), c);
        }
        if earliest <= 90100 || all.len() == before {
            break;
        }
        time = dec_minute(&format!("{earliest:06}"));
    }
    Ok(all.into_values().collect())
}

/// 그날 거래대금 상위 N 종목의 분봉 시계열 수집.
async fn build_series(
    kis: &KisClient,
    krx: &KrxClient,
    date: &str,
    mkt: &str,
    topn: usize,
    codes: Option<&std::collections::HashSet<String>>,
) -> Result<Series, Box<dyn std::error::Error>> {
    let mut rows = krx.all_stock_ohlcv(date, mkt).await?;
    if rows.is_empty() {
        return Ok(Series::new());
    }
    match codes {
        // 명시 코호트 — 그 종목만(이름·거래대금은 전종목시세에서).
        Some(set) => rows.retain(|r| set.contains(&r.code)),
        // 기본 — 거래대금 상위 N.
        None => {
            rows.sort_by(|a, b| num(&b.trade_value).total_cmp(&num(&a.trade_value)));
            rows.truncate(topn);
        }
    }

    let mut series: Series = HashMap::new();
    for r in &rows {
        let candles = full_day(kis, &r.code, date).await.unwrap_or_default();
        let mut bt = BTreeMap::new();
        for c in &candles {
            bt.insert(mins(&c.stck_cntg_hour), (num(&c.acml_tr_pbmn), num(&c.stck_prpr)));
        }
        if !bt.is_empty() {
            series.insert(r.code.clone(), (r.name.clone(), bt));
        }
    }
    Ok(series)
}

/// checkpoint 분에서 각 종목 최신(≤cp) 누적거래대금으로 스냅샷.
fn snapshot_at(series: &Series, cp: u32, ts: i64) -> RotationSnapshot {
    let mut names = Vec::new();
    for (code, (name, bt)) in series {
        if let Some((_, (turn, _))) = bt.range(..=cp).next_back() {
            names.push(NameSnap {
                code: code.clone(),
                name: name.clone(),
                turnover: *turn,
                pct_change: 0.0,
                flow: None,
            });
        }
    }
    RotationSnapshot { ts, names }
}

/// cp 시점 가격(최신 ≤cp).
fn price_at(series: &Series, code: &str, cp: u32) -> Option<f64> {
    series
        .get(code)
        .and_then(|(_, bt)| bt.range(..=cp).next_back().map(|(_, (_, p))| *p))
        .filter(|p| *p > 0.0)
}

fn top_share(snap: &RotationSnapshot, k: usize) -> Vec<(String, String, f64)> {
    let total: f64 = snap.names.iter().map(|n| n.turnover).sum();
    let mut v: Vec<_> = snap
        .names
        .iter()
        .map(|n| {
            (
                n.code.clone(),
                n.name.clone(),
                if total > 0.0 { n.turnover / total } else { 0.0 },
            )
        })
        .collect();
    v.sort_by(|a, b| b.2.total_cmp(&a.2));
    v.truncate(k);
    v
}

/// 시간대별 top-3 share 타임라인 + churn.
fn print_timeline(date: &str, mkt: &str, topn: usize, series: &Series) {
    println!("\n=== {date} {mkt} | universe top {topn} by 거래대금 ({} 종목 수집) ===", series.len());
    let checkpoints = [600u32, 660, 720, 780, 840, 900]; // 10:00 ~ 15:00
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    for &cp in &checkpoints {
        let snap = snapshot_at(series, cp, 0);
        let line: Vec<String> = top_share(&snap, 3)
            .iter()
            .map(|(c, n, s)| {
                seen.insert(c.clone());
                format!("{n}({c}) {:.1}%", s * 100.0)
            })
            .collect();
        if !line.is_empty() {
            println!("  {}  {}", fmt_hm(cp), line.join(" | "));
        }
    }
    let churn = seen.len();
    let v = if churn >= 5 { "활발한 순환매" } else if churn >= 3 { "보통" } else { "집중" };
    println!("  churn: {churn} distinct in top-3 → {v}");
}

fn mean(xs: &[f64]) -> f64 {
    if xs.is_empty() { 0.0 } else { xs.iter().sum::<f64>() / xs.len() as f64 }
}

/// velocity 리더 forward-return 검증 — 여러 날 누적, (L,H) 분 그리드.
fn validate(all: &[(String, Series)]) {
    // 진입 lag(분): 신호는 t분 종료시 확정 → 라이브선 t+lag에 체결(추격). 0=lookahead(비현실).
    let lag: u32 = std::env::var("INTRADAY_ENTRY_LAG").ok().and_then(|s| s.parse().ok()).unwrap_or(1);
    // 왕복비용(bps): 거래세 매도 20bps(2026 코스피·코스닥) + 슬리피지. net% = lead% − cost.
    let cost_bps: f64 = std::env::var("INTRADAY_COST_BPS").ok().and_then(|s| s.parse().ok()).unwrap_or(50.0);
    println!("\n=== velocity-leader forward-return (across {} days, entry_lag={lag}min, cost={cost_bps}bps) ===", all.len());
    println!(
        "{:>4} {:>4} {:>6} {:>9} {:>9} {:>9} {:>9} {:>7} {:>6}",
        "L", "H", "n", "lead%", "net%", "univ%", "excess%", "t", "hit%"
    );
    let cfg = RotationConfig::default();
    for l in [20u32, 30, 60] {
        for h in [15u32, 30, 60] {
            let mut excess = Vec::new();
            let mut lead_acc = Vec::new();
            let mut univ_acc = Vec::new();
            for (_date, series) in all {
                // 세션 0900(540)~1530(930). 결정점 step 10.
                let mut t = 540 + l;
                // 진입 t+lag, 청산 t+lag+h. lookahead 제거(신호는 t에 확정, 체결은 t+lag).
                while t + lag + h <= 930 {
                    let base = snapshot_at(series, t - l, 0);
                    let now = snapshot_at(series, t, 1);
                    let (ent, exit) = (t + lag, t + lag + h);
                    if let Ok(rep) = RotationEngine::analyze(&[base, now.clone()], cfg) {
                        let leaders: Vec<&str> =
                            rep.leaders.iter().take(5).map(|x| x.code.as_str()).collect();
                        if !leaders.is_empty() {
                            let lret: Vec<f64> = leaders
                                .iter()
                                .filter_map(|c| {
                                    Some(price_at(series, c, exit)? / price_at(series, c, ent)? - 1.0)
                                })
                                .collect();
                            let uret: Vec<f64> = now
                                .names
                                .iter()
                                .filter_map(|n| {
                                    Some(
                                        price_at(series, &n.code, exit)?
                                            / price_at(series, &n.code, ent)?
                                            - 1.0,
                                    )
                                })
                                .collect();
                            if !lret.is_empty() && !uret.is_empty() {
                                lead_acc.push(mean(&lret));
                                univ_acc.push(mean(&uret));
                                excess.push(mean(&lret) - mean(&uret));
                            }
                        }
                    }
                    t += 10;
                }
            }
            let n = excess.len();
            if n == 0 {
                continue;
            }
            let m = mean(&excess);
            let sd = if n > 1 {
                (excess.iter().map(|x| (x - m).powi(2)).sum::<f64>() / (n as f64 - 1.0)).sqrt()
            } else {
                0.0
            };
            let t_stat = if sd > 0.0 { m / (sd / (n as f64).sqrt()) } else { 0.0 };
            let hit = excess.iter().filter(|x| **x > 0.0).count() as f64 / n as f64;
            let lead_pct = mean(&lead_acc) * 100.0;
            let net_pct = lead_pct - cost_bps / 100.0;
            println!(
                "{:>4} {:>4} {:>6} {:>9.3} {:>9.3} {:>9.3} {:>9.3} {:>7.2} {:>6.1}",
                l, h, n,
                lead_pct, net_pct, mean(&univ_acc) * 100.0, m * 100.0,
                t_stat, hit * 100.0
            );
        }
    }
    println!("\nexcess>0 & t>~2 across most cells = velocity 리더가 forward 아웃퍼폼(선행 신호).\n\
              flat/음수 = 성호전자는 운. n 작으면 일수 늘려 재검정.");
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let dates = std::env::var("INTRADAY_DATES").unwrap_or_else(|_| "20260601,20260602".to_string());
    let mkt = std::env::var("INTRADAY_MKT").unwrap_or_else(|_| "STK".to_string());
    let topn: usize = std::env::var("INTRADAY_TOPN")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(20);
    // 명시 코호트 — 설정시 거래대금 top-N 대신 이 종목들로 유니버스 고정.
    let codes: Option<std::collections::HashSet<String>> = std::env::var("INTRADAY_CODES")
        .ok()
        .map(|s| s.split(',').map(|c| c.trim().to_string()).collect());

    let kis = KisClient::new(KisConfig::from_env()?)?;
    let krx = KrxClient::from_env().map_err(|e| format!("KRX_ID/KRX_PW: {e}"))?;

    let mut all = Vec::new();
    for date in dates.split(',') {
        let date = date.trim().to_string();
        match build_series(&kis, &krx, &date, &mkt, topn, codes.as_ref()).await {
            Ok(series) if series.len() >= 3 => {
                print_timeline(&date, &mkt, topn, &series);
                all.push((date, series));
            }
            Ok(_) => eprintln!("{date}: 분봉 부족/휴장 — skip"),
            Err(e) => eprintln!("{date}: error — {e}"),
        }
    }
    if !all.is_empty() {
        validate(&all);
    }
    Ok(())
}
