//! 포지셔널(주/월) 스마트머니 누적순매수 → forward 아웃퍼폼 횡단면 검증 — go/no-go.
//!
//! 가설: "지난 N거래일 동안 스마트머니(외국인+기관) 순매수 누적이 큰 종목"이
//! forward M거래일 동안 유니버스를 아웃퍼폼하는가? (intraday 검증은 null이었음 — 그 포지셔널 짝.)
//!
//! 방법론(edge_check.rs 규율 + advisor 제약 반영):
//! - **랭크/부호 테스트, 파라미터 피팅 없음** → OOS by construction.
//!   리밸런스일마다 유니버스를 신호 랭크로 top-half / bottom-half 분할,
//!   excess = mean(top fwd ret) − mean(bottom fwd ret) (유니버스 내 시장중립).
//! - (N,M) 그리드는 **최적화가 아니라 robustness 표시** — 한 칸만 빛나면 과적합 의심.
//! - **신호 단위 = 거래대금(KRW)** `frgn_ntby_tr_pbmn + orgn_ntby_tr_pbmn`.
//!   순매수 수량은 종목별 주가가 달라 횡단면 비교 무의미 → 자본흐름(KRW)이 옳은 측도.
//! - **날짜로 조회, 배열 인덱스로 절대 안함** — KIS는 newest-first 반환, 종목별 결측일 독립
//!   → 공통 날짜축 위에서 by-date 룩업. 누적신호 = 윈도 내 일별 net 합(필드는 일별 net).
//! - **size confound 로버스트니스 컬럼**: raw 누적 net-KRW는 대형주일수록 절대 KRW가 커
//!   "top-half ≈ 대형주" size proxy일 수 있음. 2차 신호 = net / 자기 거래대금(turnover)으로
//!   정규화한 랭크. raw 결과가 정규화 후에도 살아남으면 진짜 수급 신호, 무너지면 size였던 것.
//!
//! 환경변수: KIS 크레덴셜(KisConfig::from_env). `POS_CODES`(콤마, 기본 테크 코호트),
//! `POS_ASOF`(YYYYMMDD 기준일, 기본 오늘), `POS_STEP`(리밸런스 간격 거래일, 기본 5),
//! `POS_MKT`(브로드 모드: STK/KSQ — KRX 전종목 거래대금 top-N으로 유니버스 대체),
//! `POS_TOPN`(브로드 모드 N, 기본 30).

use std::collections::{BTreeMap, BTreeSet, HashMap};

use chrono::{Duration, Local, NaiveDate};
use kis_adapter::kis::external::KrxClient;
use kis_adapter::{KisClient, KisConfig, Market};

/// 콤마 문자열 → f64 (부호 보존).
fn num(s: &str) -> f64 {
    s.chars()
        .filter(|c| *c != ',' && !c.is_whitespace())
        .collect::<String>()
        .parse()
        .unwrap_or(0.0)
}

fn env_or(k: &str, d: &str) -> String {
    std::env::var(k).unwrap_or_else(|_| d.to_string())
}

/// 한 종목의 일별 시계열: 날짜(YYYYMMDD) → (smart_net_KRW, close, turnover_KRW).
type Daily = BTreeMap<String, (f64, f64, f64)>;

