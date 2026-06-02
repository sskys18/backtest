//! 단타(intraday day-trade) timing 신호 검증 — 삼성전자(005930) · SK하이닉스(000660), 1분봉.
//!
//! 단일종목 인트라데이 타이밍(NOT 횡단면). 가격/거래량만으로 만들 수 있는 약한 신호
//! 4종(VWAP 회귀 / 모멘텀 / 거래량 급증 / 시가범위 돌파)을 a-priori 고정 임계로 분류,
//! forward H분 수익(체결 lag·왕복비용 차감)이 그 종목의 **무조건 인트라데이 drift(baseline)**를
//! 이기는지만 본다. 삼전/하닉은 초효율·고차익거래 → 비용차감 후 null이 자연스럽고 정당한 결과.
//! edge 조작 금지. 정직하게 보고.
//!
//! 비용 모델·진입 lag는 intraday_check.rs validate() 패턴 재사용.
//! 통계는 분(minute) 단위가 아니라 **일(day)** 단위 — 분내 자기상관 때문에 분단위 t는 폭증.
//!
//! 검정 metric (drift 통제가 핵심):
//!   fwd = close[t+lag+H]/close[t+lag] − 1
//!   baseline = 그날 모든 분의 fwd 평균(gross, 무조건 drift = 그냥 보유 벤치마크)
//!   signal_net = 신호분 fwd 평균 − cost (액티브 왕복은 비용 지불)
//!   edge = (signal_gross − baseline_gross) − cost
//!   → edge>0 & t>~2 (타이밍이 drift를 이김) AND signal_net>0 (비용 후 거래가능) 둘 다여야 의미.
//!   t-stat은 **트리거가 있는 날**의 일별 edge 시계열에 대해(effective N = #days).
//!
//! 사용: `DAY_CODES=005930,000660 DAY_END=20260602 DAY_N=25 \
//!        cargo run -p kis-radar --features edgecheck --bin daytrade_check`
//!   또는 DAY_DATES=20260526,20260527,... 명시. DAY_COST_BPS(기본 35), DAY_ENTRY_LAG(기본 1).

use std::collections::BTreeMap;

use chrono::{Datelike, Duration, NaiveDate, Weekday};
use kis_adapter::kis::domestic_stock::MinuteCandle;
use kis_adapter::{KisClient, KisConfig, Market};

/// 쉼표/공백 제거 후 f64. 빈 문자열/파싱실패는 0.0.
fn num(s: &str) -> f64 {
    s.chars()
        .filter(|c| *c != ',' && !c.is_whitespace())
        .collect::<String>()
        .parse()
        .unwrap_or(0.0)
}

/// env 값 또는 default.
fn env_or(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_string())
}

/// "133000" → 810 (자정 기준 분).
fn mins(hhmmss: &str) -> u32 {
    let v: u32 = hhmmss.parse().unwrap_or(0);
    (v / 10000) * 60 + (v / 100) % 100
}

/// HHMMSS 1분 감소. "100000" → "095900".
fn dec_minute(hhmmss: &str) -> String {
    let total = mins(hhmmss);
    let t = total.saturating_sub(1);
    format!("{:02}{:02}00", t / 60, t % 60)
}

/// 한 종목 그날 전 세션 1분봉 — time 내려가며 페이징 (intraday_check 패턴).
async fn full_day(
    kis: &KisClient,
    code: &str,
    date: &str,
) -> Result<Vec<MinuteCandle>, Box<dyn std::error::Error>> {
    let mut all: BTreeMap<String, MinuteCandle> = BTreeMap::new();
    let mut time = "153000".to_string();
    for _ in 0..10 {
        let before = all.len();
        let (_summary, candles) = kis
            .domestic_stock()
            .minute_chart_by_date(code, date, &time, true, Market::Krx)
            .await?;
        if candles.is_empty() {
            break;
        }
        let mut earliest = u32::MAX;
        for c in candles {
            let t: u32 = c.stck_cntg_hour.parse().unwrap_or(0);
            earliest = earliest.min(t);
            all.insert(c.stck_cntg_hour.clone(), c);
        }
        if earliest <= 90100 || all.len() == before {
            break;
        }
        time = dec_minute(&format!("{earliest:06}"));
    }
    Ok(all.into_values().collect())
}

/// 한 분의 정규화 bar.
#[derive(Clone, Copy)]
struct Bar {
    t: u32,       // 자정기준 분
    close: f64,
    vol: f64,     // 분 거래량
    cum_turn: f64, // 누적 거래대금 (VWAP 분자)
}

