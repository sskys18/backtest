//! 순환매 일별 횡단면 엣지 체크 — go/no-go.
//!
//! 가설: "거래대금-share가 (지난 L일 대비) 상승 + 외국인 보유 증가" 종목이
//! forward H일 동안 유니버스를 아웃퍼폼하는가?
//!
//! 방법론(advisor 제약 반영):
//! - **부호 테스트, 파라미터 피팅 없음** → OOS by construction. share_delta>0 & foreign_delta>0.
//! - 일별로 (주도주 평균 fwd수익 − 유니버스 평균)을 모아 day-series t검정(횡단면 상관 통제).
//! - (L,H) 그리드는 **최적화가 아니라 robustness 표시** — 한 칸만 빛나면 과적합 의심.
//! - 데이터: KRX 전종목 EOD(어댑터 external). pykrx 불필요.
//!
//! 한계: 외국인 보유는 EOD 공표 → D 종가 진입은 ~1일 낙관(보수판은 D+1 진입). v1은 D 진입.
//!
//! 환경변수: `KRX_ID`/`KRX_PW`(필수), `EDGE_START`/`EDGE_END`(YYYYMMDD),
//! `EDGE_MKT`(STK=코스피/KSQ=코스닥/ALL), `EDGE_MIN_TURNOVER`(원, 기본 1e9).

use std::collections::HashMap;

use chrono::{Datelike, NaiveDate, Weekday};
use kis_adapter::external::KrxClient;

/// 하루치 횡단면 데이터.
struct DayData {
    /// code → (종가, 거래대금)
    px: HashMap<String, (f64, f64)>,
    /// code → 외국인 보유수량
    fq: HashMap<String, f64>,
}

/// KRX 콤마 문자열 → f64.
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

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let start = env_or("EDGE_START", "20251201");
    let end = env_or("EDGE_END", "20260530");
    let mkt = env_or("EDGE_MKT", "STK");
    let min_turnover: f64 = env_or("EDGE_MIN_TURNOVER", "1000000000").parse().unwrap_or(1e9);

    let d0 = NaiveDate::parse_from_str(&start, "%Y%m%d")?;
    let d1 = NaiveDate::parse_from_str(&end, "%Y%m%d")?;
    eprintln!("edge check {start}..{end} mkt={mkt} min_turnover={min_turnover:.0}");

    let krx = KrxClient::from_env()
        .map_err(|e| format!("KRX_ID/KRX_PW required: {e}"))?;

    // 1) 거래일 데이터 수집. KRX는 비거래일에 직전 거래일 데이터를 반환(stale) →
    //    주말은 weekday로 skip, 공휴일은 총거래대금 동일 가드로 skip.
    let mut days: Vec<DayData> = Vec::new();
    let mut prev_turnover = -1.0_f64;
    let mut d = d0;
    while d <= d1 {
        if matches!(d.weekday(), Weekday::Sat | Weekday::Sun) {
            d = d.succ_opt().ok_or("date overflow")?;
            continue;
        }
        let ds = d.format("%Y%m%d").to_string();
        let ohlcv = krx.all_stock_ohlcv(&ds, &mkt).await?;
        if !ohlcv.is_empty() {
            let total_tv: f64 = ohlcv.iter().map(|r| num(&r.trade_value)).sum();
            // 공휴일 stale 가드 — 직전 수집일과 총거래대금이 같으면 비거래일.
            if (total_tv - prev_turnover).abs() < 1.0 {
                eprintln!("  {ds}: stale (==prev turnover) — skip");
                d = d.succ_opt().ok_or("date overflow")?;
                continue;
            }
            prev_turnover = total_tv;

            let foreign = krx.foreign_holding_all(&ds, &mkt).await.unwrap_or_default();
            let px: HashMap<String, (f64, f64)> = ohlcv
                .iter()
                .map(|r| (r.code.clone(), (num(&r.close), num(&r.trade_value))))
                .collect();
            let fq: HashMap<String, f64> = foreign
                .iter()
                .map(|r| (r.code.clone(), num(&r.foreign_qty)))
                .collect();
            eprintln!("  {ds}: {} stocks, {} foreign rows", px.len(), fq.len());
            days.push(DayData { px, fq });
        }
        d = d.succ_opt().ok_or("date overflow")?;
    }
    eprintln!("collected {} trading days\n", days.len());
    if days.len() < 20 {
        return Err("too few trading days collected — widen date range or check creds".into());
    }

    // 2) (L,H) 그리드 — robustness.
    println!(
        "{:>3} {:>3} {:>6} {:>8} {:>11} {:>11} {:>11} {:>7} {:>7}",
        "L", "H", "days", "avg_lead", "lead_ret%", "univ_ret%", "excess%", "t", "hit%"
    );
    for l in [3usize, 5, 10] {
        for h in [3usize, 5, 10] {
            let r = evaluate(&days, l, h, min_turnover);
            println!(
                "{:>3} {:>3} {:>6} {:>8.1} {:>11.3} {:>11.3} {:>11.3} {:>7.2} {:>7.1}",
                l,
                h,
                r.n_days,
                r.avg_leaders,
                r.lead_ret * 100.0,
                r.univ_ret * 100.0,
                r.excess * 100.0,
                r.t_stat,
                r.hit_rate * 100.0
            );
        }
    }
    println!(
        "\nread: excess% > 0 with t > ~2 across MOST (L,H) cells = edge exists → proceed.\n\
         one bright cell only = overfit. flat/negative = signal dead, stop here."
    );
    Ok(())
}

