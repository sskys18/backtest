# Session Handoff
> Generated: 2026-06-04 (KST) — 엔진 재설계(Approach A) + data-engine cleanup 세션

## Task
**Superior backtest engine 설계·구축** — KOSPI 메이저 long-short on Hyperliquid `xyz` perp
(삼전/하닉 vs NVDA/MU/TSM) + cross-sectional 섹터/종목 로테이션. 현 `bt-engine`은 long-only·
단일종목이라 표현 불가. 설계 = `docs/specs/2026-06-04-kr-perp-portfolio-engine-design.md`.

## Status
### Completed — 이번 세션
- **설계 스펙 확정 (Approach A)**: panel-vectorized weight-matrix 시그널 + event-driven 멀티에셋
  포트폴리오 엔진. self-review + **Codex spec-review** 통과(7 findings 반영). 커밋 `276166d`.
- **data-engine 제거 → import-only** (커밋 `2c176a4`): `src/bt/data/sources/`(11 어댑터)·
  `fetch.py`·`collect_liq.py`·`bt data fetch`·`bt collect-liq`·관련 테스트 9개·
  `exchange-adapters/httpx/websockets` deps 삭제. 유지 = import/store/validate/schemas + 양 엔진.
  `scripts/build_btc_scanner_features.py` import-only로 strip(liq zero-fill).
- 문서 동기화(CLAUDE.md/architecture.md/data-coverage.md = import-only), `.gitignore`에
  `.claude/`·`.playwright-mcp/`·`replay_final.json` 추가.
- **검증**: `cargo test -p bt-engine` 28 pass / `pytest` 64 pass.

### In Progress
- 없음. 스펙이 **유저 sign-off 대기** 상태.

## Resume Here
1. **스펙 sign-off 받기** → 받으면 즉시 `writing-plans` 스킬로 P0–P4 실행계획 생성.
   **계획 승인 전 엔진 코드 작성 금지**(brainstorming HARD-GATE). 스펙 §10 phase 순서:
   P0 panel loader/session tagger/funding reader(import-only) → P1 Rust `PortfolioEngine`
   + **2D PyO3 binding**(현 `python.rs`는 1D only) → P2 `PanelStrategy` API + pairs/CS-rotation
   레퍼런스 2종 → P3 walk-forward(purged CV + DSR/PBO) → P4 CLI `bt backtest-panel`.
2. 각 phase 끝 = `cargo test -p bt-engine` + `pytest` green 유지(크립토/PPO 경로 무손상).

## Decisions (do NOT revisit)
- **Approach A 확정**: hybrid(vector panel 시그널 + event exec). B(per-asset 후합산) 기각=pairs/CS 불가,
  C(풀 event 재작성) 기각=벡터속도·PPO경로 파괴·extend-in-place 위반.
- **extend bt-engine = ADDITIVE**: `portfolio_engine.rs`/`run_panel()` 신규. `tick()`/`run()`/
  `StepEngine`(PPO) 불변. **perp 회계는 spot 특수케이스 아님** — signed+collateral 독립 경로(Codex 지적).
- **data fetching 이 repo서 제거** — siblings(korea-stock/upbit) 소유. 여긴 `bt data import`만.
- **TradingAgents(LLM 멀티에이전트) 엔진으로 기각** (스펙 §13): 최근 KR 데이터 백테스트=training leakage로
  가짜 알파. 허용 범위=forward-only thesis 레이어(DART+뉴스→방향성 bias), 절대 백테스트 대상 아님.
- **시스템 알파 없음=확정**(12각도 OOS 생존 0). 엣지=thesis+포지셔닝(베타). 이 엔진은 *long-short/로테이션*
  구조를 정직하게 백테스트하려는 것 — 같은 12신호·같은 레짐 재탐색 금지(과적합).
- **KR-perp 데이터 = HL `xyz` DEX 105일**(바이낸스 2일 아님). 펀딩 HL ~23%/yr.
- **시간중립 필수** — 오버나잇27.7h vs 장중6.0h 비대칭. Sharpe는 exposure-hour 가중(스펙 §8 공식).

## Gotchas
- **coinank.py 삭제됨** — 이전 핸드오프의 "CoinAnk env 키" gotcha 무효. 키 갱신 필요시 sibling에서.
- **PyO3 현재 1D array만** (`crates/bt-engine/src/python.rs`) — panel은 2D binding 신규 필요(P1).
- **현 spot/cash 엔진은 perp 회계 베이스 재사용 불가** — signed qty/마진/펀딩 독립 구현.
- **korea-stock 경로**: `kis_adapter::domestic::kis::{...}` + `kis_adapter::global::{binance,hyperliquid}`.
  kis-radar bin들(dashboard 등) 구 경로라 깨짐(무관 — 이번 엔진작업과 별개).
- bin 전부 `required-features=["edgecheck"]` 게이팅. `experiments/perp/*.json` gitignore.
- **`add -A` 주의** — `.claude/`·`.playwright-mcp/` junk 휩쓸림(이제 gitignore됨).

## Context
- **Branch**: main, origin = github.com/sskys18/backtest (private). HEAD **`2c176a4`**.
- **Tests**: cargo bt-engine 28/28, pytest 64/64. venv = `.venv`(py3.14), `maturin develop`로 .so 빌드됨.
- **메모리**: 프로젝트 decision 레이어에 Approach A 기록됨(`sspower-mem`). `kr-tech-alpha-findings.md`=12각도 봉인.
- **Unknowns**(verify 전 가정 금지): HL `xyz` 유동성/존속(6에셋×105일 = OOS 표본 빈약, DSR/PBO 필수);
  KIS 분봉 시맨틱(단일콜 풀세션); panel parquet 실제 스키마는 sibling 산출물 확인 후 확정.
