#!/usr/bin/env bash
# End-to-end IPv4 + IPv6 test of the accounting and policy nft rules, the
# collector and the evaluator, using throwaway network namespaces.
#
#   client (aa:bb:cc:00:00:10) --br-lan-- router --pppoe-wan-- server
#
# Needs: Linux with unprivileged user namespaces, nft, ip, curl, python3.
# Touches nothing outside the namespaces; run as a normal user:
#   tests/ipv6_netns_e2e.sh
set -euo pipefail

if [ "${R3S_E2E_INNER:-}" != 1 ]; then
    # As root (CI) skip the user-namespace mapping, which would hide files
    # owned by other users; as a normal user it provides the needed root.
    if [ "$(id -u)" -eq 0 ]; then
        exec env R3S_E2E_INNER=1 unshare -nm --propagation private "$0" "$@"
    fi
    exec env R3S_E2E_INNER=1 unshare -rnm --propagation private "$0" "$@"
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
DRIVER="$REPO/tests/ipv6_netns_driver.py"
ACCOUNTING_NFT="$WORK/10-r3s-accounting.nft"
LAN_IFACES=br-lan WAN_IFACES=pppoe-wan TEMPLATE="$REPO/config/nftables/templates/10-r3s-accounting.nft.in" \
    OUTPUT="$ACCOUNTING_NFT" sh "$REPO/scripts/render-accounting-nft.sh" >/dev/null || [ $? -eq 10 ]
mount -t tmpfs none /run
mkdir -p /run/netns
for ns in client router server; do ip netns add "$ns"; done
nsx() { local ns="$1"; shift; nsenter --net="/run/netns/$ns" "$@"; }

ip -n router link add br-lan type veth peer name c0 netns client
ip -n router link add pppoe-wan type veth peer name s0 netns server
ip -n client link set c0 address aa:bb:cc:00:00:10
for ns in client router server; do ip -n "$ns" link set lo up; done
ip -n client link set c0 up
ip -n server link set s0 up
ip -n router link set br-lan up
ip -n router link set pppoe-wan up

nsx router sysctl -qw net.ipv4.ip_forward=1 net.ipv6.conf.all.forwarding=1
for ns in client router server; do nsx "$ns" sysctl -qw net.ipv6.conf.all.accept_dad=0 net.ipv6.conf.default.accept_dad=0; done

ip -n client addr add 192.168.1.10/24 dev c0
ip -n client addr add 2001:db8:1::10/64 dev c0 nodad
ip -n client route add default via 192.168.1.1
ip -n client -6 route add default via 2001:db8:1::1
ip -n router addr add 192.168.1.1/24 dev br-lan
ip -n router addr add 2001:db8:1::1/64 dev br-lan nodad
ip -n router addr add 203.0.113.1/24 dev pppoe-wan
ip -n router addr add 2001:db8:2::1/64 dev pppoe-wan nodad
ip -n server addr add 203.0.113.2/24 dev s0
ip -n server addr add 203.0.113.3/24 dev s0
ip -n server addr add 2001:db8:2::2/64 dev s0 nodad
ip -n server addr add 2001:db8:2::3/64 dev s0 nodad
ip -n server route add default via 203.0.113.1
ip -n server -6 route add default via 2001:db8:2::1

nsx router nft -f "$ACCOUNTING_NFT"
nsx router nft -f "$REPO/config/nftables/ruleset-pre/20-r3s-policy.nft"

nsx server python3 -I -m http.server --bind :: 8080 >/dev/null 2>&1 &
sleep 1

fails=0
check() { # name expected(ok|fail) url [extra curl args]
    if nsx client curl -s -o /dev/null --max-time 3 "${@:4}" "$3"; then got=ok; else got=fail; fi
    if [ "$got" = "$2" ]; then echo "PASS  $1"; else echo "FAIL  $1 (expected $2, got $got)"; fails=$((fails+1)); fi
}
expect() { # name condition...
    local name="$1"; shift
    if "$@"; then echo "PASS  $name"; else echo "FAIL  $name"; fails=$((fails+1)); fi
}
sql() { python3 -I -c 'import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute(sys.argv[2]).fetchone()[0])' "$WORK/accounting.sqlite3" "$1"; }

python3 -I "$DRIVER" "$WORK" init
nsx router python3 -I "$DRIVER" "$WORK" collect   # first run only sets baselines