/// 그날 한 종목의 분봉 시퀀스(시간순). 09:00(540)~15:30(930) 정규장만.
fn to_bars(candles: &[MinuteCandle]) -> Vec<Bar> {
    let mut bt: BTreeMap<u32, Bar> = BTreeMap::new();
    for c in candles {
        let t = mins(&c.stck_cntg_hour);
        if !(540..=930).contains(&t) {
            continue;
        }
        let close = num(&c.stck_prpr);
        if close <= 0.0 {
            continue;
        }
        bt.insert(
            t,
            Bar {
                t,
                close,
                vol: num(&c.cntg_vol),
                cum_turn: num(&c.acml_tr_pbmn),
            },
        );
    }
    bt.into_values().collect()
}

/// 신호 방향.
#[derive(Clone, Copy, PartialEq)]
enum Dir {
    Long,
    Short,
    None,
}

/// 분류기: bars + 인덱스 i + 그날 PreCalc → 방향.
type Classifier = Box<dyn Fn(&[Bar], usize, &PreCalc) -> Dir>;

/// 신호 정의 — (이름, side, 분류 클로저). 클로저는 인덱스 i의 방향 반환.
/// i는 bars의 위치. 고정 a-priori 임계, 튜닝 없음.
struct Signal {
    name: &'static str,
    side: Dir, // 테스트할 쪽(Long/Short). 같은 신호를 양쪽 따로 등록.
    classify: Classifier,
}

/// 그날 미리 계산해 두는 보조값(OR-high/low 등).
struct PreCalc {
    or_high: f64,
    or_low: f64,
    or_ready_from: u32, // 시가범위 확정 시각(분) — 첫 30분 후
}

fn precalc(bars: &[Bar]) -> PreCalc {
    // 시가범위 = 첫 30분(09:00~09:30, t in 540..=569) high/low.
    let mut hi = f64::MIN;
    let mut lo = f64::MAX;
    for b in bars.iter().filter(|b| (540..=569).contains(&b.t)) {
        hi = hi.max(b.close);
        lo = lo.min(b.close);
    }
    PreCalc {
        or_high: if hi == f64::MIN { 0.0 } else { hi },
        or_low: if lo == f64::MAX { 0.0 } else { lo },
        or_ready_from: 570, // 09:30
    }
}