/// 테크 코호트(하드코딩 기본) — 핸드픽 winners(레벨은 survivorship-biased, 상대 spread는 덜).
const DEFAULT_COHORT: &str =
    "005930,000660,066570,011070,009150,001820,034220,064400,018260,307950,035420,181710,036570,454910";

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let asof = env_or("POS_ASOF", &Local::now().format("%Y%m%d").to_string());
    let start = env_or("POS_START", "20240101"); // 스티칭 백워드 목표 시작(레짐 커버).
    let step: usize = env_or("POS_STEP", "5").parse().unwrap_or(5);
    let mkt_broad = std::env::var("POS_MKT").ok();
    let topn: usize = env_or("POS_TOPN", "30").parse().unwrap_or(30);

    let kis = KisClient::new(KisConfig::from_env()?)?;

    // 유니버스 결정.
    let codes: Vec<String> = match &mkt_broad {
        Some(mkt) => {
            // 브로드 모드 — KRX 전종목 거래대금 top-N(비교용).
            eprintln!("broad mode: KRX {mkt} top-{topn} by 거래대금 @ {asof}");
            let krx = KrxClient::from_env().map_err(|e| format!("KRX_ID/KRX_PW: {e}"))?;
            let mut rows = krx.all_stock_ohlcv(&asof, mkt).await?;
            if rows.is_empty() {
                return Err("KRX 빈 응답 — POS_ASOF 거래일 확인".into());
            }
            rows.sort_by(|a, b| num(&b.trade_value).total_cmp(&num(&a.trade_value)));
            rows.truncate(topn);
            rows.iter().map(|r| r.code.clone()).collect()
        }
        None => env_or("POS_CODES", DEFAULT_COHORT)
            .split(',')
            .map(|c| c.trim().to_string())
            .filter(|c| !c.is_empty())
            .collect(),
    };
    eprintln!("universe: {} codes, asof={asof}, step={step}", codes.len());

    // 종목별 일별 시계열 수집 — investor_trend_daily_all은 asof로부터 ~30거래일만 반환(KIS TR cap).
    // 멀티-주(주/월) history가 필요하므로 asof를 백워드로 스티칭: 각 윈도의 가장 이른 날 −1일을
    // 다음 asof로, start까지 또는 진전 없을 때까지 반복. BTreeMap이 날짜 dedup.
    const MAX_WINDOWS: usize = 30; // 윈도×30거래일 ≈ 백스톱.
    let mut series: HashMap<String, Daily> = HashMap::new();
    let mut total_calls = 0usize;
    for code in &codes {
        let mut daily: Daily = BTreeMap::new();
        let mut cursor = asof.clone();
        let mut prev_earliest = String::new();
        for _ in 0..MAX_WINDOWS {
            total_calls += 1;
            let days = match kis
                .domestic_stock()
                .investor_trend_daily_all(code, &cursor, Market::Krx)
                .await
            {
                Ok((_summary, days)) => days,
                Err(e) => {
                    eprintln!("  {code} @ {cursor}: error — {e}");
                    break;
                }
            };
            if days.is_empty() {
                break;
            }
            let mut earliest = String::new();
            for d in &days {
                let close = num(&d.stck_clpr);
                if d.stck_bsop_date.is_empty() || close <= 0.0 {
                    continue;
                }
                // 스마트머니 net = 외국인 + 기관 (거래대금, KRW).
                let smart_net = num(&d.frgn_ntby_tr_pbmn) + num(&d.orgn_ntby_tr_pbmn);
                let turnover = num(&d.acml_tr_pbmn); // 그날 총거래대금(정규화 분모).
                daily.insert(d.stck_bsop_date.clone(), (smart_net, close, turnover));
                if earliest.is_empty() || d.stck_bsop_date < earliest {
                    earliest = d.stck_bsop_date.clone();
                }
            }
            // 진전 가드 — 가장 이른 날이 줄지 않으면 무한루프 방지로 중단.
            if earliest.is_empty() {
                break;
            }
            if !prev_earliest.is_empty() && earliest >= prev_earliest {
                break;
            }
            // start 도달 시 중단.
            if earliest.as_str() <= start.as_str() {
                break;
            }
            prev_earliest = earliest.clone();
            // 다음 asof = earliest − 1 calendar day.
            cursor = match NaiveDate::parse_from_str(&earliest, "%Y%m%d") {
                Ok(d) => (d - Duration::days(1)).format("%Y%m%d").to_string(),
                Err(_) => break,
            };
            // throttle — KIS rate limit 보호.
            tokio::time::sleep(std::time::Duration::from_millis(120)).await;
        }
        if let (Some((mn, _)), Some((mx, _))) = (daily.iter().next(), daily.iter().next_back()) {
            eprintln!("  {code}: {} days [{mn}..{mx}]", daily.len());
        }
        if !daily.is_empty() {
            series.insert(code.clone(), daily);
        }
    }
    eprintln!("total investor-trend calls: {total_calls}");
    if series.len() < 4 {
        return Err("유니버스 < 4 종목 수집 — top/bottom 분할 무의미. 크레덴셜/날짜 확인".into());
    }

    // 가격조정 진단 — stck_clpr은 FID_ORG_ADJ_PRC="" → 미조정(unadjusted) 추정.
    // 분할/대규모 배당이면 1일 ±extreme 가짜 수익이 M=40 윈도에 들어가 excess 왜곡.
    // 콜 추가 없이 메모리 내 종가 day-over-day로 최대 1일 변동 스캔, >30%면 플래그.
    {
        eprintln!("\n--- price-adjustment diagnostic (max |1-day move| per stock) ---");
        let mut flagged = 0;
        for code in &codes {
            let Some(daily) = series.get(code) else {
                continue;
            };
            let rows: Vec<(&String, f64)> = daily.iter().map(|(d, (_, c, _))| (d, *c)).collect();
            let mut max_mv = 0.0f64;
            let mut max_at: Option<(String, f64, String, f64)> = None; // (d0,c0,d1,c1)
            for w in rows.windows(2) {
                let ((d0, c0), (d1, c1)) = (&w[0], &w[1]);
                if *c0 > 0.0 {
                    let mv = (c1 / c0 - 1.0).abs();
                    if mv > max_mv {
                        max_mv = mv;
                        max_at = Some(((*d0).clone(), *c0, (*d1).clone(), *c1));
                    }
                }
            }
            // 분할은 깨끗한 비율(/2,/5)·큰 음수, 한도가(±30.0%)는 실거래 — 날짜·종가로 구분.
            let mut detail = String::new();
            let mark = if max_mv > 0.30 {
                flagged += 1;
                if let Some((d0, c0, d1, c1)) = &max_at {
                    let signed = c1 / c0 - 1.0;
                    detail = format!(" [{d0} {c0:.0} → {d1} {c1:.0} = {:+.1}%]", signed * 100.0);
                }
                "  <-- >30% — 분할 의심? (한도가 ±30.0%면 실거래)"
            } else {
                ""
            };
            eprintln!("  {code}: max 1-day move {:.1}%{mark}{detail}", max_mv * 100.0);
        }
        eprintln!(
            "  → {flagged} stock(s) with >30% single-day move. 0 = unadjusted close 안전(분할 없음).\n"
        );
    }

    // 공통 날짜축 — 모든 종목 날짜의 합집합(정렬). by-date 룩업, 인덱스 desync 방지.
    let axis: Vec<String> = {
        let mut s: BTreeSet<String> = BTreeSet::new();
        for d in series.values() {
            s.extend(d.keys().cloned());
        }
        s.into_iter().collect()
    };
    eprintln!(
        "common date axis: {} trading days [{}..{}]\n",
        axis.len(),
        axis.first().map(String::as_str).unwrap_or("?"),
        axis.last().map(String::as_str).unwrap_or("?"),
    );

    // 결과 헤더.
    println!("=== POSITIONAL smart-money(외국인+기관) accumulation → forward outperformance ===");
    println!(
        "universe={} stocks  date range [{}..{}]  {} trading days  step={step}\n",
        series.len(),
        axis.first().map(String::as_str).unwrap_or("?"),
        axis.last().map(String::as_str).unwrap_or("?"),
        axis.len(),
    );

    let grid = [10usize, 20, 40];
    for (label, normalize) in [("RAW cumulative net-KRW", false), ("NORMALIZED net/turnover", true)] {
        println!("--- signal = {label} ---");
        println!(
            "{:>3} {:>3} {:>7} {:>6} {:>11} {:>11} {:>11} {:>7} {:>7}",
            "N", "M", "n_dates", "n_sto", "top%", "bottom%", "excess%", "t", "hit%"
        );
        for &n in &grid {
            for &m in &grid {
                let r = evaluate(&series, &axis, n, m, step, normalize);
                println!(
                    "{:>3} {:>3} {:>7} {:>6} {:>11.3} {:>11.3} {:>11.3} {:>7.2} {:>7.1}",
                    n,
                    m,
                    r.n_dates,
                    r.avg_stocks,
                    r.top * 100.0,
                    r.bottom * 100.0,
                    r.excess * 100.0,
                    r.t_stat,
                    r.hit * 100.0,
                );
            }
        }
        println!();
    }

    print_caveats(&axis);
    Ok(())
}

