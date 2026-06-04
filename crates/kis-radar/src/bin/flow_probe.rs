//! 1시간 관찰 — 삼전/하닉 오늘 1분봉(가격/거래대금) + 외국인 가집계 전체행(시간대 궤적).
//! 가집계 TR이 시간대 버킷을 주는지 확인용 + 장중 흐름 눈으로 보기. 읽기전용.
//!
//! 환경: KIS_*. FLOW_DATE(YYYYMMDD, 기본 오늘 KST), FLOW_CODES(기본 005930,000660).

use chrono::{Datelike, Utc};
use kis_adapter::{KisClient, KisConfig, Market};

type Dyn = Box<dyn std::error::Error>;

fn num(s: &str) -> f64 {
    s.chars().filter(|c| *c != ',' && !c.is_whitespace()).collect::<String>().parse().unwrap_or(0.0)
}

fn name_of(code: &str) -> &str {
    match code {
        "005930" => "삼성전자",
        "000660" => "SK하이닉스",
        other => other,
    }
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Dyn> {
    let date = std::env::var("FLOW_DATE").unwrap_or_else(|_| {
        let k = Utc::now() + chrono::Duration::hours(9);
        format!("{:04}{:02}{:02}", k.year(), k.month(), k.day())
    });
    let codes: Vec<String> = std::env::var("FLOW_CODES")
        .unwrap_or_else(|_| "005930,000660".into())
        .split(',').map(|s| s.trim().to_string()).collect();

    let kis = KisClient::new(KisConfig::from_env()?)?;
    let ds = kis.domestic_stock();
    println!("=== FLOW PROBE {date} ===");

    for code in &codes {
        println!("\n[{}({code})]", name_of(code));

        // 1) 외국인/기관 가집계 — 전체 행(시간대 버킷이면 궤적).
        match ds.investor_trend_estimate(code).await {
            Ok(rows) => {
                println!("  가집계 행 {}개:", rows.len());
                for r in &rows {
                    println!(
                        "    시간대 {:<6} 외국인 {:>12} 기관 {:>12} 합산 {:>12}",
                        r.bsop_hour_gb, r.frgn_fake_ntby_qty, r.orgn_fake_ntby_qty, r.sum_fake_ntby_qty
                    );
                }
            }
            Err(e) => println!("  가집계 조회 실패: {e}"),
        }

        // 2) 오늘 1분봉 — 09:00~now, 가격/거래량 요약 + 30분 간격 샘플.
        match ds.minute_chart_by_date(code, &date, "153000", true, Market::Krx).await {
            Ok((_s, candles)) if !candles.is_empty() => {
                // candles는 보통 최신순; 시간순 정렬.
                let mut cs: Vec<_> = candles.iter().collect();
                cs.sort_by_key(|c| c.stck_cntg_hour.clone());
                let first = cs.first().unwrap();
                let last = cs.last().unwrap();
                let o = num(&first.stck_prpr);
                let c = num(&last.stck_prpr);
                let hi = cs.iter().map(|x| num(&x.stck_hgpr)).fold(0.0_f64, f64::max);
                let lo = cs.iter().map(|x| num(&x.stck_lwpr)).filter(|v| *v > 0.0).fold(f64::MAX, f64::min);
                let pct = if o > 0.0 { (c / o - 1.0) * 100.0 } else { 0.0 };
                println!(
                    "  1분봉 {}~{} ({}봉): 시작 {o:.0} → 현재 {c:.0} ({pct:+.2}%)  H {hi:.0} L {lo:.0}",
                    first.stck_cntg_hour, last.stck_cntg_hour, cs.len()
                );
                // 30분 간격 샘플(누적거래대금 포함).
                for w in cs.iter().step_by(30) {
                    println!(
                        "    {} 가 {:>8} 누적거래대금 {:>14}",
                        w.stck_cntg_hour, w.stck_prpr, w.acml_tr_pbmn
                    );
                }
            }
            Ok(_) => println!("  1분봉 없음(휴장/장외?)"),
            Err(e) => println!("  1분봉 조회 실패: {e}"),
        }
    }
    Ok(())
}
