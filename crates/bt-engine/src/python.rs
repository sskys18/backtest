use numpy::{PyArray1, PyReadonlyArray1};
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::engine::BacktestEngine as RustEngine;
use crate::types::*;

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

#[pymodule]
fn bt_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyBacktestEngine>()?;
    Ok(())
}