struct EvalResult {
    n_dates: usize,
    avg_stocks: usize,
    top: f64,
    bottom: f64,
    excess: f64,
    t_stat: f64,
    hit: f64,
}

/// 횡단면 평가. 리밸런스일 idx i: 룩백 윈도 axis[i-N+1..=i], forward 날짜 axis[i+M].
/// 각 종목 신호 = 윈도 내 일별 smart_net 합 (normalize=true면 윈도 내 turnover 합으로 나눔).
/// 유니버스를 신호 랭크로 top-half/bottom-half 분할, excess = mean(top fwd) − mean(bottom fwd).
fn evaluate(
    series: &HashMap<String, Daily>,
    axis: &[String],
    n: usize,
    m: usize,
    step: usize,
    normalize: bool,
) -> EvalResult {
    let mut per_date_excess = Vec::new();
    let mut top_rets = Vec::new();
    let mut bottom_rets = Vec::new();
    let mut counts = Vec::new();

    if axis.len() <= n + m {
        return empty();
    }

    let mut i = n.saturating_sub(1);
    while i + m < axis.len() {
        let window = &axis[(i + 1 - n)..=i];
        let d_now = &axis[i];
        let d_fwd = &axis[i + m];

        // 이 날 평가 가능한 종목: 윈도 신호 계산 가능 + now/fwd close 존재.
        let mut entries: Vec<(f64, f64)> = Vec::new(); // (signal, fwd_ret)
        for daily in series.values() {
            // 진입/청산 종가.
            let Some(&(_, close_now, _)) = daily.get(d_now) else {
                continue;
            };
            let Some(&(_, close_fwd, _)) = daily.get(d_fwd) else {
                continue;
            };
            if close_now <= 0.0 || close_fwd <= 0.0 {
                continue;
            }
            // 윈도 누적 신호 — by-date 합. 윈도 내 결측일은 0 기여(보유 그 종목).
            let mut net_sum = 0.0;
            let mut turn_sum = 0.0;
            let mut have = 0usize;
            for d in window {
                if let Some(&(net, _, turn)) = daily.get(d) {
                    net_sum += net;
                    turn_sum += turn;
                    have += 1;
                }
            }
            // 윈도 내 데이터 최소 절반 요구(앙상한 종목 제외).
            if have * 2 < n {
                continue;
            }
            let signal = if normalize {
                if turn_sum > 0.0 {
                    net_sum / turn_sum
                } else {
                    continue;
                }
            } else {
                net_sum
            };
            let fwd_ret = close_fwd / close_now - 1.0;
            entries.push((signal, fwd_ret));
        }

        // top/bottom 분할은 최소 4종목 필요.
        if entries.len() >= 4 {
            entries.sort_by(|a, b| b.0.total_cmp(&a.0)); // 신호 내림차순.
            let half = entries.len() / 2;
            let top: Vec<f64> = entries[..half].iter().map(|e| e.1).collect();
            let bottom: Vec<f64> = entries[entries.len() - half..].iter().map(|e| e.1).collect();
            let t_mean = mean(&top);
            let b_mean = mean(&bottom);
            per_date_excess.push(t_mean - b_mean);
            top_rets.push(t_mean);
            bottom_rets.push(b_mean);
            counts.push(entries.len());
        }

        i += step;
    }

    let k = per_date_excess.len();
    if k == 0 {
        return empty();
    }
    let excess = mean(&per_date_excess);
    let sd = std(&per_date_excess, excess);
    let t_stat = if sd > 0.0 {
        excess / (sd / (k as f64).sqrt())
    } else {
        0.0
    };
    let hit = per_date_excess.iter().filter(|x| **x > 0.0).count() as f64 / k as f64;
    let avg_stocks = (counts.iter().sum::<usize>() as f64 / k as f64).round() as usize;

    EvalResult {
        n_dates: k,
        avg_stocks,
        top: mean(&top_rets),
        bottom: mean(&bottom_rets),
        excess,
        t_stat,
        hit,
    }
}

