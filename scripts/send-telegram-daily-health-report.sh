#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Send yesterday's router-health report to Telegram.
set -eu

SECRETS_FILE="${R3S_TELEGRAM_SECRETS:-/etc/r3s-accounting/secrets}"
HEALTH_OFFSET="${R3S_HEALTH_REPORT_OFFSET:--1}"
REPORT_FILE="/tmp/r3s-telegram-health-report.$$"
RESPONSE_FILE="/tmp/r3s-telegram-health-response.$$"

cleanup() {
    rm -f "$REPORT_FILE" "$RESPONSE_FILE"
    unset TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID
}
trap cleanup EXIT INT TERM

[ -r "$SECRETS_FILE" ] || {
    logger -t r3s-telegram-health 'secrets file is missing'
    exit 1
}
. "$SECRETS_FILE"
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN is missing}"
: "${TELEGRAM_CHAT_ID:?TELEGRAM_CHAT_ID is missing}"

/usr/bin/r3s-accounting-report daily "$HEALTH_OFFSET" --telegram-health > "$REPORT_FILE" || {
    logger -t r3s-telegram-health 'daily health report generation failed'
    exit 1
}

curl -fsS --connect-timeout 15 --retry 2 \
    --data-urlencode "chat_id=$TELEGRAM_CHAT_ID" \
    --data-urlencode 'parse_mode=MarkdownV2' \
    --data-urlencode "text@$REPORT_FILE" \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    > "$RESPONSE_FILE" || {
    logger -t r3s-telegram-health 'Telegram daily health request failed'
    exit 1
}

grep -q '"ok":true' "$RESPONSE_FILE" || {
    logger -t r3s-telegram-health 'Telegram rejected the daily health report'
    exit 1
}
logger -t r3s-telegram-health 'daily health report sent'