/// 신호 세트 구성(고정 임계).
fn build_signals() -> Vec<Signal> {
    let d = 0.003_f64; // VWAP 편차 0.3%
    let m = 0.003_f64; // 모멘텀 0.3%
    let r = 10usize; // 모멘텀 trailing 분
    let surge_x = 3.0_f64; // 거래량 급증 배수
    let surge_w = 20usize; // 급증 비교 윈도우(분)

    let mut sigs: Vec<Signal> = Vec::new();

    // VWAP 회귀: dev=(close−VWAP)/VWAP. long if dev<−d (가격 < VWAP → 반등), short if dev>+d.
    for side in [Dir::Long, Dir::Short] {
        sigs.push(Signal {
            name: "vwap_revert",
            side,
            classify: Box::new(move |bars, i, _pre| {
                // VWAP는 호출부에서 cum_vol 필요 → 클로저 밖에서 못 봄. 대신 close vs
                // running-turnover/running-vol을 여기서 재계산.
                let mut tv = 0.0;
                // cum_vol을 다시 구하기보다, bars[i].cum_turn / running vol.
                // running vol은 0..=i 합.
                for b in &bars[..=i] {
                    tv += b.vol;
                }
                let vwap = if tv > 0.0 && bars[i].cum_turn > 0.0 {
                    bars[i].cum_turn / tv
                } else {
                    bars[i].close
                };
                if vwap <= 0.0 {
                    return Dir::None;
                }
                let dev = (bars[i].close - vwap) / vwap;
                if dev < -d {
                    Dir::Long
                } else if dev > d {
                    Dir::Short
                } else {
                    Dir::None
                }
            }),
        });
    }

    // 모멘텀(연속): trailing R분 수익 ret. long if ret>+m, short if ret<−m.
    for side in [Dir::Long, Dir::Short] {
        sigs.push(Signal {
            name: "momentum",
            side,
            classify: Box::new(move |bars, i, _pre| {
                if i < r {
                    return Dir::None;
                }
                let p0 = bars[i - r].close;
                if p0 <= 0.0 {
                    return Dir::None;
                }
                let ret = bars[i].close / p0 - 1.0;
                if ret > m {
                    Dir::Long
                } else if ret < -m {
                    Dir::Short
                } else {
                    Dir::None
                }
            }),
        });
    }

    // 거래량 급증 — vol/trailing-20avg > X. 방향 = 그 분 수익 부호. follow와 fade 둘 다.
    // follow: 가격↑급증 → long, 가격↓급증 → short.
    for side in [Dir::Long, Dir::Short] {
        sigs.push(Signal {
            name: "vsurge_follow",
            side,
            classify: Box::new(move |bars, i, _pre| {
                if i < surge_w || i == 0 {
                    return Dir::None;
                }
                let avg = bars[i - surge_w..i].iter().map(|b| b.vol).sum::<f64>() / surge_w as f64;
                if avg <= 0.0 || bars[i].vol / avg < surge_x {
                    return Dir::None;
                }
                let pr = bars[i - 1].close;
                if pr <= 0.0 {
                    return Dir::None;
                }
                let ret = bars[i].close / pr - 1.0;
                if ret > 0.0 {
                    Dir::Long
                } else if ret < 0.0 {
                    Dir::Short
                } else {
                    Dir::None
                }
            }),
        });
    }
    // fade: 가격↑급증 → short(역추세), 가격↓급증 → long.
    for side in [Dir::Long, Dir::Short] {
        sigs.push(Signal {
            name: "vsurge_fade",
            side,
            classify: Box::new(move |bars, i, _pre| {
                if i < surge_w || i == 0 {
                    return Dir::None;
                }
                let avg = bars[i - surge_w..i].iter().map(|b| b.vol).sum::<f64>() / surge_w as f64;
                if avg <= 0.0 || bars[i].vol / avg < surge_x {
                    return Dir::None;
                }
                let pr = bars[i - 1].close;
                if pr <= 0.0 {
                    return Dir::None;
                }
                let ret = bars[i].close / pr - 1.0;
                if ret > 0.0 {
                    Dir::Short
                } else if ret < 0.0 {
                    Dir::Long
                } else {
                    Dir::None
                }
            }),
        });
    }

    // 시가범위 돌파 — 첫 30분 후 OR-high 상향돌파 시 long, OR-low 하향돌파 시 short.
    // 첫 교차 이벤트만(close[i]>OR && close[i-1]<=OR) — 매분 above 중복 방지.
    for side in [Dir::Long, Dir::Short] {
        sigs.push(Signal {
            name: "or_breakout",
            side,
            classify: Box::new(move |bars, i, pre| {
                if i == 0 || bars[i].t < pre.or_ready_from || pre.or_high <= 0.0 {
                    return Dir::None;
                }
                let (c, p) = (bars[i].close, bars[i - 1].close);
                if c > pre.or_high && p <= pre.or_high {
                    Dir::Long
                } else if c < pre.or_low && p >= pre.or_low {
                    Dir::Short
                } else {
                    Dir::None
                }
            }),
        });
    }

    sigs
}

fn mean(xs: &[f64]) -> f64 {
    if xs.is_empty() {
        0.0
    } else {
        xs.iter().sum::<f64>() / xs.len() as f64
    }
}

/// 한 종목·한 날의 분봉에서, 주어진 H·lag로 모든 분의 forward gross fwd return을 반환.
/// 인덱스 i의 진입은 t+lag(=i+lag), 청산은 i+lag+H. 인덱스=분 가정(연속 1분봉).
/// 분봉에 결손이 있을 수 있어 인덱스가 아닌 **시각(t)** 기준 lookup 사용.
fn fwd_ret(bars: &[Bar], idx: usize, lag: u32, h: u32) -> Option<f64> {
    let t = bars[idx].t;
    let ent_t = t + lag;
    let exit_t = ent_t + h;
    if exit_t > 930 {
        return None;
    }
    let ent = price_at_or_after(bars, ent_t)?;
    let exit = price_at_or_after(bars, exit_t)?;
    if ent <= 0.0 {
        return None;
    }
    Some(exit / ent - 1.0)
}

/// 시각 tt 이상에서 가장 가까운 bar의 close(결손분 robust). 없으면 None.
fn price_at_or_after(bars: &[Bar], tt: u32) -> Option<f64> {
    bars.iter().find(|b| b.t >= tt).map(|b| b.close)
}

