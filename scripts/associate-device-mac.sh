#!/bin/sh
# Add an explicitly approved MAC alias to an existing logical device.
# Usage: associate-device-mac.sh <device_id> <mac> [db] [registry]
set -eu

DEVICE_ID="${1:-}"
MAC="${2:-}"
DB="${3:-/var/lib/r3s-accounting/accounting.sqlite3}"
REGISTRY="${4:-/etc/r3s-accounting/devices.json}"

[ -n "$DEVICE_ID" ] && [ -n "$MAC" ] || {
    echo "Usage: $0 <device_id> <mac> [db] [registry]" >&2
    exit 2
}

python3 - "$DEVICE_ID" "$MAC" "$DB" "$REGISTRY" <<'PY'
import json
import os
import re
import sqlite3
import sys
import tempfile

DEVICE_ID = int(sys.argv[1])
MAC = sys.argv[2].lower()
DB = sys.argv[3]
REGISTRY = sys.argv[4]

if not re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", MAC):
    raise SystemExit(f"ERROR: invalid MAC: {MAC}")
if DEVICE_ID <= 0:
    raise SystemExit("ERROR: invalid device_id")
if not os.path.isfile(REGISTRY):
    raise SystemExit(f"ERROR: registry not found: {REGISTRY}")
if not os.path.isfile(DB):
    raise SystemExit(f"ERROR: database not found: {DB}")

with open(REGISTRY, "r", encoding="utf-8") as f:
    data = json.load(f)
if data.get("version") != 1:
    raise SystemExit("ERROR: unsupported device registry version")

devices = data.get("devices")
if not isinstance(devices, list):
    raise SystemExit("ERROR: registry devices must be a list")

target = next((d for d in devices if d.get("device_id") == DEVICE_ID), None)
if target is None:
    raise SystemExit(f"ERROR: device_id {DEVICE_ID} not found in registry")

for d in devices:
    if MAC in [str(m).lower() for m in d.get("macs", [])]:
        if d.get("device_id") == DEVICE_ID:
            print(f"MAC already approved: {MAC} -> device_id {DEVICE_ID}")
            raise SystemExit(0)
        raise SystemExit(
            f"ERROR: MAC {MAC} is already assigned to device_id {d.get('device_id')}"
        )

# Keep registry as the source of truth. Write atomically after validation.
target.setdefault("macs", []).append(MAC)
fd, tmp = tempfile.mkstemp(prefix="devices.json.", dir=os.path.dirname(REGISTRY), text=True)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, os.stat(REGISTRY).st_mode & 0o777)
    os.replace(tmp, REGISTRY)
except Exception:
    try: os.unlink(tmp)
    except OSError: pass
    raise

# Provision the complete registry transactionally. If it fails, restore the
# registry from the pre-change JSON held in memory and leave SQLite untouched.
try:
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("BEGIN")
    for d in devices:
        con.execute(
            "INSERT INTO devices(device_id,name,group_name,enabled) VALUES(?,?,?,?) "
            "ON CONFLICT(device_id) DO UPDATE SET name=excluded.name, group_name=excluded.group_name, enabled=excluded.enabled",
            (d["device_id"], d["name"], d["group_name"], 1 if d.get("enabled", True) else 0),
        )
        con.execute("DELETE FROM device_macs WHERE device_id=?", (d["device_id"],))
        for mac_value in d.get("macs", []):
            con.execute("INSERT INTO device_macs(mac,device_id) VALUES(?,?)", (mac_value.lower(), d["device_id"]))
    con.commit()
    con.close()
except Exception:
    try:
        # Remove the just-added alias while preserving the previous registry.
        target["macs"].remove(MAC)
        with open(REGISTRY, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
    except Exception:
        pass
    raise

print(f"Approved MAC {MAC} for device_id {DEVICE_ID} ({target['name']})")
PY
