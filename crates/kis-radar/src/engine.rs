//! 순환매 레이더 엔진 — 시장 스냅샷 스트림에서 주도주/이탈을 판정.
//!
//! 가격이 아니라 **거래대금 share의 이동**을 본다. 순환매에서 돈은 시장을
//! 떠나지 않고 테마 사이를 회전한다 — 그 회전의 지문이 거래대금 share다.
//! 누가 사는지(외국인+기관 vs 개인)로 진짜 주도주와 분산(개인이 떠안는 함정)을 가른다.
//!
//! 순수 로직 — KIS 호출 없음. [`RotationSnapshot`] 윈도우를 입력받아
//! [`RotationReport`]를 낸다. 녹화 스냅샷으로 오프라인 검증·백테스트 가능.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

/// 한 종목의 투자자 순매수(가집계 또는 확정).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Flow {
    /// 외국인 순매수(수량 또는 금액 — 호출자 일관 유지).
    pub frgn_ntby: i64,
    /// 기관 순매수.
    pub orgn_ntby: i64,
    /// 개인 순매수.
    pub prsn_ntby: i64,
}

impl Flow {
    /// 스마트머니 순매수 = 외국인 + 기관.
    pub fn smart_net(&self) -> i64 {
        self.frgn_ntby + self.orgn_ntby
    }
}

/// 한 시점·한 종목 스냅샷.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NameSnap {
    pub code: String,
    pub name: String,
    /// 누적 거래대금 — 순환매 연료. 일중 단조 증가 가정.
    pub turnover: f64,
    /// 전일 대비율(%) — 참고용. 판정엔 미사용.
    pub pct_change: f64,
    /// 투자자 순매수. 없으면 share만으로 판정(미확인 모드).
    pub flow: Option<Flow>,
}

/// 한 시점의 시장 스냅샷(거래대금 순위 등).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RotationSnapshot {
    /// 단조 증가 시퀀스/epoch. 윈도우 정렬 가정(호출자 보장).
    pub ts: i64,
    pub names: Vec<NameSnap>,
}

/// 엔진 파라미터. 모두 튜너블.
#[derive(Debug, Clone, Copy)]
pub struct RotationConfig {
    /// 주도주/이탈 판정 share 변화 임계(절대값, 0~1). 기본 0.005 = 0.5%p.
    pub share_delta_threshold: f64,
    /// 출력 주도주 상한.
    pub top_n: usize,
}

impl Default for RotationConfig {
    fn default() -> Self {
        Self {
            share_delta_threshold: 0.005,
            top_n: 20,
        }
    }
}

/// 종목 판정.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Verdict {
    /// share 상승 + 스마트머니 매수 — 탈 것.
    Leader,
    /// 스마트머니 매도인데 개인이 매수 — 분산(함정), 피할 것.
    Distribution,
    /// share 붕괴 — 돈이 떠남, 이탈.
    Fading,
    /// 유의미한 신호 없음.
    Neutral,
}

/// 판정된 종목.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RankedName {
    pub code: String,
    pub name: String,
    /// 최신 스냅샷의 거래대금 share(0~1).
    pub cur_share: f64,
    /// 윈도우 시작 대비 share 변화(+면 유입).
    pub share_delta: f64,
    /// 스마트머니 순매수(flow 있을 때만).
    pub smart_net: Option<i64>,
    pub verdict: Verdict,
}

/// 레이더 결과 — 버킷별 분류.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct RotationReport {
    /// share 가속 + 스마트머니 확인. share_delta 내림차순.
    pub leaders: Vec<RankedName>,
    /// 개인이 떠안는 분산. cur_share 내림차순.
    pub distribution: Vec<RankedName>,
    /// share 붕괴. share_delta 오름차순(가장 급한 이탈 먼저).
    pub fading: Vec<RankedName>,
}

/// 레이더 오류.
#[derive(Debug, thiserror::Error)]
pub enum RadarError {
    /// share 변화 계산엔 최소 2개 스냅샷 필요.
    #[error("need >= 2 snapshots, got {0}")]
    TooFewSnapshots(usize),
}

/// 순환매 레이더 엔진.
pub struct RotationEngine;

