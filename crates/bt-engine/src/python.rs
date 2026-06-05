use numpy::{PyArray1, PyReadonlyArray1, PyReadonlyArray2};
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::engine::BacktestEngine as RustEngine;
use crate::step::StepEngine as RustStepEngine;
use crate::types::*;

use crate::portfolio_engine as pe;

#[pyclass(name = "BacktestEngine")]
pub struct PyBacktestEngine {
    config: EngineConfig,
}

#[pymethods]
impl PyBacktestEngine {
    #[new]
    fn new(
        initial_capital: f64,
        broker: &Bound<'_, PyDict>,
        risk: &Bound<'_, PyDict>,
        bars_per_year: f64,
    ) -> PyResult<Self> {
        let slippage_bps: f64 = broker.get_item("slippage_bps")?.unwrap().extract()?;
        let fee_rate: f64 = broker.get_item("fee_rate")?.unwrap().extract()?;
        let fill_model_str: String = broker.get_item("fill_model")?.unwrap().extract()?;
        let fill_model = match fill_model_str.as_str() {
            "immediate" => FillModel::Immediate,
            "next_bar" => FillModel::NextBar,
            other => return Err(pyo3::exceptions::PyValueError::new_err(
                format!("invalid fill_model: '{}', expected 'immediate' or 'next_bar'", other),
            )),
        };

        let max_position_size: f64 = risk.get_item("max_position_size")?.unwrap().extract()?;
        let max_drawdown_halt: f64 = risk.get_item("max_drawdown_halt")?.unwrap().extract()?;
        let signal_threshold: f64 = risk.get_item("signal_threshold")?.unwrap().extract()?;

        Ok(Self {
            config: EngineConfig {
                initial_capital,
                broker: BrokerConfig { slippage_bps, fee_rate, fill_model },
                risk: RiskConfig { max_position_size, max_drawdown_halt, signal_threshold },
                bars_per_year,
            },
        })
    }

    fn run<'py>(
        &self,
        py: Python<'py>,
        timestamps: PyReadonlyArray1<'py, i64>,
        opens: PyReadonlyArray1<'py, f64>,
        highs: PyReadonlyArray1<'py, f64>,
        lows: PyReadonlyArray1<'py, f64>,
        closes: PyReadonlyArray1<'py, f64>,
        volumes: PyReadonlyArray1<'py, f64>,
        strengths: PyReadonlyArray1<'py, f64>,
        target_sizes: PyReadonlyArray1<'py, f64>,
    ) -> PyResult<Bound<'py, PyDict>> {
        let ts = timestamps.as_slice()?;
        let o = opens.as_slice()?;
        let h = highs.as_slice()?;
        let l = lows.as_slice()?;
        let c = closes.as_slice()?;
        let v = volumes.as_slice()?;
        let str_arr = strengths.as_slice()?;
        let ts_arr = target_sizes.as_slice()?;

        let bars: Vec<Bar> = (0..ts.len())
            .map(|i| Bar {
                timestamp: ts[i],
                open: o[i],
                high: h[i],
                low: l[i],
                close: c[i],
                volume: v[i],
            })
            .collect();

        let signals: Vec<Signal> = (0..str_arr.len())
            .map(|i| Signal {
                strength: str_arr[i],
                target_size: ts_arr[i],
            })
            .collect();

        let mut engine = RustEngine::new(self.config.clone())
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let result = engine
            .run(&bars, &signals)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        // Build result dict
        let dict = PyDict::new(py);

        // Equity curve as numpy arrays
        let eq_ts: Vec<i64> = result.equity_curve.iter().map(|e| e.0).collect();
        let eq_vals: Vec<f64> = result.equity_curve.iter().map(|e| e.1).collect();
        dict.set_item("equity_timestamps", PyArray1::from_vec(py, eq_ts))?;
        dict.set_item("equity_values", PyArray1::from_vec(py, eq_vals))?;

        // Fills as list of dicts
        let fills_list: Vec<Bound<'py, PyDict>> = result.fills.iter().map(|f| {
            let d = PyDict::new(py);
            d.set_item("order_id", f.order_id).unwrap();
            d.set_item("side", match f.side { OrderSide::Buy => "buy", OrderSide::Sell => "sell" }).unwrap();
            d.set_item("price", f.price).unwrap();
            d.set_item("quantity", f.quantity).unwrap();
            d.set_item("fee", f.fee).unwrap();
            d.set_item("timestamp", f.timestamp).unwrap();
            d
        }).collect();
        dict.set_item("fills", fills_list)?;

        // Metrics
        let m = &result.metrics;
        let metrics = PyDict::new(py);
        metrics.set_item("total_return", m.total_return)?;
        metrics.set_item("sharpe_ratio", m.sharpe_ratio)?;
        metrics.set_item("sortino_ratio", m.sortino_ratio)?;
        metrics.set_item("max_drawdown", m.max_drawdown)?;
        metrics.set_item("num_round_trips", m.num_round_trips)?;
        metrics.set_item("win_rate", m.win_rate)?;
        metrics.set_item("profit_factor", m.profit_factor)?;
        metrics.set_item("bars_per_year", m.bars_per_year)?;
        dict.set_item("metrics", metrics)?;

        // Final state
        let fs = &result.final_state;
        let state = PyDict::new(py);
        state.set_item("equity", fs.equity)?;
        state.set_item("cash", fs.cash)?;
        state.set_item("peak_equity", fs.peak_equity)?;
        state.set_item("total_pnl", fs.total_pnl)?;
        state.set_item("halted", fs.halted)?;
        state.set_item("has_position", fs.position.is_some())?;
        dict.set_item("final_state", state)?;

        dict.set_item("halted_at", result.halted_at)?;

        Ok(dict)
    }
}

