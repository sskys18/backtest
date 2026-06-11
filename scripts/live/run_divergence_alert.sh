#!/bin/zsh
# 외국인 수급 다이버전스 알림기 — launchd 무인 실행 래퍼.
# .env(KIS_*)와 (선택) TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID를 로드 후 release 바이너리 실행.
# 바이너리는 KST 15:40 후 자동 종료 → launchd가 매 평일 08:55 재기동.

set -a
. /Users/sskys/Mine/korea-stock/.env 2>/dev/null
# 텔레그램 알림 원하면 아래 주석 해제 후 채우기(또는 .env에 추가):
# export TELEGRAM_BOT_TOKEN="..."
# export TELEGRAM_CHAT_ID="..."
set +a

export ALERT_CODES="${ALERT_CODES:-005930,000660}"
export ALERT_INTERVAL_SECS="${ALERT_INTERVAL_SECS:-30}"
export ALERT_THRESH="${ALERT_THRESH:-2.0}"

exec /Users/sskys/Mine/backtest/target/release/divergence_alert