impl RotationEngine {
    /// 스냅샷 윈도우를 분석해 주도주/분산/이탈 분류.
    ///
    /// share = 종목 거래대금 / 전체 거래대금. `share_delta` = 최신 share − 윈도우 시작 share.
    /// 누적 거래대금이 시장 전체에서 차지하는 비중이 윈도우 동안 어떻게 이동했는지가 신호.
    pub fn analyze(
        window: &[RotationSnapshot],
        cfg: RotationConfig,
    ) -> Result<RotationReport, RadarError> {
        if window.len() < 2 {
            return Err(RadarError::TooFewSnapshots(window.len()));
        }
        let base = &window[0];
        let latest = &window[window.len() - 1];

        let base_share = share_map(base);
        let latest_total: f64 = latest.names.iter().map(|n| n.turnover.max(0.0)).sum();

        let thr = cfg.share_delta_threshold;
        let mut leaders = Vec::new();
        let mut distribution = Vec::new();
        let mut fading = Vec::new();

        for n in &latest.names {
            let cur_share = if latest_total > 0.0 {
                n.turnover.max(0.0) / latest_total
            } else {
                0.0
            };
            let prev_share = base_share.get(n.code.as_str()).copied().unwrap_or(0.0);
            let share_delta = cur_share - prev_share;
            let smart_net = n.flow.map(|f| f.smart_net());

            let verdict = classify(&n.flow, cur_share, share_delta, thr);
            let ranked = RankedName {
                code: n.code.clone(),
                name: n.name.clone(),
                cur_share,
                share_delta,
                smart_net,
                verdict,
            };
            match verdict {
                Verdict::Leader => leaders.push(ranked),
                Verdict::Distribution => distribution.push(ranked),
                Verdict::Fading => fading.push(ranked),
                Verdict::Neutral => {}
            }
        }

        // 주도주: share_delta 내림차순, top_n.
        leaders.sort_by(|a, b| b.share_delta.total_cmp(&a.share_delta));
        leaders.truncate(cfg.top_n);
        // 분산: 거래대금 share 내림차순(가장 뜨거운 함정 먼저).
        distribution.sort_by(|a, b| b.cur_share.total_cmp(&a.cur_share));
        // 이탈: share_delta 오름차순(가장 급한 유출 먼저).
        fading.sort_by(|a, b| a.share_delta.total_cmp(&b.share_delta));

        Ok(RotationReport {
            leaders,
            distribution,
            fading,
        })
    }
}

/// 종목 판정 로직. 분산을 주도주보다 먼저 검사(함정 우선 차단).
fn classify(flow: &Option<Flow>, cur_share: f64, share_delta: f64, thr: f64) -> Verdict {
    match flow {
        Some(f) => {
            let smart = f.smart_net();
            // 스마트머니 매도 + 개인 매수 + 실제 거래 中 → 분산(함정). share 상승이어도.
            if smart < 0 && f.prsn_ntby > 0 && cur_share > 0.0 {
                Verdict::Distribution
            } else if share_delta <= -thr {
                Verdict::Fading
            } else if share_delta >= thr && smart > 0 {
                Verdict::Leader
            } else {
                Verdict::Neutral
            }
        }
        // flow 미확인: share만으로 판정.
        None => {
            if share_delta >= thr {
                Verdict::Leader
            } else if share_delta <= -thr {
                Verdict::Fading
            } else {
                Verdict::Neutral
            }
        }
    }
}

