#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Install or upgrade openwrt-traffic-quota on an OpenWrt 25.12+ router.
#
#   wget -qO /tmp/install.sh https://raw.githubusercontent.com/raymondlmy/openwrt-traffic-quota/main/install.sh
#   sh /tmp/install.sh                 # latest release: all three packages
#   sh /tmp/install.sh --no-telegram   # skip Telegram reports/commands
#   sh /tmp/install.sh --no-luci       # command line only
#   sh /tmp/install.sh --version 0.6.1 # a specific release
#   sh /tmp/install.sh --from /tmp/pkgs  # local .apk files + SHA256SUMS (offline)
#   sh /tmp/install.sh --check         # only check this router, change nothing
#
# Downloads the release packages and SHA256SUMS from GitHub, verifies every
# checksum, then installs with apk, which pulls the dependencies (python3,
# sqlite3-cli, nftables-json, curl, ...) from the router's normal OpenWrt feeds.
# The packages are not signed with an OpenWrt key, hence --allow-untrusted;
# the SHA256 check above is what ties them to the release.
set -eu

REPO="raymondlmy/openwrt-traffic-quota"
VERSION=""
FROM=""
WANT_LUCI=1
WANT_TELEGRAM=1
CHECK_ONLY=0

usage() { sed -n '3,11p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
say() { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --version) [ $# -ge 2 ] || usage 2; VERSION="$2"; shift ;;
        --from) [ $# -ge 2 ] || usage 2; FROM="$2"; shift ;;
        --no-luci) WANT_LUCI=0 ;;
        --no-telegram) WANT_TELEGRAM=0 ;;
        --check) CHECK_ONLY=1 ;;
        -h|--help) usage 0 ;;
        *) warn "unknown option: $1"; usage 2 ;;
    esac
    shift
done
case "$VERSION" in ''|[0-9]*.[0-9]*.[0-9]*) ;; *) die "--version must look like 0.6.1" ;; esac

# ---- checks ----
[ "$(id -u)" = 0 ] || die "run as root"
[ -r /etc/openwrt_release ] || die "this is not OpenWrt"
# shellcheck source=/dev/null
. /etc/openwrt_release
major="${DISTRIB_RELEASE%%.*}"
rest="${DISTRIB_RELEASE#*.}"; minor="${rest%%.*}"
case "$major$minor" in *[!0-9]*|'') warn "unrecognised OpenWrt version '$DISTRIB_RELEASE'; continuing" ;;
    *) if [ "$major" -lt 25 ] || { [ "$major" -eq 25 ] && [ "$minor" -lt 12 ]; }; then
           die "OpenWrt $DISTRIB_RELEASE: needs 25.12 or newer (apk packages, firewall4)"
       fi ;;
esac
command -v apk >/dev/null 2>&1 || die "apk not found: needs OpenWrt 25.12 or newer"
command -v nft >/dev/null 2>&1 && [ -x /sbin/fw4 ] || die "firewall4/nftables not found"
say "OpenWrt $DISTRIB_RELEASE ($DISTRIB_TARGET): OK"

free_kb() { df -k "$1" 2>/dev/null | awk 'NR==2 {print $4}'; }
root_free="$(free_kb /)"
if [ -n "$root_free" ] && [ "$root_free" -lt 30720 ]; then
    die "only $((root_free / 1024)) MB free on /; python3 and the packages need about 25 MB"
fi
say "Free space on /: $((${root_free:-0} / 1024)) MB: OK"

mem_kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
if [ "${mem_kb:-0}" -lt 240000 ]; then
    warn "$((mem_kb / 1024)) MB RAM; 256 MB is the minimum, 512 MB+ recommended"
fi