fn empty() -> EvalResult {
    EvalResult {
        n_dates: 0,
        avg_stocks: 0,
        top: 0.0,
        bottom: 0.0,
        excess: 0.0,
        t_stat: 0.0,
        hit: 0.0,
    }
}

fn mean(xs: &[f64]) -> f64 {
    if xs.is_empty() {
        0.0
    } else {
        xs.iter().sum::<f64>() / xs.len() as f64
    }
}

fn std(xs: &[f64], m: f64) -> f64 {
    if xs.len() < 2 {
        return 0.0;
    }
    let var = xs.iter().map(|x| (x - m).powi(2)).sum::<f64>() / (xs.len() as f64 - 1.0);
    var.sqrt()
}

fn print_caveats(axis: &[String]) {
    let range = format!(
        "{}..{}",
        axis.first().map(String::as_str).unwrap_or("?"),
        axis.last().map(String::as_str).unwrap_or("?"),
    );
    println!("read: RAW excess% > 0 with t > ~2 across MOST (N,M) cells = accumulation→continuation edge.");
    println!("      one bright cell only = overfit. flat/negative = signal dead.");
    println!("      NORMALIZED 컬럼이 RAW와 함께 살아남으면 진짜 수급, 무너지면 RAW는 size였던 것.\n");
    println!("CAVEATS (mandatory — apply before believing any number):");
    println!("  1. 단일 레짐: data window {range} ≈ 2024–26 AI/HBM 슈퍼사이클.");
    println!("     accumulation→continuation 양수는 부분적으로 레짐-bound/동어반복.");
    println!("  2. |t| OVERSTATED — 두 겹으로: (a) 횡단면 상관(집중 코호트일수록 심함) → effective N << n_dates,");
    println!("     (b) step=5 < M이면 연속 리밸런스가 forward 날짜를 공유 → 일별 excess series autocorrelated,");
    println!("     t-공식의 독립가정 위반. → |t|>2 노리지 말고 부호 일관성 + hit%로 판단.");
    println!("  3. 핸드픽 winners 코호트면 LEVEL은 survivorship-biased. top-vs-bottom 상대 spread는");
    println!("     덜 biased지만 여전히 winners 내부 비교.");
    println!("  4. size confound: RAW 누적 net-KRW 랭크는 대형주일수록 절대 KRW가 커 'top-half ≈ 대형주'");
    println!("     일 수 있음 — spread가 수급이 아니라 large-cap factor일 수 있음. NORMALIZED 컬럼으로 확인.");
    println!("  5. 거래비용 미반영(포지셔널 주간 리밸런스 → 작지만 0 아님).");
    println!("  6. D종가 진입 vs D 종료시 공표되는 수급 → ~1일 낙관(edge_check.rs와 동일 v1 가정).");
    println!("  7. stck_clpr은 미조정 종가 추정 — 위 진단에서 분할 아티팩트 0이면 무관.");
}