/// 누적 결과: 한 (name×signal×side×H) 셀.
#[derive(Default)]
struct Cell {
    day_edges: Vec<f64>,   // 트리거 있는 날의 일별 edge (signal_gross − baseline_gross)
    sig_net: Vec<f64>,     // 트리거분 net fwd (per-trigger, net of cost) — 평균표시용
    sig_gross: Vec<f64>,   // 트리거분 gross fwd
    baselines: Vec<f64>,   // 트리거 있는 날의 baseline(gross) — 평균표시용
    n_triggers: usize,
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let codes: Vec<(String, String)> = {
        let raw = env_or("DAY_CODES", "005930,000660");
        raw.split(',')
            .map(|c| {
                let c = c.trim().to_string();
                let nm = match c.as_str() {
                    "005930" => "삼성전자",
                    "000660" => "SK하이닉스",
                    _ => "?",
                };
                (c, nm.to_string())
            })
            .collect()
    };
    let lag: u32 = env_or("DAY_ENTRY_LAG", "1").parse().unwrap_or(1);
    let cost_bps: f64 = env_or("DAY_COST_BPS", "35").parse().unwrap_or(35.0);
    let cost_frac = cost_bps / 10000.0;
    let hs = [5u32, 15, 30];

    // 날짜: DAY_DATES 명시 또는 DAY_END에서 DAY_N개 평일 역산.
    let dates: Vec<String> = if let Ok(d) = std::env::var("DAY_DATES") {
        d.split(',').map(|s| s.trim().to_string()).collect()
    } else {
        let end = env_or("DAY_END", "20260602");
        let n: usize = env_or("DAY_N", "25").parse().unwrap_or(25);
        let mut d = NaiveDate::parse_from_str(&end, "%Y%m%d")?;
        let mut out = Vec::new();
        while out.len() < n {
            if !matches!(d.weekday(), Weekday::Sat | Weekday::Sun) {
                out.push(d.format("%Y%m%d").to_string());
            }
            d -= Duration::days(1);
        }
        out.reverse();
        out
    };

    eprintln!(
        "daytrade_check | codes={:?} dates={}..{} (n={}) lag={lag}min cost={cost_bps}bps H={:?}",
        codes.iter().map(|(c, _)| c.as_str()).collect::<Vec<_>>(),
        dates.first().cloned().unwrap_or_default(),
        dates.last().cloned().unwrap_or_default(),
        dates.len(),
        hs
    );

    let kis = KisClient::new(KisConfig::from_env()?)?;

    // cells[(code, signame, side, h)] — side를 문자열로.
    type Key = (String, &'static str, &'static str, u32);
    let mut cells: BTreeMap<Key, Cell> = BTreeMap::new();
    // baseline 일별 시계열(name×H) — 참고용 전체평균.
    let signals = build_signals();

    // 종목별 실제 사용일수 추적.
    let mut usable_days: BTreeMap<String, usize> = BTreeMap::new();

    for (code, nm) in &codes {
        for date in &dates {
            let candles = match full_day(&kis, code, date).await {
                Ok(c) => c,
                Err(e) => {
                    eprintln!("  {nm}({code}) {date}: fetch err {e} — skip");
                    continue;
                }
            };
            let bars = to_bars(&candles);
            eprintln!("  {nm}({code}) {date}: {} bars", bars.len());
            if bars.len() < 200 {
                // 휴장/결손 — 정규장 ~370분 기대. skip.
                continue;
            }
            *usable_days.entry(code.clone()).or_default() += 1;

            let pre = precalc(&bars);

            for h in hs {
                // 그날 baseline(gross) = 모든 분의 fwd 평균(진입가능한 분만).
                let all_fwd: Vec<f64> = (0..bars.len())
                    .filter_map(|i| fwd_ret(&bars, i, lag, h))
                    .collect();
                if all_fwd.is_empty() {
                    continue;
                }
                let baseline = mean(&all_fwd);

                for sig in &signals {
                    // 그날 이 신호·이 side로 트리거된 분의 gross fwd 모으기.
                    let mut trig_gross = Vec::new();
                    for i in 0..bars.len() {
                        if (sig.classify)(&bars, i, &pre) != sig.side {
                            continue;
                        }
                        if let Some(g) = fwd_ret(&bars, i, lag, h) {
                            trig_gross.push(g);
                        }
                    }
                    if trig_gross.is_empty() {
                        continue;
                    }
                    let sig_g = mean(&trig_gross);
                    let side_str = if sig.side == Dir::Long { "L" } else { "S" };
                    let key: Key = (code.clone(), sig.name, side_str, h);
                    let cell = cells.entry(key).or_default();
                    // 일별 edge = signal_gross − baseline_gross (이 날). cost는 표시 단계서.
                    cell.day_edges.push(sig_g - baseline);
                    cell.baselines.push(baseline);
                    cell.sig_gross.push(sig_g);
                    cell.sig_net.push(sig_g - cost_frac);
                    cell.n_triggers += trig_gross.len();
                }
            }
        }
    }

