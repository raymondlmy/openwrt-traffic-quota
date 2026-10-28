#!/bin/sh
set -eu

DB=""
REGISTRY=""
PRUNE=0

for arg in "$@"; do
    case "$arg" in
        --prune) PRUNE=1 ;;
        -*) echo "ERROR: unknown option: $arg" >&2; exit 1 ;;
        *) if [ -z "$DB" ]; then DB="$arg"; elif [ -z "$REGISTRY" ]; then REGISTRY="$arg"; fi ;;
    esac
done

# Live registry is authoritative. This file, when run by package install, is a
# template of placeholder MACs and must not prune real mappings.
DB="${DB:-/opt/r3s-accounting/data/accounting.sqlite3}"
REGISTRY="${REGISTRY:-/etc/r3s-accounting/devices.json}"

[ -f "$DB" ] || { echo "ERROR: database not found: $DB" >&2; exit 1; }
[ -f "$REGISTRY" ] || { echo "ERROR: registry not found: $REGISTRY" >&2; exit 1; }

python3 - "$DB" "$REGISTRY" "$PRUNE" <<'PY'
import json
import sqlite3
import sys

DB, REGISTRY = sys.argv[1], sys.argv[2]
prune = sys.argv[3] == "1"
with open(REGISTRY, "r", encoding="utf-8") as f:
    data = json.load(f)

if data.get("version") != 1:
    raise SystemExit("ERROR: unsupported device registry version")

devices = data.get("devices")
if not isinstance(devices, list):
    raise SystemExit("ERROR: registry devices must be a list")

seen_ids = set()
seen_macs = set()
for d in devices:
    did = d.get("device_id")
    name = d.get("name")
    group = d.get("group_name")
    macs = d.get("macs", [])
    if not isinstance(did, int) or did <= 0:
        raise SystemExit("ERROR: invalid device_id")
    if did in seen_ids:
        raise SystemExit(f"ERROR: duplicate device_id: {did}")
    if not isinstance(name, str) or not name or not isinstance(group, str) or not group:
        raise SystemExit(f"ERROR: invalid device metadata for device_id {did}")
    if not isinstance(macs, list):
        raise SystemExit(f"ERROR: macs must be a list for device_id {did}")
    seen_ids.add(did)
    for mac in macs:
        if not isinstance(mac, str) or len(mac) != 17:
            raise SystemExit(f"ERROR: invalid MAC for device_id {did}: {mac!r}")
        mac = mac.lower()
        if mac in seen_macs:
            raise SystemExit(f"ERROR: duplicate MAC: {mac}")
        seen_macs.add(mac)

con = sqlite3.connect(DB)
try:
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("BEGIN")
    for d in devices:
        con.execute(
            "INSERT INTO devices(device_id,name,group_name,enabled) VALUES(?,?,?,?) "
            "ON CONFLICT(device_id) DO UPDATE SET name=excluded.name, group_name=excluded.group_name, enabled=excluded.enabled",
            (d["device_id"], d["name"], d["group_name"], 1 if d.get("enabled", True) else 0),
        )
        # Additive by default. The live registry on the router is the source of
        # truth for MACs, not this file: phones rotate private MACs and IoT
        # devices re-pair, so a stale copy must never delete live mappings.
        # Pruning is opt-in via --prune, for deliberate removals only.
        if prune:
            con.execute("DELETE FROM device_macs WHERE device_id=?", (d["device_id"],))
        for mac in d.get("macs", []):
            con.execute("INSERT OR IGNORE INTO device_macs(mac,device_id) VALUES(?,?)", (mac.lower(), d["device_id"]))
    con.commit()
finally:
    con.close()

print(f"Provisioned {len(devices)} devices and {len(seen_macs)} MAC mappings into {DB}")
PY
