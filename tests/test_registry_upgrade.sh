#!/usr/bin/env bash
# Simulate r3s-accounting install/upgrade registry handling in a scratch root:
#   A. upgrade where apk removed the previously packaged devices.json
#   B. upgrade where apk left devices.json in place
#   C. fresh install with no registry
# plus: the example registry is valid and is never provisioned.
# Runs preinst.sh/postinst.sh with every absolute path redirected; no root needed.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
fails=0
ok()  { echo "PASS  $1"; }
bad() { echo "FAIL  $1"; fails=$((fails+1)); }

mkdir -p "$T/bin"
# sqlite3 stand-in: "sqlite3 DB 'SQL'" or "sqlite3 DB < file"
cat > "$T/bin/sqlite3" <<'EOF'
#!/usr/bin/env python3
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
sql = sys.argv[2] if len(sys.argv) > 2 else sys.stdin.read()
if len(sys.argv) > 2 and sql.strip().upper().startswith(("SELECT", "PRAGMA")):
    for row in con.execute(sql):
        print("|".join("" if v is None else str(v) for v in row))
else:
    con.executescript(sql)
con.commit()
EOF
chmod +x "$T/bin/sqlite3"
printf '#!/bin/sh\nexit 1\n' > "$T/bin/uci"   # no UCI here: every option unset
chmod +x "$T/bin/uci"

run_script() { # script root
    sed -e "s#/etc/r3s-accounting#$2/etc/r3s-accounting#g" \
        -e "s#/opt/r3s-accounting#$2/opt/r3s-accounting#g" \
        -e "s#/tmp/lib/r3s-accounting#$2/tmp/lib#g" \
        -e "s#/usr/share/r3s-accounting/schema.sql#$REPO/config/schema.sql#g" \
        -e "s#/usr/share/r3s-accounting/migrations#$REPO/config/migrations#g" \
        -e "s#/usr/bin/r3s-accounting-provision-devices#sh $REPO/scripts/provision-device-registry.sh#g" \
        -e "s#/usr/bin/sqlite3#sqlite3#g" \
        -e "s#^/etc/init.d/[a-z0-9-]* [a-z]*#true#" \
        -e "s#/usr/bin/r3s-accounting-render-nft#LAN_IFACES=br-lan WAN_IFACES=wan TEMPLATE=$REPO/config/nftables/templates/10-r3s-accounting.nft.in OUTPUT=$2/nft.out sh $REPO/scripts/render-accounting-nft.sh#" \
        "$1" > "$2/script.sh"
    PATH="$T/bin:$PATH" sh "$2/script.sh"
}

LIVE='{"version":1,"devices":[{"device_id":7,"name":"tablet-1","group_name":"Kids","enabled":true,"macs":["02:00:00:00:00:07"]}]}'
names() { python3 -I -c 'import sqlite3,sys; print(",".join(r[0] for r in sqlite3.connect(sys.argv[1]).execute("SELECT name FROM devices ORDER BY device_id")))' "$1/opt/r3s-accounting/data/accounting.sqlite3"; }

# A: upgrade, apk deletes the old packaged registry between preinst and postinst
R="$T/a"; mkdir -p "$R/etc/r3s-accounting"; echo "$LIVE" > "$R/etc/r3s-accounting/devices.json"
run_script "$REPO/packages/r3s-accounting/preinst.sh" "$R"
rm "$R/etc/r3s-accounting/devices.json"
run_script "$REPO/packages/r3s-accounting/postinst.sh" "$R" >/dev/null
[ "$(cat "$R/etc/r3s-accounting/devices.json")" = "$LIVE" ] && ok "A: deleted registry restored byte-for-byte" || bad "A: registry not restored"
[ "$(names "$R")" = "tablet-1" ] && ok "A: database has the live device only" || bad "A: database names: $(names "$R")"
grep -q 'iifname { "br-lan" } oifname { "wan" }' "$R/nft.out" 2>/dev/null && ok "A: postinst rendered the accounting rules" || bad "A: accounting rules not rendered"

# B: upgrade, file kept
R="$T/b"; mkdir -p "$R/etc/r3s-accounting"; echo "$LIVE" > "$R/etc/r3s-accounting/devices.json"
run_script "$REPO/packages/r3s-accounting/preinst.sh" "$R"
run_script "$REPO/packages/r3s-accounting/postinst.sh" "$R" >/dev/null
[ "$(cat "$R/etc/r3s-accounting/devices.json")" = "$LIVE" ] && ok "B: kept registry untouched" || bad "B: registry changed"
[ "$(names "$R")" = "tablet-1" ] && ok "B: database has the live device only" || bad "B: database names: $(names "$R")"

# C: fresh install, nothing there
R="$T/c"; mkdir -p "$R"
run_script "$REPO/packages/r3s-accounting/postinst.sh" "$R" >/dev/null
python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(not (d=={"version":1,"devices":[]}))' "$R/etc/r3s-accounting/devices.json" \
    && ok "C: fresh install creates an empty registry (no example devices)" || bad "C: unexpected registry"
[ -z "$(names "$R")" ] && ok "C: database has no devices" || bad "C: database names: $(names "$R")"
[ "$(stat -c %a "$R/etc/r3s-accounting/devices.json")" = 600 ] && ok "C: registry mode 600" || bad "C: registry mode"

# Example registry is valid for the provisioner (no duplicate MACs)
R="$T/d"; mkdir -p "$R"; PATH="$T/bin:$PATH" sqlite3 "$R/db" < "$REPO/config/schema.sql"
if sh "$REPO/scripts/provision-device-registry.sh" "$R/db" "$REPO/config/devices.example.json" >/dev/null; then
    ok "example registry passes the provisioner"; else bad "example registry rejected"; fi

# Packaging never puts a registry in /etc
if grep -nE 'INSTALL_CONF.*devices|A/etc/r3s-accounting/devices' "$REPO/scripts/build-managed-apks.sh" "$REPO/packages/r3s-accounting/Makefile" >/dev/null; then
    bad "a package still installs /etc/r3s-accounting/devices.json"; else ok "no package installs /etc/r3s-accounting/devices.json"; fi

echo
if [ "$fails" -eq 0 ]; then echo "ALL PASSED"; else echo "$fails FAILED"; exit 1; fi
