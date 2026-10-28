#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Deliver sustained router CPU/memory threshold events to Telegram.
set -eu

SECRETS_FILE="${R3S_TELEGRAM_SECRETS:-/etc/r3s-accounting/secrets}"
DB="${R3S_ACCOUNTING_DB:-/var/lib/r3s-accounting/accounting.sqlite3}"
LOCK="/tmp/r3s-telegram-health-alerts.lock"
EVENT_FILE="/tmp/r3s-telegram-health-events.$$"
RESPONSE_FILE="/tmp/r3s-telegram-health-response.$$"
ERROR_FILE="/tmp/r3s-telegram-health-error.$$"

cleanup() {
    rm -f "$EVENT_FILE" "$RESPONSE_FILE" "$ERROR_FILE"
    rmdir "$LOCK" 2>/dev/null || true
    unset TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID
}
trap cleanup EXIT INT TERM

# Cron may overlap a slow Telegram request; one sender must own delivery.
mkdir "$LOCK" 2>/dev/null || exit 0

[ -r "$SECRETS_FILE" ] || {
    logger -t r3s-telegram-health 'secrets file is missing'
    exit 1
}
. "$SECRETS_FILE"
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN is missing}"
: "${TELEGRAM_CHAT_ID:?TELEGRAM_CHAT_ID is missing}"

sqlite3 -cmd '.timeout 5000' -separator '|' "$DB" 2>"$ERROR_FILE" \
    'SELECT alert_id,event_time,resource,event_type,value_x100
       FROM router_health_alerts
      WHERE sent_at IS NULL
      ORDER BY alert_id;' > "$EVENT_FILE" || {
    logger -t r3s-telegram-health "could not read router health alerts: $(head -c 200 "$ERROR_FILE" 2>/dev/null)"
    exit 1
}

while IFS='|' read -r alert_id event_time resource event_type value_x100; do
    [ -n "$alert_id" ] || continue
    case "$resource" in
        cpu) label='CPU' ;;
        memory) label='Memory' ;;
        *) logger -t r3s-telegram-health "unknown resource in alert $alert_id"; exit 1 ;;
    esac
    value="$(awk -v raw="$value_x100" 'BEGIN { printf "%.0f", raw / 100 }')%"
    occurred="$(date -d "@$event_time" '+%Y-%m-%d %H:%M')"

    if [ "$event_type" = warning ]; then
        text="⚠️ Router resource warning

$label has remained above 90% for 1 hour.
Current: $value
Since: $occurred"
    elif [ "$event_type" = recovery ]; then
        text="✅ Router resource recovered

$label is back below 90%.
Current: $value
At: $occurred"
    else
        logger -t r3s-telegram-health "unknown event type in alert $alert_id"
        exit 1
    fi

    curl -fsS --connect-timeout 15 --retry 2 \
        --data-urlencode "chat_id=$TELEGRAM_CHAT_ID" \
        --data-urlencode "text=$text" \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        > "$RESPONSE_FILE" || {
        logger -t r3s-telegram-health 'Telegram request failed'
        exit 1
    }
    grep -q '"ok":true' "$RESPONSE_FILE" || {
        logger -t r3s-telegram-health 'Telegram rejected the health alert'
        exit 1
    }
    sqlite3 -cmd '.timeout 5000' "$DB" \
        "UPDATE router_health_alerts SET sent_at=strftime('%s','now') WHERE alert_id=$alert_id AND sent_at IS NULL;" || {
        logger -t r3s-telegram-health 'could not mark health alert delivered'
        exit 1
    }
done < "$EVENT_FILE"

logger -t r3s-telegram-health 'health alerts processed'
