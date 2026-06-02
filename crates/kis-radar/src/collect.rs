//! 라이브 수집기 — KIS 어댑터로 [`RotationSnapshot`] 한 시점을 수집.
//!
//! 거래대금순 랭킹(시장 전체) + 상위 종목 투자자 가집계(외국인/기관)를 묶어
//! 엔진이 먹는 스냅샷을 만든다. `ts`는 호출자가 주입(런타임 클럭은 상위 계층 책임).
//!
//! 장중 가집계(`investor_trend_estimate`)엔 **개인 순매수가 없다** → `prsn_ntby=0`.
//! 따라서 장중 분산(Distribution) 판정은 제한적(주도주/이탈은 스마트머니로 동작).
//! 개인 포함 확정 플로우는 EOD `investor_trend_daily`로 보강(후속).

use kis_adapter::{KisClient, KisConfig, Market, RankBy};

use crate::engine::{Flow, NameSnap, RotationSnapshot};

/// 수집기 오류.
#[derive(Debug, thiserror::Error)]
pub enum CollectError {
    #[error("KIS adapter error: {0}")]
    Kis(#[from] kis_adapter::KisError),
}

/// KIS 라이브 수집기.
pub struct Collector {
    client: KisClient,
}

impl Collector {
    /// 환경변수(`KIS_*`)에서 클라이언트 구성.
    pub fn from_env() -> Result<Self, CollectError> {
        let cfg = KisConfig::from_env()?;
        Ok(Self {
            client: KisClient::new(cfg)?,
        })
    }

    /// 기존 클라이언트로 구성.
    pub fn new(client: KisClient) -> Self {
        Self { client }
    }

    /// 한 시점 스냅샷 수집.
    ///
    /// 거래대금순 랭킹을 받고, 상위 `flow_top`개 종목에 한해 투자자 가집계를 덧붙인다
    /// (레이트리밋 절약 — 랭킹 전부에 flow를 붙이면 호출 폭증). `ts`는 호출자 주입.
    pub async fn snapshot(
        &self,
        market: Market,
        flow_top: usize,
        ts: i64,
    ) -> Result<RotationSnapshot, CollectError> {
        let ds = self.client.domestic_stock();
        let ranks = ds.volume_rank(market, RankBy::TradingAmount).await?;

        let mut names = Vec::with_capacity(ranks.len());
        for (i, item) in ranks.iter().enumerate() {
            let flow = if i < flow_top {
                // 가집계 실패(데이터 없음 등)는 flow=None으로 흡수 — 스냅샷은 계속.
                match ds.investor_trend_estimate(&item.mksc_shrn_iscd).await {
                    Ok(est) => est.last().map(|e| Flow {
                        frgn_ntby: parse_i64(&e.frgn_fake_ntby_qty),
                        orgn_ntby: parse_i64(&e.orgn_fake_ntby_qty),
                        prsn_ntby: 0, // 장중 가집계엔 개인 미포함.
                    }),
                    Err(_) => None,
                }
            } else {
                None
            };

            names.push(NameSnap {
                code: item.mksc_shrn_iscd.clone(),
                name: item.hts_kor_isnm.clone(),
                turnover: parse_f64(&item.acml_tr_pbmn),
                pct_change: parse_f64(&item.prdy_ctrt),
                flow,
            });
        }

        Ok(RotationSnapshot { ts, names })
    }
}

/// KIS 문자열 숫자 → f64. 콤마·공백·부호 내성, 실패 시 0.0.
fn parse_f64(s: &str) -> f64 {
    let cleaned: String = s.chars().filter(|c| !c.is_whitespace() && *c != ',').collect();
    cleaned.parse().unwrap_or(0.0)
}

/// KIS 문자열 정수 → i64. 음수(순매도) 허용, 실패 시 0.
fn parse_i64(s: &str) -> i64 {
    let cleaned: String = s.chars().filter(|c| !c.is_whitespace() && *c != ',').collect();
    // 소수점 들어오면 절사 후 정수 파싱.
    let head = cleaned.split('.').next().unwrap_or("");
    head.parse().unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_f64_handles_commas_and_blank() {
        assert_eq!(parse_f64("1,234,567"), 1234567.0);
        assert_eq!(parse_f64("2.86"), 2.86);
        assert_eq!(parse_f64(""), 0.0);
        assert_eq!(parse_f64("  "), 0.0);
    }

    #[test]
    fn parse_i64_handles_negative_and_decimal() {
        assert_eq!(parse_i64("-400"), -400);
        assert_eq!(parse_i64("1,200"), 1200);
        assert_eq!(parse_i64("350.0"), 350);
        assert_eq!(parse_i64(""), 0);
    }
}
