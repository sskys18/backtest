//! KR TECH DRIVER DASHBOARD — 삼성전자(005930) + SK하이닉스(000660).
//!
//! 의사결정 보조 도구(decision-support). 매수/매도 신호 생성기가 *아니다*.
//! 이 세션 전체가 추출 가능한 가격/플로우 시그널이 없음을 입증했다 — 정직한
//! 결론은 엣지가 *판단·논리(thesis)* 에 있다는 것. 이 대시보드는 사람이 직접
//! 판단하도록 **드라이버를 한 화면에** 펼친다. "BUY"/"SELL" 출력 없음 —
//! 사실(부호·크기 라벨링)만.
//!
//! 패널:
//!   [KRX SPOT]     현재가·등락·거래대금 + 외국인/기관 가집계(장중 추정 수급)
//!   [BINANCE PERP] 마크가·펀딩(연율 환산)·24h 거래대금(유동성)·호가 잔량
//!   [GLOBAL SEMIS] NVDA·MU·AVGO·TSM 전일 종가 대비 등락
//!   [CONTEXT]      외국인 보유율(KRX 현재가에 동봉)
//!
//! 설계 원칙: **패널별 에러 격리**. Binance KR-perp는 2026-06-02 상장(데모
//! 당일) — 24h 거래량이 미미하거나 호가가 비어 있을 수 있고, 콜이 실패할 수도
//! 있다. 한 패널의 실패가 화면 전체를 죽이면 안 된다. 각 fetch를 개별적으로
//! 처리하고 실패 시 inline `n/a`/에러로 표기한다.
//!
//! 단위 주의: Binance KR-perp `mark_price`는 USD 표기로 추정(예제의 지정가
//! "65.00" vs 삼전 ≈75,000원). KRX 현물은 KRW. FX 소스가 없으므로 basis%는
//! 출력하지 않는다 — raw 값 + "unit-unverified" 라벨만.
//!
//! 실행:
//!   cargo run -q -p kis-radar --features edgecheck --bin dashboard
//! 옵션: `DASH_WATCH=<secs>` → N초마다 재출력(루프). 기본은 one-shot.

use kis_adapter::binance::{BinanceClient, BinanceConfig, SAMSUNG, SK_HYNIX};
use kis_adapter::kis::overseas_stock::OverseasExchange;
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

/// 원 → 억원.
fn eok(won: f64) -> f64 {
    won / 1.0e8
}

type Dyn = Box<dyn std::error::Error>;

/// KRX 종목 1줄: 현재가·등락·거래대금 + 외국인/기관 가집계.
async fn krx_line(kis: &KisClient, name: &str, code: &str) -> String {
    // 현재가 스냅샷.
    let cp = match kis.domestic_stock().current_price(code, Market::Krx).await {
        Ok(c) => c,
        Err(e) => return format!("  {name:<9} 현재가 n/a (KIS 에러: {e})"),
    };
    let prpr = num(&cp.stck_prpr);
    // prdy_ctrt(등락율)는 부호 포함 문자열(예 "-0.13"). 크기는 abs로 취하고
    // 부호는 prdy_vrss_sign으로 정규화한다(KIS: 1상한 2상승 3보합 4하한 5하락).
    let ctrt = num(&cp.prdy_ctrt).abs();
    let sign = match cp.prdy_vrss_sign.trim() {
        "1" | "2" => "+",
        "4" | "5" => "-",
        _ => {
            // 보합(3) 또는 미지정 — prdy_vrss 부호로 보강.
            if num(&cp.prdy_vrss) < 0.0 { "-" } else { "+" }
        }
    };
    let tr_eok = eok(num(&cp.acml_tr_pbmn));
    let frgn_ehrt = num(&cp.hts_frgn_ehrt); // 외국인 보유율 %

    // 외국인/기관 장중 가집계 — 별도 TR, 실패해도 윗줄은 보존.
    let (frgn_str, orgn_str) = match kis.domestic_stock().investor_trend_estimate(code).await {
        Ok(rows) => match rows.last() {
            Some(r) => (
                fmt_signed_qty(num(&r.frgn_fake_ntby_qty)),
                fmt_signed_qty(num(&r.orgn_fake_ntby_qty)),
            ),
            None => ("n/a".into(), "n/a".into()),
        },
        Err(_) => ("n/a".into(), "n/a".into()),
    };

    format!(
        "  {name:<9} 현재가 {prpr:>9.0}  등락 {sign}{ctrt:>5.2}%  거래대금 {tr_eok:>8.0}억  \
         외국인가집계 {frgn_str:>11}  기관가집계 {orgn_str:>11}  (외국인보유율 {frgn_ehrt:.2}%)"
    )
}