#[pyclass(name = "StepEngine")]
pub struct PyStepEngine {
    inner: RustStepEngine,
}

#[pymethods]
impl PyStepEngine {
    #[new]
    fn new(
        initial_capital: f64,
        broker: &Bound<'_, PyDict>,
        risk: &Bound<'_, PyDict>,
        bars_per_year: f64,
        timestamps: PyReadonlyArray1<'_, i64>,
        opens: PyReadonlyArray1<'_, f64>,
        highs: PyReadonlyArray1<'_, f64>,
        lows: PyReadonlyArray1<'_, f64>,
        closes: PyReadonlyArray1<'_, f64>,
        volumes: PyReadonlyArray1<'_, f64>,
    ) -> PyResult<Self> {
        let slippage_bps: f64 = broker.get_item("slippage_bps")?.unwrap().extract()?;
        let fee_rate: f64 = broker.get_item("fee_rate")?.unwrap().extract()?;
        let fill_model_str: String = broker.get_item("fill_model")?.unwrap().extract()?;
        let fill_model = match fill_model_str.as_str() {
            "immediate" => FillModel::Immediate,
            "next_bar" => FillModel::NextBar,
            other => return Err(pyo3::exceptions::PyValueError::new_err(
                format!("invalid fill_model: '{}'", other),
            )),
        };
        let max_position_size: f64 = risk.get_item("max_position_size")?.unwrap().extract()?;
        let max_drawdown_halt: f64 = risk.get_item("max_drawdown_halt")?.unwrap().extract()?;
        let signal_threshold: f64 = risk.get_item("signal_threshold")?.unwrap().extract()?;

        let config = EngineConfig {
            initial_capital,
            broker: BrokerConfig { slippage_bps, fee_rate, fill_model },
            risk: RiskConfig { max_position_size, max_drawdown_halt, signal_threshold },
            bars_per_year,
        };

        let ts = timestamps.as_slice()?;
        let o = opens.as_slice()?;
        let h = highs.as_slice()?;
        let l = lows.as_slice()?;
        let c = closes.as_slice()?;
        let v = volumes.as_slice()?;
        let bars: Vec<Bar> = (0..ts.len()).map(|i| Bar {
            timestamp: ts[i], open: o[i], high: h[i], low: l[i], close: c[i], volume: v[i],
        }).collect();

        let inner = RustStepEngine::new(config, bars)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        Ok(Self { inner })
    }

    fn reset<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let obs = self.inner.reset();
        Self::obs_to_dict(py, &obs)
    }

    fn step<'py>(&mut self, py: Python<'py>, strength: f64, target_size: f64) -> PyResult<Bound<'py, PyDict>> {
        let result = self.inner.step(strength, target_size);
        let dict = PyDict::new(py);
        dict.set_item("obs", Self::obs_to_dict(py, &result.obs)?)?;
        dict.set_item("reward", result.reward)?;
        dict.set_item("done", result.done)?;
        dict.set_item("truncated", result.truncated)?;
        Ok(dict)
    }

    fn num_bars(&self) -> usize {
        self.inner.num_bars()
    }
}