data_dir="$(uci -q get r3s-accounting.main.data_dir 2>/dev/null || true)"
data_dir="${data_dir:-/opt/r3s-accounting/data}"
probe="$data_dir"
while [ ! -d "$probe" ] && [ "$probe" != / ]; do probe="$(dirname "$probe")"; done
fstype="$(awk -v d="$probe" '{m=$2; if (m=="/") m=""} index(d"/", m"/")==1 && length(m)>=n {n=length(m); t=$3} END {print t}' /proc/mounts)"
case "$fstype" in
    overlay|jffs2|ubifs|squashfs)
        warn "the database directory $data_dir is on $fstype (router flash). It is written every minute and"
        warn "grows to 50-300 MB. Use an SD card, eMMC, USB or SSD: set data_dir in /etc/config/r3s-accounting." ;;
    *) say "Database storage ($data_dir on ${fstype:-?}): OK" ;;
esac
if [ "$(uci -q get firewall.@defaults[0].flow_offloading 2>/dev/null)" = "1" ]; then
    warn "firewall flow offloading is on: offloaded traffic skips counting and blocks. Turn it off."
fi
if ! pidof AdGuardHome >/dev/null 2>&1; then
    say "AdGuard Home: not running (optional; needed only for per-site quotas)"
fi
[ "$CHECK_ONLY" = 1 ] && { say "Check finished; nothing changed."; exit 0; }

# ---- download and verify ----
WORK="$(mktemp -d /tmp/otq-install.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT INT TERM
fetch() { wget -q -O "$2" "$1" || die "download failed: $1"; }

if [ -n "$FROM" ]; then
    [ -r "$FROM/SHA256SUMS" ] || die "$FROM/SHA256SUMS not found"
    cp "$FROM/SHA256SUMS" "$WORK/"
else
    if [ -n "$VERSION" ]; then
        BASE="https://github.com/$REPO/releases/download/v$VERSION"
    else
        BASE="https://github.com/$REPO/releases/latest/download"
    fi
    say "Downloading from $BASE"
    fetch "$BASE/SHA256SUMS" "$WORK/SHA256SUMS"
fi

files=""
while read -r sum name; do
    case "$name" in
        r3s-accounting-[0-9]*.apk) ;;
        luci-app-r3s-[0-9]*.apk) [ "$WANT_LUCI" = 1 ] || continue ;;
        r3s-accounting-telegram-[0-9]*.apk) [ "$WANT_TELEGRAM" = 1 ] || continue ;;
        *) continue ;;
    esac
    case "$name" in */*|*..*) die "unexpected file name in SHA256SUMS: $name" ;; esac
    if [ -n "$FROM" ]; then cp "$FROM/$name" "$WORK/$name" || die "missing $FROM/$name"
    else fetch "$BASE/$name" "$WORK/$name"; fi
    [ "$(sha256sum "$WORK/$name" | cut -d' ' -f1)" = "$sum" ] || die "checksum mismatch: $name"
    say "Verified $name"
    files="$files $WORK/$name"
done < "$WORK/SHA256SUMS"
case "$files" in *r3s-accounting-[0-9]*) ;; *) die "r3s-accounting package not in this release" ;; esac

# ---- install ----
say "Installing (apk also fetches dependencies from the OpenWrt feeds)..."
apk update >/dev/null 2>&1 || warn "apk update failed; dependencies may not resolve"
# shellcheck disable=SC2086  # $files is a list of paths without spaces (mktemp)
apk add --allow-untrusted $files

say ""
say "Installed:"
apk info -v 2>/dev/null | grep -E '^(r3s-accounting|luci-app-r3s)' | sed 's/^/  /'
say ""
say "Next:"
[ "$WANT_LUCI" = 1 ] && say "  - LuCI: Services > Traffic Quotas: register devices (Unresolved MACs), add schedules/quotas"
say "  - Or the command line: r3s-policy devices | r3s-policy unresolved | bw-rpt daily"
[ "$WANT_TELEGRAM" = 1 ] && say "  - Telegram: Services > Telegram Reports, or r3s-telegram-setup"
say "  - Per-site quotas need AdGuard Home: see INSTALL.md"