/// 부호 있는 수량(주) — +/− 명시, 천단위 콤마.
fn fmt_signed_qty(q: f64) -> String {
    let sign = if q > 0.0 { "+" } else if q < 0.0 { "-" } else { "" };
    let a = q.abs();
    // 간단 천단위 콤마.
    let i = a.round() as i64;
    let s = i.to_string();
    let mut out = String::new();
    let bytes = s.as_bytes();
    for (n, b) in bytes.iter().enumerate() {
        if n > 0 && (bytes.len() - n).is_multiple_of(3) {
            out.push(',');
        }
        out.push(*b as char);
    }
    format!("{sign}{out}")
}

/// Binance KR-perp 1줄: 마크가·펀딩(연율)·24h 거래대금·호가 잔량.
/// 패널 헤더와 별개로 각 줄이 독립 실패하도록 Result로 반환하지 않고 String.
async fn perp_line(md: &kis_adapter::binance::market::Market<'_>, name: &str, symbol: &str) -> String {
    let idx = match md.premium_index(symbol).await {
        Ok(i) => i,
        Err(e) => return format!("  {name:<8} mark n/a ({symbol} premiumIndex 에러: {e})"),
    };
    let mark = num(&idx.mark_price);
    let raw_funding = num(&idx.last_funding_rate); // 분수, 예 0.0001 = 0.01%
    let funding_pct = raw_funding * 100.0;
    // 8h 펀딩(mod.rs 문서) → 연율 = ×3회/일 ×365일.
    let funding_annual = raw_funding * 3.0 * 365.0 * 100.0;
    let crowd = if raw_funding > 0.0 {
        "양(+)=롱 과밀"
    } else if raw_funding < 0.0 {
        "음(−)=숏 과밀"
    } else {
        "중립"
    };

    // 24h 거래대금(유동성). 실패해도 마크가/펀딩은 보존.
    let (vol_str, vol_thin) = match md.ticker_24hr(symbol).await {
        Ok(t) => {
            let qv = num(&t.quote_volume); // USDT 거래대금
            // 24h 명목거래대금 임계치 — 100만 USDT 미만은 사실상 사용 불가.
            (format!("{qv:>12.0} USDT", qv = qv), qv < 1.0e6)
        }
        Err(_) => ("n/a".into(), true),
    };

    // 호가 매수/매도 1호가 잔량 + L1 명목금액(= mark × 잔량). 유동성의 *진짜*
    // 리얼리티 체크: 24h 거래대금이 멀쩡해도 호가창이 얇으면(L1 명목이 푼돈이면)
    // 어떤 실주문이든 호가를 쓸어버린다 → 슬리피지.
    let (book_str, depth_thin) = match md.depth(symbol, Some(5)).await {
        Ok(b) => {
            let bid_qty = b.bids.first().map(|e| num(&e[1])).unwrap_or(0.0);
            let ask_qty = b.asks.first().map(|e| num(&e[1])).unwrap_or(0.0);
            let bid_notional = mark * bid_qty;
            let ask_notional = mark * ask_qty;
            // L1 양측 모두 1,000 USDT 미만이면 사실상 거래 불가 깊이.
            let depth_thin = bid_notional < 1_000.0 || ask_notional < 1_000.0;
            (
                format!(
                    "매수1 {bid_qty:.2}계약(~{bid_notional:.0} USDT) / 매도1 {ask_qty:.2}계약(~{ask_notional:.0} USDT)"
                ),
                depth_thin,
            )
        }
        Err(_) => ("호가 n/a".into(), true),
    };

    let thin = vol_thin || depth_thin;
    let thin_flag = if thin {
        let why = match (vol_thin, depth_thin) {
            (true, true) => "24h거래대금+호가 모두 얇음",
            (true, false) => "24h거래대금 얇음",
            (false, true) => "호가 L1 명목 푼돈",
            (false, false) => "",
        };
        format!("  ⚠️ THIN — slippage risk ({why})")
    } else {
        String::new()
    };

    format!(
        "  {name:<8} mark {mark:>9.2}(USD?)  funding {funding_pct:+.4}%(연율 {funding_annual:+.1}%, {crowd})\n\
         {pad}24h거래대금 {vol_str}  호가 {book_str}{thin_flag}",
        pad = "           "
    )
}