echo "== Accounting =="
check "IPv6 traffic forwarded" ok "http://[2001:db8:2::2]:8080/"
expect "IPv6 client counted in tx_by_ip6" sh -c "nsenter --net=/run/netns/router nft list set inet r3s_accounting tx_by_ip6 | grep -q '2001:db8:1::10'"
expect "IPv6 client counted in rx_by_ip6" sh -c "nsenter --net=/run/netns/router nft list set inet r3s_accounting rx_by_ip6 | grep -q '2001:db8:1::10'"
expect "IPv6 destination pair counted" sh -c "nsenter --net=/run/netns/router nft list set inet r3s_accounting tx_by_destination6 | grep -q '2001:db8:1::10 . 2001:db8:2::2'"
nsx router python3 -I "$DRIVER" "$WORK" collect
expect "collector maps the IPv6 address to the client MAC (neighbour table)" \
    test "$(sql "SELECT mac FROM ip_assignments WHERE ip='2001:db8:1::10'")" = aa:bb:cc:00:00:10
v6_total=$(sql "SELECT COALESCE(SUM(rx_bytes+tx_bytes),0) FROM usage_hourly WHERE device_id=1")
expect "collector charges IPv6-only traffic to the device (${v6_total} bytes)" test "$v6_total" -gt 0
expect "no IPv6 traffic left unresolved" \
    test "$(sql "SELECT COUNT(*) FROM unresolved_usage WHERE ip LIKE '%:%'")" -eq 0
check "IPv4 traffic forwarded" ok "http://203.0.113.2:8080/"
nsx router python3 -I "$DRIVER" "$WORK" collect
expect "collector still charges IPv4 traffic to the device" \
    test "$(sql "SELECT COALESCE(SUM(rx_bytes+tx_bytes),0) FROM usage_hourly WHERE device_id=1")" -gt "$v6_total"
expect "collector stores IPv6 destination usage" \
    test "$(sql "SELECT COUNT(*) FROM destination_usage_quarterly WHERE client_ip='2001:db8:1::10' AND destination_ip='2001:db8:2::2' AND device_id=1")" -gt 0

echo "== Internet schedule block =="
nsx router python3 -I "$DRIVER" "$WORK" evaluate
expect "client MAC in blocked_internet_mac6" sh -c "nsenter --net=/run/netns/router nft list set inet r3s_policy blocked_internet_mac6 | grep -q 'aa:bb:cc:00:00:10'"
check "IPv6 blocked during schedule" fail "http://[2001:db8:2::2]:8080/"
check "IPv4 blocked during schedule" fail "http://203.0.113.2:8080/"
ip -n client addr add 2001:db8:1::99/64 dev c0 nodad
check "IPv6 blocked from a brand-new (rotated) address the evaluator never saw" fail "http://[2001:db8:2::2]:8080/" --interface 2001:db8:1::99
nsx router python3 -I "$DRIVER" "$WORK" unevaluate
check "IPv6 allowed again after override=allow" ok "http://[2001:db8:2::2]:8080/"
check "IPv4 allowed again after override=allow" ok "http://203.0.113.2:8080/"

echo "== Custom quota pair block =="
nsx router python3 -I "$DRIVER" "$WORK" pairs
check "IPv6 blocked destination refused" fail "http://[2001:db8:2::2]:8080/"
check "IPv6 other destination still allowed" ok "http://[2001:db8:2::3]:8080/"
check "IPv4 blocked destination refused" fail "http://203.0.113.2:8080/"
check "IPv4 other destination still allowed" ok "http://203.0.113.3:8080/"
start=$(date +%s.%N); nsx client curl -s -o /dev/null --max-time 3 "http://[2001:db8:2::2]:8080/" || true
elapsed=$(python3 -c "import sys; print(float(sys.argv[2]) - float(sys.argv[1]))" "$start" "$(date +%s.%N)")
expect "IPv6 TCP block answers with reset, not timeout (${elapsed}s)" python3 -c "import sys; sys.exit(float(sys.argv[1]) >= 1.0)" "$elapsed"
nsx router python3 -I "$DRIVER" "$WORK" unpairs
check "IPv6 destination allowed after unblock" ok "http://[2001:db8:2::2]:8080/"

echo "== Reload =="
nsx router nft -f "$ACCOUNTING_NFT"
nsx router nft -f "$REPO/config/nftables/ruleset-pre/20-r3s-policy.nft"
check "IPv6 forwarded after rule reload" ok "http://[2001:db8:2::2]:8080/"

kill %1 2>/dev/null || true
rm -rf "$WORK"
echo
if [ "$fails" -eq 0 ]; then echo "ALL PASSED"; else echo "$fails FAILED"; exit 1; fi