impl PyStepEngine {
    fn obs_to_dict<'py>(py: Python<'py>, obs: &crate::step::StepObs) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        d.set_item("open", obs.open)?;
        d.set_item("high", obs.high)?;
        d.set_item("low", obs.low)?;
        d.set_item("close", obs.close)?;
        d.set_item("volume", obs.volume)?;
        d.set_item("equity", obs.equity)?;
        d.set_item("cash", obs.cash)?;
        d.set_item("position_qty", obs.position_qty)?;
        d.set_item("position_entry_price", obs.position_entry_price)?;
        d.set_item("unrealized_pnl", obs.unrealized_pnl)?;
        d.set_item("drawdown", obs.drawdown)?;
        d.set_item("step", obs.step)?;
        d.set_item("halted", obs.halted)?;
        Ok(d)
    }
}

// ---------------------------------------------------------------------------
// 2D panel binding (P1) — thin numpy → slice wrapper over portfolio_engine.
// ---------------------------------------------------------------------------

fn cfg_from_dict(cfg: &Bound<'_, PyDict>) -> PyResult<pe::PortfolioConfig> {
    fn f(d: &Bound<'_, PyDict>, k: &str, default: f64) -> PyResult<f64> {
        match d.get_item(k)? {
            Some(v) => v.extract(),
            None => Ok(default),
        }
    }
    let initial_collateral = f(cfg, "initial_collateral", 100_000.0)?;
    let fee_rate = f(cfg, "fee_rate", 0.0)?;
    let slippage_bps = f(cfg, "slippage_bps", 0.0)?;
    let im_rate = f(cfg, "im_rate", 0.1)?;
    let mm_rate = f(cfg, "mm_rate", 0.05)?;
    let leverage_cap = f(cfg, "leverage_cap", 1.0)?;
    let max_net = f(cfg, "max_net", 1.0)?;
    let max_per_asset = f(cfg, "max_per_asset", 1.0)?;
    let rebalance_freq = match cfg.get_item("rebalance_freq")? {
        Some(v) => v.extract::<usize>()?,
        None => 1,
    };
    let fill_model = match cfg.get_item("fill_model")? {
        Some(v) => match v.extract::<String>()?.as_str() {
            "immediate" => pe::FillModel::Immediate,
            "next_bar" => pe::FillModel::NextBar,
            other => {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid fill_model: '{other}'"
                )))
            }
        },
        None => pe::FillModel::NextBar,
    };
    let session_aware = match cfg.get_item("session_aware")? {
        Some(v) => v.extract::<bool>()?,
        None => false,
    };
    let on_violation = match cfg.get_item("on_violation")? {
        Some(v) => match v.extract::<String>()?.as_str() {
            "reject" => pe::ViolationMode::Reject,
            "renormalize" => pe::ViolationMode::Renormalize,
            other => {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid on_violation: '{other}'"
                )))
            }
        },
        None => pe::ViolationMode::Reject,
    };
    let cap_kind = match cfg.get_item("cap_kind")? {
        Some(v) => match v.extract::<String>()?.as_str() {
            "hard" => pe::CapKind::Hard,
            "soft" => {
                let tol = f(cfg, "cap_tolerance", 0.0)?;
                pe::CapKind::Soft { tolerance: tol }
            }
            other => {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "invalid cap_kind: '{other}'"
                )))
            }
        },
        None => pe::CapKind::Hard,
    };
    let numeraire_is_unit_fx = match cfg.get_item("numeraire_is_unit_fx")? {
        Some(v) => v.extract::<bool>()?,
        None => true,
    };
    Ok(pe::PortfolioConfig {
        initial_collateral,
        fee_rate,
        slippage_bps,
        im_rate,
        mm_rate,
        leverage_cap,
        max_net,
        max_per_asset,
        rebalance_freq,
        fill_model,
        session_aware,
        on_violation,
        cap_kind,
        numeraire_is_unit_fx,
    })
}

/// Flatten a [T,N] f64 ArrayView into a row-major Vec, asserting shape.
fn flat2(name: &str, arr: &PyReadonlyArray2<f64>, t: usize, n: usize) -> PyResult<Vec<f64>> {
    let view = arr.as_array();
    let (rows, cols) = (view.shape()[0], view.shape()[1]);
    if rows != t || cols != n {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "{name} shape ({rows},{cols}) != (T={t},N={n})"
        )));
    }
    let mut out = Vec::with_capacity(t * n);
    for r in 0..t {
        for c in 0..n {
            out.push(view[[r, c]]);
        }
    }
    Ok(out)
}

