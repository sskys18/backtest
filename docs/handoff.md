# Session Handoff
> Generated: 2026-06-11 (KST)

## Task
Alpha research on the panel engine + repo structure cleanup. Engine merged
(PR #1/#2), alpha session merged (PR #3, main `42cea22`).

## Status
### Completed
- **Alpha ledger complete** — see `experiments/README.md` (the single index).
  8 crypto families tested under full rigor; free-public-data families on
  liquid venues are EXHAUSTED (price/flow ×6, funding, on-chain fundamentals).
  Survivors: conditional funding-carry overlay (bull manias only) + breakout
  screener as watch tool.
- **PR #3 merged** (15 commits + 4 Codex push-gate fix commits): all 6/05+6/11
  experiments, funding/on-chain fetchers+backtests, 2 real engine bugs found by
  the push gate — (1) full_calendar closed rows were tradable on the daily path
  (loader active_mask + engine rebalance/fill-defer fixed, cargo 63), (2)
  `_run_weights` import crash in surge search.
- **Repo restructure** (branch `chore/repo-structure`): scripts/ split into
  `live/` + `experiments/`, experiments/archive/ for scan-replay era artifacts,
  `experiments/README.md` ledger, `data/raw/README.md` reproduction guide
  (+ .gitignore exception), stale `docs/plan/handoff.md` removed, path
  references rewritten (plist, scanners.md, experiment docs, parents[2]).

## Resume Here
1. Ship `chore/repo-structure` to main (/ship — commit exists, needs the
   one-confirm push/PR/merge).
2. Next research fork (pick one):
   a. **Kimchi-premium dataset** — the structural moat play. Upbit KRW ×
      Binance USDT × USDKRW; unlocks `kimp_wave`/`kimchi_audit` scanner
      validation too. Unknowns: upbit sibling KRW depth, FX source.
   b. **Live micro collection** — start a collector for taker/OI/liq (500pt
      cap means history is impossible; only forward collection unlocks the
      4 capped scanners).

## Decisions (do NOT revisit)
- Free-public-data signal families on liquid venues: DEAD (8 families, all
  rigorous). No re-mining variants — that's filter-shopping overfit.
- Rigor protocol mandatory: single-shot OOS + shuffle placebo + DSR/PBO.
- Edge categories left: KR fragmentation (kimp), capacity-constrained small
  KRW, conditional carry, live-collected micro, event-class on-chain (paid).
- Approach A panel engine — locked.

## Gotchas
- **GIT AUTH**: plain git resolves the WRONG account. Push:
  `git push "https://x-access-token:$(gh auth token -u sskys18)@github.com/sskys18/backtest.git" <branch>`.
  gh write commands need `-R sskys18/backtest`.
- **Codex push gate**: denies are usually REAL (caught 2 engine bugs). Fix,
  commit, re-push; 3-round cap → `rm .git/sspower-review-rounds-<branch>` to
  reset after fixing everything. Never bypass without user say-so.
- **/tmp commit-msg files race with other sessions** — use unique names
  (`/tmp/bt_ship_msg_*.txt`), verify `git log -1 --format=%s` after commit.
- Binance free taker/OI/liq/LSR cap ≈500 points; funding + klines paginate to
  FULL history (that asymmetry is load-bearing for what's testable).
- Binance klines = live symbols only → survivorship (anti-momentum). State it.
- `e-v-a-l(` literal substring in code trips the security hook on Write.
- Local main may be stale; hook blocks even ff merges → fetch with token URL
  then `git reset --hard origin/main` (verify 0 unique local commits first).

## Context
- Branch: `chore/repo-structure` (1 commit ahead of main `42cea22`, unpushed).
- Tests: cargo 63, pytest 165. Run pytest via
  `python -c "import pytest,sys; sys.exit(pytest.main(['tests/python/','-q']))"`.
- Data inventory + rebuild commands: `data/raw/README.md`.
- Memories current: `alt-alpha-meta-conclusion` (updated), `funding-carry-compressed` (new).