struct EvalResult {
    n_days: usize,
    avg_leaders: f64,
    lead_ret: f64,
    univ_ret: f64,
    excess: f64,
    t_stat: f64,
    hit_rate: f64,
}

/// 일별 횡단면 평가. 결정일 D: base=D-L, fwd=D+H.
fn evaluate(days: &[DayData], l: usize, h: usize, min_turnover: f64) -> EvalResult {
    let mut per_day_excess = Vec::new();
    let mut lead_rets = Vec::new();
    let mut univ_rets = Vec::new();
    let mut lead_counts = Vec::new();

    if days.len() <= l + h {
        return EvalResult {
            n_days: 0,
            avg_leaders: 0.0,
            lead_ret: 0.0,
            univ_ret: 0.0,
            excess: 0.0,
            t_stat: 0.0,
            hit_rate: 0.0,
        };
    }

    for i in l..(days.len() - h) {
        let dd = &days[i];
        let base = &days[i - l];
        let fwd = &days[i + h];

        // 유니버스: D에 거래대금 ≥ 임계 & base/fwd 가격 존재.
        let total_d: f64 = dd
            .px
            .iter()
            .filter(|(_, (_, tv))| *tv >= min_turnover)
            .map(|(_, (_, tv))| *tv)
            .sum();
        let total_base: f64 = base.px.values().map(|(_, tv)| *tv).sum();
        if total_d <= 0.0 || total_base <= 0.0 {
            continue;
        }

        let mut day_lead = Vec::new();
        let mut day_univ = Vec::new();

        for (code, (close_d, tv_d)) in &dd.px {
            if *tv_d < min_turnover || *close_d <= 0.0 {
                continue;
            }
            let Some((close_f, _)) = fwd.px.get(code) else {
                continue;
            };
            if *close_f <= 0.0 {
                continue;
            }
            let fwd_ret = close_f / close_d - 1.0;
            day_univ.push(fwd_ret);

            // 신호: 거래대금-share 상승 + 외국인 보유 증가.
            let share_d = tv_d / total_d;
            let share_base = base.px.get(code).map(|(_, tv)| tv / total_base).unwrap_or(0.0);
            let share_delta = share_d - share_base;

            let fq_d = dd.fq.get(code).copied();
            let fq_base = base.fq.get(code).copied();
            let foreign_up = matches!((fq_d, fq_base), (Some(a), Some(b)) if a - b > 0.0);

            if share_delta > 0.0 && foreign_up {
                day_lead.push(fwd_ret);
            }
        }

        if day_lead.is_empty() || day_univ.is_empty() {
            continue;
        }
        let lead_mean = mean(&day_lead);
        let univ_mean = mean(&day_univ);
        per_day_excess.push(lead_mean - univ_mean);
        lead_rets.push(lead_mean);
        univ_rets.push(univ_mean);
        lead_counts.push(day_lead.len() as f64);
    }

    let n = per_day_excess.len();
    if n == 0 {
        return EvalResult {
            n_days: 0,
            avg_leaders: 0.0,
            lead_ret: 0.0,
            univ_ret: 0.0,
            excess: 0.0,
            t_stat: 0.0,
            hit_rate: 0.0,
        };
    }
    let excess = mean(&per_day_excess);
    let sd = std(&per_day_excess, excess);
    let t_stat = if sd > 0.0 {
        excess / (sd / (n as f64).sqrt())
    } else {
        0.0
    };
    let hit_rate = per_day_excess.iter().filter(|x| **x > 0.0).count() as f64 / n as f64;

    EvalResult {
        n_days: n,
        avg_leaders: mean(&lead_counts),
        lead_ret: mean(&lead_rets),
        univ_ret: mean(&univ_rets),
        excess,
        t_stat,
        hit_rate,
    }
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
