//! PyO3 바인딩 — 순수 엔진 `analyze`를 Python에 노출.
//!
//! 어댑터·tokio 없음(수집기는 별도 바이너리). JSON 문자열 in/out으로
//! pyclass 보일러플레이트 없이 최소 표면만 노출 — Python은 jsonl 스냅샷을 읽어
//! 이 함수로 리포트를 받는다(리플레이/스캐너).

use pyo3::prelude::*;

use crate::engine::{RotationConfig, RotationEngine, RotationSnapshot};

/// 스냅샷 윈도우(JSON 배열) → 회전 리포트(JSON).
///
/// `window_json`: `RotationSnapshot` 배열의 JSON. 시간순 정렬 가정.
/// 반환: `RotationReport` JSON(`leaders`/`distribution`/`fading`).
#[pyfunction]
#[pyo3(signature = (window_json, share_delta_threshold = 0.005, top_n = 20))]
fn analyze_json(window_json: &str, share_delta_threshold: f64, top_n: usize) -> PyResult<String> {
    let window: Vec<RotationSnapshot> = serde_json::from_str(window_json)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("bad window json: {e}")))?;
    let cfg = RotationConfig {
        share_delta_threshold,
        top_n,
    };
    let report = RotationEngine::analyze(&window, cfg)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    serde_json::to_string(&report)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
}

/// `import kis_radar` — Python 확장 모듈.
#[pymodule]
fn kis_radar(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(analyze_json, m)?)?;
    Ok(())
}