    // ── 결과 출력 ────────────────────────────────────────────────────────────
    println!("\n=== usable days per name (full-session bars≥200) ===");
    for (code, nm) in &codes {
        println!("  {nm}({code}): {} days", usable_days.get(code).copied().unwrap_or(0));
    }

    println!(
        "\n=== single-name intraday signal edge (net of {cost_bps}bps round-trip, day-series t) ==="
    );
    println!("baseline=무조건 intraday drift(gross). edge=(signal_gross−baseline_gross)−cost.");
    println!("의미: edge>0 & |t|>~2 (drift 이김) AND signal_net>0 (비용후 거래가능) 둘 다.\n");
    println!(
        "{:<10} {:<14} {:>4} {:>3} {:>6} {:>6} {:>9} {:>9} {:>9} {:>7} {:>6}",
        "name", "signal", "side", "H", "ndays", "ntrig", "sig_net%", "base%", "edge%", "t", "win%"
    );

    // name 묶음 출력 위해 codes 순서대로.
    for (code, nm) in &codes {
        for sig in &signals {
            let side_str = if sig.side == Dir::Long { "L" } else { "S" };
            for h in hs {
                let key: Key = (code.clone(), sig.name, side_str, h);
                let Some(cell) = cells.get(&key) else {
                    continue;
                };
                let n = cell.day_edges.len();
                if n == 0 {
                    continue;
                }
                let m = mean(&cell.day_edges); // 일별 edge 평균(gross 차)
                let sd = if n > 1 {
                    (cell.day_edges.iter().map(|x| (x - m).powi(2)).sum::<f64>()
                        / (n as f64 - 1.0))
                        .sqrt()
                } else {
                    0.0
                };
                let t_stat = if sd > 0.0 {
                    m / (sd / (n as f64).sqrt())
                } else {
                    0.0
                };
                // edge%(net) = (signal_gross−baseline_gross 평균) − cost.
                let edge_net = (m - cost_frac) * 100.0;
                let sig_net = mean(&cell.sig_net) * 100.0;
                let base = mean(&cell.baselines) * 100.0;
                // win% = 일별 edge>0 비율(트리거 있는 날 중).
                let win = cell.day_edges.iter().filter(|x| **x > 0.0).count() as f64 / n as f64
                    * 100.0;
                let flag = if n < 10 { " <10d!" } else { "" };
                println!(
                    "{:<10} {:<14} {:>4} {:>3} {:>6} {:>6} {:>9.4} {:>9.4} {:>9.4} {:>7.2} {:>6.1}{flag}",
                    nm, sig.name, side_str, h, n, cell.n_triggers, sig_net, base, edge_net, t_stat, win
                );
            }
        }
    }

    println!(
        "\n— 정직 caveat —\n\
         · 삼성전자=초효율 → 비용차감 후 신호≈0 기대. 하닉 변동성↑로 미세하게 여지.\n\
         · 1분봉은 약한 가격/거래량 신호만. 진짜 인트라데이 엣지(호가 imbalance, 체결강도)는\n\
           WS 전용 → 이 데이터에 없음. 라이브 forward-test로만 검증 가능.\n\
         · 단일 regime(최근 수주), baseline으로 drift 통제했으나 regime-bound.\n\
         · effective N=#days(작음). 분단위 t는 자기상관으로 폭증 → 일단위만 신뢰.\n\
         · 다중비교: 2종목×다신호×양side×3H ≈ 수십 셀 → t>2 1~2개는 우연. H그리드 전반에\n\
           일관된 (name,signal,side)만 진짜 hit. 단발 t>2는 노이즈.\n\
         · n_days<10 셀(<10d! 표시)은 부호·t 무관하게 통계적 무의미.\n\
         · 한국 공매도는 2025-03-31까지 금지[High]·인트라데이 제약 → short side는 실행 난도↑.\n"
    );

    Ok(())
}
