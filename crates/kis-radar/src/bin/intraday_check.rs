//! 하루치 인트라데이 순환매 진단 — 특정일 1분봉 재구성.
//!
//! "어제/오늘 장중에 순환매가 실제로 있었나 + 거래대금 share-velocity가 무빙을 잡나"를
//! 하루 단위로 눈으로 확인. 통계적 edge가 아니라 진단(diagnostic).
//!
//! 흐름: KRX 전종목시세(그날 거래대금 상위 N = 활성 종목) → 각 종목 KIS 일별분봉
//! 전 세션 페이징 → 분별 누적거래대금 패널 → 시간대별 top share + 엔진 share-velocity.
//!
//! 수급(외국인/기관)은 분봉에 없음 → 거래대금-rotation만. 읽기전용(매매 불가 TR).
//!
//! 사용: `INTRADAY_DATES=20260601,20260602 INTRADAY_TOPN=20 cargo run -p kis-radar \
//!        --features edgecheck --bin intraday_check`
//! 환경: KRX_ID/KRX_PW, KIS_* 필요.

use std::collections::{BTreeMap, HashMap};

use kis_adapter::domestic_stock::MinuteCandle;
use kis_adapter::external::KrxClient;
use kis_adapter::{KisClient, KisConfig, Market};
use kis_radar::{NameSnap, RotationConfig, RotationEngine, RotationSnapshot};

/// code → (종목명, HHMM → (누적거래대금, 가격)).
type Series = HashMap<String, (String, BTreeMap<u32, (f64, f64)>)>;

fn num(s: &str) -> f64 {
    s.chars()
        .filter(|c| *c != ',' && !c.is_whitespace())
        .collect::<String>()
        .parse()
        .unwrap_or(0.0)
}

/// "133000" → 1330 (HHMM).
fn hhmm(s: &str) -> u32 {
    s.parse::<u32>().unwrap_or(0) / 100
}

/// HHMMSS 1분 감소. "100000" → "095900".
fn dec_minute(hhmmss: &str) -> String {
    let v: u32 = hhmmss.parse().unwrap_or(90000);
    let total = (v / 10000) * 60 + (v / 100) % 100;
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
        // 09:00 이전 도달 or 새 봉 없음(진전 없음) → 종료.
        if earliest <= 90100 || all.len() == before {
            break;
        }
        time = dec_minute(&format!("{earliest:06}"));
    }
    Ok(all.into_values().collect())
}

/// checkpoint 시각(HHMM)에서 각 종목의 최신(≤cp) 누적거래대금·가격으로 스냅샷.
fn snapshot_at(series: &Series, cp: u32, ts: i64) -> RotationSnapshot {
    let mut names = Vec::new();
    for (code, (name, bt)) in series {
        if let Some((_, (turn, _px))) = bt.range(..=cp).next_back() {
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

/// 스냅샷에서 share 상위 k.
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

async fn run_day(
    kis: &KisClient,
    krx: &KrxClient,
    date: &str,
    mkt: &str,
    topn: usize,
) -> Result<(), Box<dyn std::error::Error>> {
    println!("\n=== {date} {mkt} | universe top {topn} by 거래대금 ===");

    // 1) 그날 활성 종목 = 거래대금 상위 N.
    let mut rows = krx.all_stock_ohlcv(date, mkt).await?;
    if rows.is_empty() {
        println!("  (거래 데이터 없음 — 휴장일?)");
        return Ok(());
    }
    rows.sort_by(|a, b| num(&b.trade_value).total_cmp(&num(&a.trade_value)));
    rows.truncate(topn);

    // 2) 각 종목 전 세션 1분봉 → 시계열.
    let mut series: Series = HashMap::new();
    for r in &rows {
        let candles = full_day(kis, &r.code, date).await.unwrap_or_default();
        let mut bt = BTreeMap::new();
        for c in &candles {
            bt.insert(hhmm(&c.stck_cntg_hour), (num(&c.acml_tr_pbmn), num(&c.stck_prpr)));
        }
        if !bt.is_empty() {
            series.insert(r.code.clone(), (r.name.clone(), bt));
        }
    }
    eprintln!("  {} 종목 분봉 수집", series.len());
    if series.len() < 3 {
        println!("  (분봉 수집 부족 — 데이터/날짜 확인)");
        return Ok(());
    }

    // 3) 시간대별 top-3 share 타임라인 + churn.
    let checkpoints = [1000u32, 1100, 1200, 1300, 1400, 1500];
    let mut seen_top: std::collections::HashSet<String> = std::collections::HashSet::new();
    println!("  시간   top-3 누적거래대금 share");
    for (i, &cp) in checkpoints.iter().enumerate() {
        let snap = snapshot_at(&series, cp, i as i64);
        let top = top_share(&snap, 3);
        let line: Vec<String> = top
            .iter()
            .map(|(c, n, s)| {
                seen_top.insert(c.clone());
                format!("{n}({c}) {:.1}%", s * 100.0)
            })
            .collect();
        println!("  {:02}:00  {}", cp / 100, line.join(" | "));
    }

    // 4) 엔진 share-velocity (직전 1시간 윈도우) — 13:00, 15:00 시점 누가 share 가속.
    for &cp in &[1300u32, 1500] {
        let base = snapshot_at(&series, cp - 100, 0);
        let now = snapshot_at(&series, cp, 1);
        if let Ok(rep) = RotationEngine::analyze(&[base, now], RotationConfig::default()) {
            let lead: Vec<String> = rep
                .leaders
                .iter()
                .take(3)
                .map(|l| format!("{}({}) +{:.2}%p", l.name, l.code, l.share_delta * 100.0))
                .collect();
            println!("  share-velocity {:02}:00 (직전1h): {}", cp / 100, lead.join(" | "));
        }
    }

    // 5) churn = top-3에 등장한 distinct 종목 수.
    let churn = seen_top.len();
    let verdict = if churn >= 5 {
        "활발한 순환매"
    } else if churn >= 3 {
        "보통"
    } else {
        "집중(순환매 약)"
    };
    println!("  rotation churn: 6개 체크포인트 top-3에 {churn}개 distinct → {verdict}");
    Ok(())
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let dates = std::env::var("INTRADAY_DATES").unwrap_or_else(|_| "20260601,20260602".to_string());
    let mkt = std::env::var("INTRADAY_MKT").unwrap_or_else(|_| "STK".to_string());
    let topn: usize = std::env::var("INTRADAY_TOPN")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(20);

    let kis = KisClient::new(KisConfig::from_env()?)?;
    let krx = KrxClient::from_env().map_err(|e| format!("KRX_ID/KRX_PW: {e}"))?;

    for date in dates.split(',') {
        let date = date.trim();
        if let Err(e) = run_day(&kis, &krx, date, &mkt, topn).await {
            eprintln!("{date}: error — {e}");
        }
    }
    Ok(())
}
