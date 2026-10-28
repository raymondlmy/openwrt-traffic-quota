#!/usr/bin/env bash
# Renderer for the accounting nft rules: names, multiple interfaces, rejection
# of anything that is not a plain interface name, idempotence, and that the
# result loads in nft (rootless network namespace).
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
R="$REPO/scripts/render-accounting-nft.sh"
TPL="$REPO/config/nftables/templates/10-r3s-accounting.nft.in"
fails=0
ok()  { echo "PASS  $1"; }
bad() { echo "FAIL  $1"; fails=$((fails+1)); }
render() { env -i PATH="$PATH" TEMPLATE="$TPL" OUTPUT="$T/out.nft" "$@" sh "$R" >/dev/null 2>"$T/err"; }

render LAN_IFACES=br-lan WAN_IFACES=wan; rc=$?
[ $rc -eq 10 ] && grep -q 'iifname { "br-lan" } oifname { "wan" }' "$T/out.nft" && ok "renders LAN/WAN names (rc 10 = written)" || bad "basic render rc=$rc"
render LAN_IFACES=br-lan WAN_IFACES=wan; rc=$?
[ $rc -eq 0 ] && ok "second identical render reports unchanged (rc 0)" || bad "idempotence rc=$rc"
render LAN_IFACES="br-lan br-guest" WAN_IFACES="pppoe-wan wan6"; rc=$?
grep -q 'iifname { "br-lan", "br-guest" } oifname { "pppoe-wan", "wan6" }' "$T/out.nft" && ok "multiple interfaces become an nft set" || bad "multi rc=$rc"
! grep -q '@LAN_IFACES@\|@WAN_IFACES@' "$T/out.nft" && ok "no placeholders left" || bad "placeholders left"
for evil in 'wan"; drop' 'a b{' 'wan;flush ruleset' 'thisnameiswaytoolong0'; do
    cp "$T/out.nft" "$T/before"
    if render LAN_IFACES=br-lan WAN_IFACES="$evil"; then bad "accepted invalid name: $evil"
    else cmp -s "$T/before" "$T/out.nft" && ok "rejects invalid name and leaves rules unchanged: $evil" || bad "file changed for: $evil"; fi
done
rm -f "$T/out.nft"
render; rc=$?   # no uci, no overrides
[ $rc -eq 1 ] && grep -q "cannot detect the WAN device" "$T/err" && ok "fails clearly when WAN cannot be detected" || bad "no-WAN rc=$rc"
render LAN_IFACES=br-lan WAN_IFACES=pppoe-wan
if unshare -rn sh -c "nft -f '$T/out.nft' && nft -f '$T/out.nft' && nft -f '$REPO/config/nftables/ruleset-pre/20-r3s-policy.nft' && nft -f '$REPO/config/nftables/ruleset-pre/20-r3s-policy.nft'" 2>"$T/nfterr"; then
    ok "rendered + policy rules load and reload in nft (no 'destroy table')"
else bad "nft load: $(head -2 "$T/nfterr")"; fi
echo; [ "$fails" -eq 0 ] && echo "ALL PASSED" || { echo "$fails FAILED"; exit 1; }