/// 스냅샷의 종목별 거래대금 share 맵.
fn share_map(snap: &RotationSnapshot) -> HashMap<&str, f64> {
    let total: f64 = snap.names.iter().map(|n| n.turnover.max(0.0)).sum();
    if total <= 0.0 {
        return HashMap::new();
    }
    snap.names
        .iter()
        .map(|n| (n.code.as_str(), n.turnover.max(0.0) / total))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    type Row<'a> = (&'a str, f64, Option<(i64, i64, i64)>);

    fn snap(ts: i64, rows: &[Row<'_>]) -> RotationSnapshot {
        RotationSnapshot {
            ts,
            names: rows
                .iter()
                .map(|(code, turnover, flow)| NameSnap {
                    code: (*code).into(),
                    name: (*code).into(),
                    turnover: *turnover,
                    pct_change: 0.0,
                    flow: flow.map(|(frgn, orgn, prsn)| Flow {
                        frgn_ntby: frgn,
                        orgn_ntby: orgn,
                        prsn_ntby: prsn,
                    }),
                })
                .collect(),
        }
    }

    #[test]
    fn too_few_snapshots() {
        let w = vec![snap(0, &[("A", 100.0, None)])];
        assert!(matches!(
            RotationEngine::analyze(&w, RotationConfig::default()),
            Err(RadarError::TooFewSnapshots(1))
        ));
    }

    #[test]
    fn leader_rising_share_with_smart_buy() {
        // A: share .33 → .67 (급유입) + 외국인/기관 매수.
        let base = snap(0, &[("A", 100.0, None), ("B", 100.0, None), ("C", 100.0, None)]);
        let now = snap(
            1,
            &[
                ("A", 400.0, Some((500, 300, -200))),
                ("B", 100.0, Some((0, 0, 0))),
                ("C", 100.0, Some((0, 0, 0))),
            ],
        );
        let r = RotationEngine::analyze(&[base, now], RotationConfig::default()).unwrap();
        assert_eq!(r.leaders.len(), 1);
        assert_eq!(r.leaders[0].code, "A");
        assert_eq!(r.leaders[0].verdict, Verdict::Leader);
        assert!(r.leaders[0].share_delta > 0.3);
        assert_eq!(r.leaders[0].smart_net, Some(800));
    }

    #[test]
    fn distribution_retail_buys_into_smart_selling() {
        // A: share 높고 상승하지만 외국인+기관 순매도 + 개인 순매수 → 분산(함정).
        let base = snap(0, &[("A", 100.0, None), ("B", 300.0, None)]);
        let now = snap(
            1,
            &[
                ("A", 600.0, Some((-400, -200, 900))),
                ("B", 100.0, Some((0, 0, 0))),
            ],
        );
        let r = RotationEngine::analyze(&[base, now], RotationConfig::default()).unwrap();
        assert_eq!(r.distribution.len(), 1);
        assert_eq!(r.distribution[0].code, "A");
        assert_eq!(r.distribution[0].verdict, Verdict::Distribution);
        // 분산은 주도주에 들어가면 안 됨 — share 상승이어도.
        assert!(r.leaders.iter().all(|l| l.code != "A"));
    }

    #[test]
    fn fading_share_collapse_is_exit() {
        // A: share .80 → .20 붕괴 → 이탈.
        let base = snap(0, &[("A", 400.0, None), ("B", 100.0, None)]);
        let now = snap(
            1,
            &[("A", 100.0, Some((50, 50, 0))), ("B", 400.0, Some((0, 0, 0)))],
        );
        let r = RotationEngine::analyze(&[base, now], RotationConfig::default()).unwrap();
        assert!(r
            .fading
            .iter()
            .any(|f| f.code == "A" && f.verdict == Verdict::Fading));
        assert!(r.leaders.iter().all(|l| l.code != "A"));
    }

    #[test]
    fn no_flow_ranks_by_share_only() {
        // flow 없음 → share_delta만으로 leader/fading.
        let base = snap(0, &[("A", 100.0, None), ("B", 100.0, None)]);
        let now = snap(1, &[("A", 900.0, None), ("B", 100.0, None)]);
        let r = RotationEngine::analyze(&[base, now], RotationConfig::default()).unwrap();
        assert_eq!(r.leaders[0].code, "A");
        assert_eq!(r.leaders[0].smart_net, None);
        assert!(r.fading.iter().any(|f| f.code == "B"));
    }

    #[test]
    fn stable_shares_are_neutral() {
        // share 변화 미미 → 어느 버킷에도 안 들어감.
        let base = snap(
            0,
            &[("A", 100.0, Some((10, 10, 0))), ("B", 100.0, Some((0, 0, 0)))],
        );
        let now = snap(
            1,
            &[("A", 101.0, Some((10, 10, 0))), ("B", 100.0, Some((0, 0, 0)))],
        );
        let r = RotationEngine::analyze(&[base, now], RotationConfig::default()).unwrap();
        assert!(r.leaders.is_empty());
        assert!(r.distribution.is_empty());
        assert!(r.fading.is_empty());
    }

    #[test]
    fn leaders_sorted_by_share_delta_desc() {
        // 고정 합계라 두 종목이 동시에 share를 얻으려면 REST가 줄어야 함.
        // base: A .10, B .10, REST .80 → now: A .30, B .50, REST .20.
        let base = snap(
            0,
            &[("A", 100.0, None), ("B", 100.0, None), ("REST", 800.0, None)],
        );
        let now = snap(
            1,
            &[
                ("A", 300.0, Some((10, 10, 0))),
                ("B", 500.0, Some((10, 10, 0))),
                ("REST", 200.0, Some((0, 0, 0))),
            ],
        );
        let r = RotationEngine::analyze(&[base, now], RotationConfig::default()).unwrap();
        // B가 share를 더 많이 가져감(+.40 > +.20) → 1위.
        assert_eq!(r.leaders[0].code, "B");
        assert_eq!(r.leaders[1].code, "A");
        assert!(r.leaders[0].share_delta > r.leaders[1].share_delta);
        // REST는 share 붕괴 → 이탈.
        assert!(r.fading.iter().any(|f| f.code == "REST"));
    }
}
