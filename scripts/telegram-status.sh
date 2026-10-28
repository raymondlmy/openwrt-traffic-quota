#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Read-only Telegram status for LuCI users with read access (never shows the token).
[ "$#" -eq 0 ] || { echo "usage: r3s-telegram-status" >&2; exit 2; }
exec /usr/bin/r3s-telegram-setup status