/// 글로벌 반도체 1종목: 전일 종가 대비 등락%.
async fn semi_cell(kis: &KisClient, exch: OverseasExchange, sym: &str) -> String {
    match kis.overseas_stock().current_price(exch, sym).await {
        Ok(c) => {
            let last = num(&c.last);
            let rate = num(&c.rate);
            let sign = if c.rate.trim_start().starts_with('-') || rate < 0.0 {
                ""
            } else {
                "+"
            };
            format!("{sym} {sign}{rate:.2}% ({last:.2})")
        }
        Err(_) => format!("{sym} n/a"),
    }
}

async fn render(kis: &KisClient) -> Result<(), Dyn> {
    let ts = chrono::Local::now().format("%Y-%m-%d %H:%M:%S KST");
    println!("=== KR TECH DRIVER DASHBOARD  {ts} ===");
    println!("    (decision-support — 사실만; 매수/매도 신호 아님)");
    println!();

    // ── KRX SPOT ──────────────────────────────────────────────
    println!("[KRX SPOT]");
    println!("{}", krx_line(kis, "삼성전자", "005930").await);
    println!("{}", krx_line(kis, "SK하이닉스", "000660").await);
    println!("   가집계 부호: +순매수 / −순매도 (장중 실시간 추정, 확정 아님)");
    println!();

    // ── BINANCE PERP ──────────────────────────────────────────
    println!("[BINANCE PERP] (USDM Futures, 8h 펀딩)");
    match BinanceClient::new(BinanceConfig::public()) {
        Ok(bc) => {
            let md = bc.market();
            println!("{}", perp_line(&md, "SAMSUNG", SAMSUNG).await);
            println!("{}", perp_line(&md, "HYNIX", SK_HYNIX).await);
            println!(
                "   basis(perp vs KRX spot): 단위 불일치 추정 — perp mark는 USD,\n\
                 \x20  KRX 현물은 KRW. FX 소스 없음 → basis% 미산출(raw 값만, unit-unverified)."
            );
        }
        Err(e) => println!("  Binance 클라이언트 생성 실패: {e}"),
    }
    println!();

    // ── GLOBAL SEMIS ──────────────────────────────────────────
    println!("[GLOBAL SEMIS] (전일 종가 대비)");
    let nvda = semi_cell(kis, OverseasExchange::Nasd, "NVDA").await;
    let mu = semi_cell(kis, OverseasExchange::Nasd, "MU").await;
    let avgo = semi_cell(kis, OverseasExchange::Nasd, "AVGO").await;
    let tsm = semi_cell(kis, OverseasExchange::Nyse, "TSM").await;
    println!("   {nvda}   {mu}   {avgo}   {tsm}");
    println!();

    println!("[CONTEXT] 외국인 보유율은 [KRX SPOT] 줄 끝 동봉. 공매도잔고는 KIS 미제공(KRX 외부 소스 필요).");
    Ok(())
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Dyn> {
    let kis = KisClient::new(KisConfig::from_env()?)?;

    // DASH_WATCH=<secs> → 루프. 미설정/0 → one-shot.
    let watch: u64 = env_or("DASH_WATCH", "0").parse().unwrap_or(0);
    if watch == 0 {
        render(&kis).await?;
    } else {
        loop {
            // 화면 클리어(ANSI) 후 재출력.
            print!("\x1b[2J\x1b[H");
            if let Err(e) = render(&kis).await {
                eprintln!("render 에러: {e}");
            }
            tokio::time::sleep(std::time::Duration::from_secs(watch)).await;
        }
    }
    Ok(())
}
