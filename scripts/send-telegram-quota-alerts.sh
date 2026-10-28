#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Deliver one Telegram message when a bandwidth quota is reached.
set -eu

SECRETS_FILE="${R3S_TELEGRAM_SECRETS:-/etc/r3s-accounting/secrets}"
DB="${R3S_ACCOUNTING_DB:-/var/lib/r3s-accounting/accounting.sqlite3}"
LOCK="/tmp/r3s-telegram-quota-alerts.lock"
EVENT_FILE="/tmp/r3s-telegram-quota-events.$$"
RESPONSE_FILE="/tmp/r3s-telegram-quota-response.$$"
ERROR_FILE="/tmp/r3s-telegram-quota-error.$$"

cleanup() {
    rm -f "$EVENT_FILE" "$RESPONSE_FILE" "$ERROR_FILE"
    rmdir "$LOCK" 2>/dev/null || true
    unset TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID
}
trap cleanup EXIT INT TERM

mkdir "$LOCK" 2>/dev/null || exit 0

[ -r "$SECRETS_FILE" ] || {
    logger -t r3s-telegram-quota 'secrets file is missing'
    exit 1
}
. "$SECRETS_FILE"
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN is missing}"
: "${TELEGRAM_CHAT_ID:?TELEGRAM_CHAT_ID is missing}"

sqlite3 -cmd '.timeout 5000' -separator '|' "$DB" 2>"$ERROR_FILE" \
    "SELECT a.alert_id,a.device_name,a.policy_name,a.quota_bytes,a.used_bytes,
            COALESCE((SELECT c.domain || ' -> ' || c.destination_ip
                        FROM policy_usage_charges c
                       WHERE c.policy_id=a.policy_id AND c.device_id=a.device_id
                         AND c.period_start=a.period_start
                       ORDER BY c.bucket_start_utc DESC,c.charged_bytes DESC LIMIT 1),'')
       FROM policy_quota_alerts a
      WHERE a.sent_at IS NULL
      ORDER BY a.alert_id;" > "$EVENT_FILE" || {
    logger -t r3s-telegram-quota "could not read quota alerts: $(head -c 200 "$ERROR_FILE" 2>/dev/null)"
    exit 1
}

format_mb() {
    awk -v bytes="$1" 'BEGIN { printf "%g MB", bytes / 1000000 }'
}

while IFS='|' read -r alert_id device_name policy_name quota_bytes used_bytes evidence; do
    [ -n "$alert_id" ] || continue
    quota="$(format_mb "$quota_bytes")"
    used="$(format_mb "$used_bytes")"
    text="⚠️ Bandwidth quota reached

$device_name reached today's $quota $policy_name quota.
Measured: $used"
    if [ -n "$evidence" ]; then
        text="$text
Evidence: $evidence"
    fi

    curl -fsS --connect-timeout 15 --retry 2 \
        --data-urlencode "chat_id=$TELEGRAM_CHAT_ID" \
        --data-urlencode "text=$text" \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        > "$RESPONSE_FILE" || {
        logger -t r3s-telegram-quota 'Telegram request failed'
        exit 1
    }
    grep -q '"ok":true' "$RESPONSE_FILE" || {
        logger -t r3s-telegram-quota 'Telegram rejected the quota alert'
        exit 1
    }
    sqlite3 -cmd '.timeout 5000' "$DB" \
        "UPDATE policy_quota_alerts SET sent_at=strftime('%s','now') WHERE alert_id=$alert_id AND sent_at IS NULL;" || {
        logger -t r3s-telegram-quota 'could not mark quota alert delivered'
        exit 1
    }
done < "$EVENT_FILE"

logger -t r3s-telegram-quota 'quota alerts processed'
