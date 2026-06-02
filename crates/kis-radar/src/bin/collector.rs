//! 순환매 레이더 수집기 데몬.
//!
//! 거래대금순 랭킹(KRX+NXT 통합) + 상위 종목 투자자 가집계를 주기적으로 폴링,
//! 한 줄당 한 스냅샷(JSONL)으로 append. Python(리플레이/스캐너)이 이 파일을 읽는다.
//!
//! 네이티브 tokio 런타임 — PyO3 async 회피. 장시간 데몬은 프로세스가 맞다.
//!
//! 환경변수:
//! - `RADAR_OUT`           출력 jsonl 경로(기본 `data/raw/kis/snapshots.jsonl`)
//! - `RADAR_INTERVAL_SECS` 폴 주기 초(기본 60)
//! - `RADAR_FLOW_TOP`      투자자 가집계 붙일 상위 종목 수(기본 10)
//! - `KIS_*`               어댑터 자격증명(KisConfig::from_env)

use std::io::Write;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use kis_adapter::Market;
use kis_radar::Collector;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let out = std::env::var("RADAR_OUT")
        .unwrap_or_else(|_| "data/raw/kis/snapshots.jsonl".to_string());
    let interval: u64 = std::env::var("RADAR_INTERVAL_SECS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(60);
    let flow_top: usize = std::env::var("RADAR_FLOW_TOP")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(10);

    if let Some(parent) = std::path::Path::new(&out).parent() {
        std::fs::create_dir_all(parent)?;
    }

    let collector = Collector::from_env()?;
    eprintln!("rotation collector → {out} (every {interval}s, flow_top={flow_top}, market=UN)");

    loop {
        let ts = SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis() as i64;
        match collector.snapshot(Market::Unified, flow_top, ts).await {
            Ok(snap) => {
                let line = serde_json::to_string(&snap)?;
                let mut f = std::fs::OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(&out)?;
                writeln!(f, "{line}")?;
                eprintln!("[{ts}] {} names appended", snap.names.len());
            }
            Err(e) => eprintln!("[{ts}] snapshot error: {e}"),
        }
        tokio::time::sleep(Duration::from_secs(interval)).await;
    }
}
