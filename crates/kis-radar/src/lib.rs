//! 순환매 레이더 — KIS 시장 스냅샷에서 주도주/이탈을 판정.
//!
//! `engine`은 순수 로직(KIS 호출 없음). 녹화 스냅샷으로 오프라인 검증·백테스트 가능.
//! 수집기(`collector` feature)·PyO3 바인딩(`python` feature)은 후속 단계.

pub mod engine;

#[cfg(feature = "collector")]
pub mod collect;

#[cfg(feature = "python")]
mod py;

pub use engine::{
    Flow, NameSnap, RadarError, RankedName, RotationConfig, RotationEngine, RotationReport,
    RotationSnapshot, Verdict,
};

#[cfg(feature = "collector")]
pub use collect::{CollectError, Collector};
