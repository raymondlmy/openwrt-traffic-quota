#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Read-only entry point for LuCI users who only have read access: exactly one
# viewing subcommand, no options (so no --db, --registry or --text).
[ "$#" -eq 1 ] || { echo "usage: r3s-policy-ro {list|devices|evaluate|unresolved|exclusions}" >&2; exit 2; }
case "$1" in
    list|devices|evaluate|unresolved|exclusions) exec /usr/bin/r3s-policy "$1" ;;
    *) echo "r3s-policy-ro: not allowed: $1" >&2; exit 2 ;;
esac
