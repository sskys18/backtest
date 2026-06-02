//! 기간 수익률 상위 — "삼성/하이닉스 외에 뭐가 올랐나" + 실제 breadth.
//!
//! 두 날짜의 전종목 종가 diff로 기간 수익률 계산, 상승 비율(breadth)과
//! 상위 상승 종목(테마 눈으로 식별)을 출력. 읽기전용 KRX 2콜.
//!
//! 사용: `GAIN_START=20260508 GAIN_END=20260602 GAIN_MKT=STK GAIN_TOPN=30 \
//!        cargo run -p kis-radar --features edgecheck --bin gainers`

use std::collections::HashMap;

use kis_adapter::kis::external::KrxClient;

fn num(s: &str) -> f64 {
    s.chars()
        .filter(|c| *c != ',' && !c.is_whitespace())
        .collect::<String>()
        .parse()
        .unwrap_or(0.0)
}

fn env_or(k: &str, d: &str) -> String {
    std::env::var(k).unwrap_or_else(|_| d.to_string())
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let start = env_or("GAIN_START", "20260508");
    let end = env_or("GAIN_END", "20260602");
    let mkt = env_or("GAIN_MKT", "STK");
    let topn: usize = env_or("GAIN_TOPN", "30").parse().unwrap_or(30);
    let min_tv: f64 = env_or("GAIN_MIN_TV", "500000000").parse().unwrap_or(5e8);

    let krx = KrxClient::from_env().map_err(|e| format!("KRX_ID/KRX_PW: {e}"))?;
    let a = krx.all_stock_ohlcv(&start, &mkt).await?;
    let b = krx.all_stock_ohlcv(&end, &mkt).await?;
    if a.is_empty() || b.is_empty() {
        return Err("빈 응답 — 날짜(거래일?)/시장 확인".into());
    }

    let start_px: HashMap<&str, f64> =
        a.iter().map(|r| (r.code.as_str(), num(&r.close))).collect();

    // 종료일 기준: 거래대금 필터(유동성) + 기간 수익률.
    let mut gainers: Vec<(String, String, f64, f64)> = Vec::new(); // code,name,ret,end_tv
    let mut up = 0usize;
    let mut total = 0usize;
    for r in &b {
        let p1 = num(&r.close);
        let tv = num(&r.trade_value);
        let Some(&p0) = start_px.get(r.code.as_str()) else {
            continue;
        };
        if p0 <= 0.0 || p1 <= 0.0 {
            continue;
        }
        let ret = p1 / p0 - 1.0;
        total += 1;
        if ret > 0.0 {
            up += 1;
        }
        if tv >= min_tv {
            gainers.push((r.code.clone(), r.name.clone(), ret, tv));
        }
    }
    gainers.sort_by(|x, y| y.2.total_cmp(&x.2));

    println!("=== {start} → {end} {mkt} | 기간 수익률 ===");
    println!(
        "breadth: {}/{} 상승 ({:.0}%) | 유동종목(거래대금≥{:.0}억) {} 개",
        up,
        total,
        if total > 0 { up as f64 / total as f64 * 100.0 } else { 0.0 },
        min_tv / 1e8,
        gainers.len()
    );
    println!("\ntop {topn} 상승 (유동종목):");
    println!("{:>4}  {:<14} {:>8}  {:>10}", "#", "종목", "수익%", "거래대금(억)");
    for (i, (code, name, ret, tv)) in gainers.iter().take(topn).enumerate() {
        println!(
            "{:>4}  {:<14} {:>7.1}%  {:>10.0}",
            i + 1,
            format!("{name}({code})"),
            ret * 100.0,
            tv / 1e8
        );
    }
    Ok(())
}
