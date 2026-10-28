#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Send yesterday's traffic report to Telegram.
# Notification failure must never stop accounting.
set -eu

SECRETS_FILE="${R3S_TELEGRAM_SECRETS:-/etc/r3s-accounting/secrets}"
REPORT_FILE="/tmp/r3s-telegram-report.$$"
RESPONSE_FILE="/tmp/r3s-telegram-response.$$"
PART_PREFIX="/tmp/r3s-telegram-report-part.$$"
COUNT_FILE="/tmp/r3s-telegram-part-count.$$"
MAX_MESSAGE_BYTES=3800
PART_COUNT=0

cleanup() {
    rm -f "$REPORT_FILE" "$RESPONSE_FILE" "$PART_PREFIX".* "$COUNT_FILE"
    unset TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID
}
trap cleanup EXIT INT TERM

[ -r "$SECRETS_FILE" ] || {
    logger -t r3s-telegram 'secrets file is missing'
    exit 1
}
. "$SECRETS_FILE"
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN is missing}"
: "${TELEGRAM_CHAT_ID:?TELEGRAM_CHAT_ID is missing}"

/usr/bin/r3s-accounting-report daily -1 --telegram > "$REPORT_FILE" || {
    logger -t r3s-telegram 'daily report generation failed'
    exit 1
}

awk -v prefix="$PART_PREFIX" -v count_file="$COUNT_FILE" -v max_bytes="$MAX_MESSAGE_BYTES" '
function flush() {
    if (chunk == "") return
    part_count++
    part_file = prefix "." part_count
    printf "%s", chunk > part_file
    close(part_file)
    chunk = ""
    chunk_bytes = 0
}
function add_paragraph() {
    if (paragraph == "") return
    if (chunk_bytes > 0 && chunk_bytes + paragraph_bytes > max_bytes) flush()
    chunk = chunk paragraph
    chunk_bytes += paragraph_bytes
    paragraph = ""
    paragraph_bytes = 0
}
{
    line = $0 "\n"
    paragraph = paragraph line
    paragraph_bytes += length(line)
    if ($0 == "") add_paragraph()
}
END {
    add_paragraph()
    flush()
    print part_count > count_file
}
' "$REPORT_FILE"
PART_COUNT="$(awk '{print $1}' "$COUNT_FILE")"
rm -f "$COUNT_FILE"

[ -n "$PART_COUNT" ] && [ "$PART_COUNT" -gt 0 ] || {
    logger -t r3s-telegram 'daily report split produced no parts'
    exit 1
}

part=1
while [ "$part" -le "$PART_COUNT" ]; do
    curl -fsS --connect-timeout 15 --retry 2 \
        --data-urlencode "chat_id=$TELEGRAM_CHAT_ID" \
        --data-urlencode 'parse_mode=MarkdownV2' \
        --data-urlencode "text@$PART_PREFIX.$part" \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        > "$RESPONSE_FILE" || {
        logger -t r3s-telegram 'Telegram request failed'
        exit 1
    }

    grep -q '"ok":true' "$RESPONSE_FILE" || {
        logger -t r3s-telegram 'Telegram rejected the daily report'
        exit 1
    }
    part=$((part + 1))
done
logger -t r3s-telegram 'daily report sent'
