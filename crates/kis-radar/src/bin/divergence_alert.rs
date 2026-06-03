//! 외국인 수급 다이버전스 알림기 — 세션 전체에서 유일하게 actionable한 실시간 인풋.
//!
//! 감시 조건(장중 외국인 가집계 vs 등락률):
//! - **분산(distribution)**: 등락률 ≥ +THRESH 인데 외국인 순매도 → 강세에 외국인이 판다(주의).
//! - **매집(accumulation)**: 등락률 ≤ −THRESH 인데 외국인 순매수 → 약세에 외국인이 산다.
//!
//! 신호(매수/매도 자동) 아님 — 포지션 관리 판단용 경보.
//!
//! 상태변화시에만 발동(디바운스 — 조건 지속 동안 매 폴 스팸 안 함). 터미널 벨(\x07).
//! 가집계는 장중(09:00~15:30 KST)만 유효 — 장외엔 비거나 stale.
//!
//! 환경: KIS_* 필요. ALERT_CODES(기본 005930,000660), ALERT_INTERVAL_SECS(기본 30),
//!       ALERT_THRESH(등락률 %, 기본 2.0).

use std::collections::HashMap;
use std::time::Duration;

use kis_adapter::{KisClient, KisConfig, Market};

type Dyn = Box<dyn std::error::Error>;

fn num(s: &str) -> f64 {
    s.chars().filter(|c| *c != ',' && !c.is_whitespace()).collect::<String>().parse().unwrap_or(0.0)
}

fn env_or(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_string())
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum State {
    Neutral,
    Distribution,
    Accumulation,
}

/// 알려진 종목명(현재가 TR엔 종목명 없음) — 없으면 코드 그대로.
fn name_of(code: &str) -> &str {
    match code {
        "005930" => "삼성전자",
        "000660" => "SK하이닉스",
        "005380" => "현대차",
        other => other,
    }
}

/// 한 종목의 등락률(부호 포함) + 외국인 가집계 순매수 조회.
async fn probe(kis: &KisClient, code: &str) -> Option<(f64, f64)> {
    let cp = kis.domestic_stock().current_price(code, Market::Krx).await.ok()?;
    let mag = num(&cp.prdy_ctrt).abs();
    // KIS prdy_vrss_sign: 1상한 2상승 3보합 4하한 5하락.
    let signed = match cp.prdy_vrss_sign.trim() {
        "1" | "2" => mag,
        "4" | "5" => -mag,
        _ => 0.0,
    };
    // 가집계 — 최신(누적) 행. 비면 장외/데이터없음.
    let est = kis.domestic_stock().investor_trend_estimate(code).await.ok()?;
    let frgn = est.last().map(|r| num(&r.frgn_fake_ntby_qty))?;
    Some((signed, frgn))
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Dyn> {
    let codes: Vec<String> = env_or("ALERT_CODES", "005930,000660")
        .split(',').map(|s| s.trim().to_string()).collect();
    let interval: u64 = env_or("ALERT_INTERVAL_SECS", "30").parse().unwrap_or(30);
    let thresh: f64 = env_or("ALERT_THRESH", "2.0").parse().unwrap_or(2.0);

    let kis = KisClient::new(KisConfig::from_env()?)?;
    eprintln!(
        "다이버전스 알림기 — {:?}, 매 {interval}s, 임계 ±{thresh}% (장중 09:00~15:30만 유효)",
        codes
    );

    let mut state: HashMap<String, State> = HashMap::new();
    loop {
        for code in &codes {
            let Some((ctrt, frgn)) = probe(&kis, code).await else {
                eprintln!("  {code}: 데이터 없음(장외?)");
                continue;
            };
            let name = name_of(code);
            let new = if ctrt >= thresh && frgn < 0.0 {
                State::Distribution
            } else if ctrt <= -thresh && frgn > 0.0 {
                State::Accumulation
            } else {
                State::Neutral
            };
            let prev = state.get(code).copied().unwrap_or(State::Neutral);

            // 상태 진입시에만 발동(디바운스).
            if new != prev && new != State::Neutral {
                let (tag, msg) = match new {
                    State::Distribution => ("⚠️ 분산", "강세에 외국인 순매도 — 롱 트림/스탑 점검"),
                    State::Accumulation => ("🟢 매집", "약세에 외국인 순매수 — 진입 근거 점검"),
                    State::Neutral => unreachable!(),
                };
                // 벨 + 강조.
                print!("\x07");
                println!(
                    "{tag}  {name}({code})  등락 {ctrt:+.2}%  외국인가집계 {frgn:+.0}  → {msg}"
                );
            } else {
                eprintln!("  {name}({code}) 등락 {ctrt:+.2}% 외국인 {frgn:+.0} [{}]",
                    match new { State::Distribution => "분산", State::Accumulation => "매집", State::Neutral => "-" });
            }
            state.insert(code.clone(), new);
        }
        tokio::time::sleep(Duration::from_secs(interval)).await;
    }
}
