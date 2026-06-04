# Session Handoff
> Generated: 2026-06-04 10:30 (KST)

## Task
KR 주식(삼성전자 005930 / SK하이닉스 000660 중심) 알파 탐색 — `korea-stock`(KIS+KRX+Binance 어댑터) 데이터를 `backtest/crates/kis-radar`(Rust, `--features edgecheck`)로 검증. 장세 = AI/HBM 슈퍼사이클·극단 집중(breadth 14%).

## Status
### Completed — 8각도 알파 탐색 전부 NULL (커밋됨)
검증기(전부 `crates/kis-radar/src/bin/`, `--features edgecheck`, 읽기전용):
- `edge_check`(일봉 횡단면 flow), `intraday_check`(1분봉 회전+velocity), `positional_check`(수급 누적 600일), `leadlag`(글로벌반도체→KR gap분해), `swing_check`(하루텀 OOS), `macro_signal`(매크로 롱+숏 OOS), `trend_overlay`(SMA), `gainers`(기간수익률+breadth), `flow_probe`(1분봉+가집계 관찰).
- 결론: 가격·거래량·수급·크로스마켓·매크로·방향성 **전부 거래가능 엣지 없음**(비용·OOS·다중검정 후). 상세 = 메모리 `kr-tech-alpha-findings.md` + `experiments/intraday_surge_findings.md`.

### 실용 산출물 (커밋됨)
- `dashboard` — KRX현물+수급·Binance perp(펀딩/베이시스)·글로벌반도체 한화면 상황인식.
- `divergence_alert` — **3종 경보**(분산/매집/대량유출입), 디바운스+텔레그램(env)+KST자동종료. release 빌드됨. launchd: `scripts/com.sskys.krdivergence.plist` + `scripts/run_divergence_alert.sh`. 라이브 검증 완료(🔴대량유출 발동 확인).

### In Progress
- 없음. 시스템 알파 탐색 종결.

## Resume Here
1. **(유저 액션) KIS 키 재발급**(노출 플래그) → `korea-stock/.env` 갱신 → 알림기 launchd 로드: `cp scripts/com.sskys.krdivergence.plist ~/Library/LaunchAgents/` 후 `launchctl load ~/Library/LaunchAgents/com.sskys.krdivergence.plist`. (선택) `.env`에 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` 추가 → 텔레그램 알림.
2. **Binance perp 유동성 모니터** — `dashboard`로 24h vol/호가 추적. perp(삼전/하닉, 2026-06-02 상장)이 두꺼워지고 펀딩 정상화되면 **숏 실행 venue로 재검토**(지금 L1 $7~48·펀딩+120%/yr = 사용불가).
3. **새 알파는 미탐색 각도만** — WS sub-minute 미시구조(호가/체결강도, 라이브 기록만) 또는 DART 이벤트. **같은 8신호를 같은 레짐에 재탐색 금지(과적합).**

## Decisions (do NOT revisit)
- **시스템 알파 없음 = 확정** — 8각도·~300셀·다중검정 보정 후 생존 0. 엣지 = thesis/판단 + 포지셔닝(베타), 시스템 신호 아님.
- **Binance = 숏 실행 venue, 알파 소스 아님** — KR 현물 숏 불가라 perp. (한때 "광맥"이라 본 것 철회.)
- **dashboard/divergence_alert = 판단 보조, 매수/매도 신호 아님.**
- **korea-stock 재구조화됨**: 패키지 `kis-adapter`→`korea-stock`, 도메인 `kis::` 하위(`kis_adapter::kis::domestic_stock`) + `toss`/`binance` 모듈. kis-radar dep = `kis-adapter = { package = "korea-stock", ... }`.

## Gotchas
- **⚠️ KIS 앱키 과거 세션 평문 노출** — 라이브 주문/무인가동 전 재발급 필수. 그래서 launchd 자동 load 안 함.
- korea-stock는 유저가 활발히 편집 중(binance WIP). `examples/binance_kr_{quote,order}.rs`는 stub로 채웠다 유저가 실제 example로 덮음. 매니페스트 깨지면 누락 example 확인.
- KIS 분봉 TR 단일콜 = 풀세션(~120봉,≈3분간격) 반환(부분 아님). 가집계(`investor_trend_estimate`)는 **시간대 버킷 2개만**(미세궤적 없음). `기관` 가집계 장중 0 자주.
- Binance KR-perp = USD建(mark/spot ~1476×), FX소스 없어 basis% 미산출.
- `CurrentPrice`엔 종목명 없음(`bstp_kor_isnm`=업종명). `prdy_ctrt`는 부호포함 → `prdy_vrss_sign`으로 정규화.
- bin은 전부 `[[bin]] required-features=["edgecheck"]` 게이팅 필요(없으면 default 빌드 깨짐).

## Context
- **Branch**: main (backtest + korea-stock 둘다).
- **Tests/빌드**: kis-radar 엔진 단위 7 통과; 전 bin `cargo clippy --features edgecheck -- -D warnings` 클린. `cargo test`(default)도 통과.
- **실행**: `cargo run -p kis-radar --features edgecheck --bin <name>` + `set -a; . korea-stock/.env; set +a`.
- **다른 파킹 스레드**: 크립토 스캐너 OOS 검증(2026-05-04, `src/bt/scanners/tune.py`) — git log 참조. 이번 세션과 무관.
- **Unknowns**: Binance perp 향후 유동성/펀딩 추이; 환경의 KIS 데이터 시맨틱(분봉이 풀세션 반환되는 등).