#[pyfunction]
#[pyo3(signature = (
    assets, close, open, funding, fx, weights, active_mask, sessions,
    decision_time, cfg, bias=None, bias_available_at=None, bias_mode="forbidden"
))]
#[allow(clippy::too_many_arguments)]
fn run_panel<'py>(
    py: Python<'py>,
    assets: Vec<String>,
    close: PyReadonlyArray2<'py, f64>,
    open: PyReadonlyArray2<'py, f64>,
    funding: PyReadonlyArray2<'py, f64>,
    fx: PyReadonlyArray2<'py, f64>,
    weights: PyReadonlyArray2<'py, f64>,
    active_mask: PyReadonlyArray2<'py, f64>,
    sessions: PyReadonlyArray2<'py, i64>,
    decision_time: PyReadonlyArray1<'py, i64>,
    cfg: &Bound<'py, PyDict>,
    bias: Option<PyReadonlyArray2<'py, f64>>,
    bias_available_at: Option<PyReadonlyArray1<'py, i64>>,
    bias_mode: &str,
) -> PyResult<Bound<'py, PyDict>> {
    let cv = close.as_array();
    let t = cv.shape()[0];
    let n = cv.shape()[1];
    if assets.len() != n {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "len(assets)={} != N={} (close columns)",
            assets.len(),
            n
        )));
    }

    let close_v = flat2("close", &close, t, n)?;
    let open_v = flat2("open", &open, t, n)?;
    let funding_v = flat2("funding", &funding, t, n)?;
    let fx_v = flat2("fx", &fx, t, n)?;
    let weights_v = flat2("weights", &weights, t, n)?;

    // active_mask passed as f64 0/1 → bool.
    let am_view = active_mask.as_array();
    if am_view.shape()[0] != t || am_view.shape()[1] != n {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "active_mask shape ({},{}) != (T={t},N={n})",
            am_view.shape()[0],
            am_view.shape()[1]
        )));
    }
    let mut active_v = Vec::with_capacity(t * n);
    for r in 0..t {
        for c in 0..n {
            active_v.push(am_view[[r, c]] != 0.0);
        }
    }

    let s_view = sessions.as_array();
    if s_view.shape()[0] != t || s_view.shape()[1] != n {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "sessions shape ({},{}) != (T={t},N={n})",
            s_view.shape()[0],
            s_view.shape()[1]
        )));
    }
    let mut sessions_v = Vec::with_capacity(t * n);
    for r in 0..t {
        for c in 0..n {
            sessions_v.push(s_view[[r, c]] as u8);
        }
    }

    let dt = decision_time.as_slice()?.to_vec();

    let mode = match bias_mode {
        "forbidden" => pe::BiasMode::Forbidden,
        "backtest" => pe::BiasMode::Backtest,
        "forward" => pe::BiasMode::Forward,
        other => {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "invalid bias_mode: '{other}'"
            )))
        }
    };

    let bias_v: Option<Vec<f64>> = match &bias {
        Some(b) => Some(flat2("bias", b, t, n)?),
        None => None,
    };
    let bias_av: Option<Vec<i64>> = match &bias_available_at {
        Some(a) => Some(a.as_slice()?.to_vec()),
        None => None,
    };

    let config = cfg_from_dict(cfg)?;

    let result = pe::run_panel_core(
        &assets,
        t,
        n,
        &close_v,
        &open_v,
        &funding_v,
        &fx_v,
        &weights_v,
        &active_v,
        &sessions_v,
        &dt,
        &config,
        bias_v.as_deref(),
        bias_av.as_deref(),
        mode,
    )
    .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

    let d = PyDict::new(py);
    d.set_item("equity", PyArray1::from_vec(py, result.equity))?;
    d.set_item("gross_exposure", PyArray1::from_vec(py, result.gross_exposure))?;
    d.set_item("net_exposure", PyArray1::from_vec(py, result.net_exposure))?;
    d.set_item("funding_pnl", PyArray1::from_vec(py, result.funding_pnl))?;
    d.set_item("turnover", PyArray1::from_vec(py, result.turnover))?;
    d.set_item("realized_pnl", PyArray1::from_vec(py, result.realized_pnl))?;

    let trades: Vec<Bound<'py, PyDict>> = result
        .trades
        .iter()
        .map(|tr| {
            let td = PyDict::new(py);
            td.set_item("bar", tr.bar).unwrap();
            td.set_item("asset", tr.asset).unwrap();
            td.set_item("qty", tr.qty).unwrap();
            td.set_item("price", tr.price).unwrap();
            td.set_item("fee", tr.fee).unwrap();
            td.set_item("fx", tr.fx).unwrap();
            td
        })
        .collect();
    d.set_item("trades", trades)?;

    Ok(d)
}

#[pymodule]
fn bt_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyBacktestEngine>()?;
    m.add_class::<PyStepEngine>()?;
    m.add_function(wrap_pyfunction!(run_panel, m)?)?;
    Ok(())
}
