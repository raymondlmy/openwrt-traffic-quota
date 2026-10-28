#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Render the accounting nft rules with this router's LAN and WAN devices.
#
#   r3s-accounting-render-nft          render; exit 0 if unchanged, 10 if written
#
# Devices come from /etc/config/r3s-accounting (lan_device / wan_device, both
# may list several names). Empty means auto-detect: LAN = network.lan.device,
# WAN = "pppoe-wan" for PPPoE, otherwise network.wan.device.
# Environment overrides (tests): LAN_IFACES, WAN_IFACES, TEMPLATE, OUTPUT.
set -eu
TEMPLATE="${TEMPLATE:-/usr/share/r3s-accounting/10-r3s-accounting.nft.in}"
OUTPUT="${OUTPUT:-/usr/share/nftables.d/ruleset-pre/10-r3s-accounting.nft}"

uciget() { command -v uci >/dev/null 2>&1 && uci -q get "$1" 2>/dev/null || true; }

lan="${LAN_IFACES:-$(uciget r3s-accounting.main.lan_device)}"
[ -n "$lan" ] || lan="$(uciget network.lan.device)"
[ -n "$lan" ] || lan="br-lan"

wan="${WAN_IFACES:-$(uciget r3s-accounting.main.wan_device)}"
if [ -z "$wan" ]; then
    if [ "$(uciget network.wan.proto)" = "pppoe" ]; then
        wan="pppoe-wan"
    else
        wan="$(uciget network.wan.device)"
    fi
fi
[ -n "$wan" ] || { echo "r3s-accounting-render-nft: cannot detect the WAN device; set r3s-accounting.main.wan_device" >&2; exit 1; }

# nft anonymous set of quoted interface names; reject anything that is not a
# plain Linux interface name (max 15 chars) so nothing else reaches nft.
nftset() {
    out=""
    for name in $1; do
        printf '%s' "$name" | grep -Eq '^[A-Za-z0-9._@-]{1,15}$' || {
            echo "r3s-accounting-render-nft: invalid interface name: $name" >&2; exit 1; }
        out="${out:+$out, }\"$name\""
    done
    printf '{ %s }' "$out"
}
lan_set="$(nftset "$lan")"
wan_set="$(nftset "$wan")"

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
sed -e "s/@LAN_IFACES@/$lan_set/g" -e "s/@WAN_IFACES@/$wan_set/g" "$TEMPLATE" > "$tmp"
if [ -f "$OUTPUT" ] && cmp -s "$tmp" "$OUTPUT"; then
    exit 0
fi
mkdir -p "$(dirname "$OUTPUT")"
cat "$tmp" > "$OUTPUT"
echo "r3s-accounting: accounting rules use LAN $lan_set, WAN $wan_set"
exit 10
