#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Graype
"""Small, dependency-free policy database interface.

The evaluator/enforcer is deliberately separate; this command only manages
validated policy records for the future LuCI frontend and enforcement daemon.
"""
from __future__ import annotations
import argparse, json, os, re, sqlite3, sys, tempfile, time
from datetime import datetime

DB_DEFAULT = "/var/lib/r3s-accounting/accounting.sqlite3"
REGISTRY_DEFAULT = "/etc/r3s-accounting/devices.json"
EXCLUDE_DEFAULT = "/etc/r3s-accounting/excluded_str"

def schedule_active(days_text, start, end, day, minute):
    if not days_text:
        return False
    days = {int(x) for x in days_text.split(',') if x.strip().isdigit()}
    if start is None or end is None or not days:
        return False
    if start <= end:
        return day in days and start <= minute < end
    prev_day = (day - 1) % 7
    return (day in days and minute >= start) or (prev_day in days and minute < end)

def db(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con

def load_registry(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("version") != 1 or not isinstance(data.get("devices"), list):
        raise SystemExit("unsupported device registry")
    return data

def save_registry(path, data):
    fd, temporary = tempfile.mkstemp(prefix="devices.json.", dir=os.path.dirname(path), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temporary, os.stat(path).st_mode & 0o777)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise

def provision_registry(con, data):
    """Apply registry metadata and MACs without changing historical usage."""
    devices = data["devices"]
    seen_ids, seen_macs = set(), set()
    for device in devices:
        device_id = device.get("device_id")
        name, group = device.get("name"), device.get("group_name")
        macs = device.get("macs", [])
        if not isinstance(device_id, int) or device_id <= 0:
            raise SystemExit("invalid device_id in registry")
        if device_id in seen_ids:
            raise SystemExit(f"duplicate device_id in registry: {device_id}")
        if not isinstance(name, str) or not name or not isinstance(group, str) or not group:
            raise SystemExit(f"invalid device metadata for device_id {device_id}")
        if not isinstance(macs, list):
            raise SystemExit(f"invalid MAC list for device_id {device_id}")
        seen_ids.add(device_id)
        for mac in macs:
            mac = str(mac).lower()
            if not re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", mac):
                raise SystemExit(f"invalid MAC for device_id {device_id}: {mac!r}")
            if mac in seen_macs:
                raise SystemExit(f"duplicate MAC in registry: {mac}")
            seen_macs.add(mac)
    try:
        for device in devices:
            con.execute(
                "INSERT INTO devices(device_id,name,group_name,enabled) VALUES(?,?,?,?) "
                "ON CONFLICT(device_id) DO UPDATE SET name=excluded.name, group_name=excluded.group_name, enabled=excluded.enabled",
                (device["device_id"], device["name"], device["group_name"], 1 if device.get("enabled", True) else 0),
            )
            con.execute("DELETE FROM device_macs WHERE device_id=?", (device["device_id"],))
            for mac in device.get("macs", []):
                con.execute("INSERT INTO device_macs(mac,device_id) VALUES(?,?)", (mac.lower(), device["device_id"]))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return len(devices), len(seen_macs)

def list_policies(con):
    rows = con.execute("""SELECT p.policy_id,p.name,p.policy_type,p.service,p.domains,
        p.quota_bytes,p.period,p.days,p.start_minute,p.end_minute,p.enabled,
        p.manual_override,group_concat(d.name, ', ') devices,
        group_concat(pd.device_id, ',') device_ids
        FROM policy_rules p LEFT JOIN policy_rule_devices pd ON pd.policy_id=p.policy_id
        LEFT JOIN devices d ON d.device_id=pd.device_id GROUP BY p.policy_id ORDER BY p.policy_id""")
    for r in rows:
        print(f"{r['policy_id']}\t{r['name']}\t{r['policy_type']}\t{r['service']}\t"
              f"{r['domains'] or '-'}\t{r['quota_bytes'] or '-'}\t{r['days'] or '-'}\t{r['start_minute'] if r['start_minute'] is not None else '-'}\t"
              f"{r['end_minute'] if r['end_minute'] is not None else '-'}\t{r['enabled']}\t"
              f"{r['manual_override']}\t{r['devices'] or '-'}\t{r['device_ids'] or '-'}")

def list_devices(con):
    rows = con.execute("""SELECT device_id,name,group_name,enabled
        FROM devices ORDER BY enabled DESC,group_name,name,device_id""")
    for r in rows:
        print(f"{r['device_id']}\t{r['name']}\t{r['group_name']}\t{r['enabled']}")

def list_unresolved(con):
    rows = con.execute("""SELECT ia.ip,ia.mac,
        COALESCE(SUM(uu.rx_bytes + uu.tx_bytes), 0) total_bytes
        FROM ip_assignments ia
        LEFT JOIN device_macs dm ON dm.mac=ia.mac
        LEFT JOIN unresolved_usage uu ON uu.ip=ia.ip
        WHERE dm.mac IS NULL
        GROUP BY ia.ip,ia.mac
        ORDER BY total_bytes DESC,ia.ip""")
    for r in rows:
        print(f"{r['ip']}\t{r['mac']}\t{r['total_bytes']}")

def exclusions(args):
    if args.text is None:
        try:
            with open(EXCLUDE_DEFAULT, "r", encoding="utf-8") as stream:
                print(stream.read(), end="")
        except FileNotFoundError:
            return
        return
    if len(args.text) > 16384 or "\x00" in args.text:
        raise SystemExit("exclusion list is too large or contains a NUL byte")
    directory = os.path.dirname(EXCLUDE_DEFAULT)
    fd, temporary = tempfile.mkstemp(prefix="excluded_str.", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(args.text)
            if args.text and not args.text.endswith("\n"):
                stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, EXCLUDE_DEFAULT)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    print("Exclusion list saved")

def forget_unresolved(con, args):
    mac = args.mac.lower()
    if not re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", mac):
        raise SystemExit(f"invalid MAC: {args.mac}")
    row = con.execute("""SELECT ia.ip FROM ip_assignments ia
        LEFT JOIN device_macs dm ON dm.mac=ia.mac
        WHERE ia.mac=? AND dm.mac IS NULL""", (mac,)).fetchone()
    if row is None:
        raise SystemExit(f"unresolved MAC {mac} not found")
    try:
        con.execute("DELETE FROM ip_assignments WHERE mac=?", (mac,))
        con.commit()
    except Exception:
        con.rollback()
        raise
    print(f"Forgot unresolved MAC {mac} at {row['ip']}; historical usage retained")

def associate_mac(con, args):
    mac = args.mac.lower()
    if not re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", mac):
        raise SystemExit(f"invalid MAC: {args.mac}")

    data = load_registry(args.registry)
    target = next((d for d in data["devices"] if d.get("device_id") == args.device), None)
    if target is None:
        raise SystemExit(f"device_id {args.device} not found in registry")
    for device in data["devices"]:
        if mac in [str(value).lower() for value in device.get("macs", [])]:
            if device.get("device_id") == args.device:
                print(f"MAC already approved: {mac} -> device_id {args.device}")
                return
            raise SystemExit(f"MAC already assigned to device_id {device.get('device_id')}")

    target.setdefault("macs", []).append(mac)
    save_registry(args.registry, data)

    try:
        con.execute("INSERT INTO device_macs(mac,device_id) VALUES(?,?)", (mac, args.device))
        con.commit()
    except Exception:
        con.rollback()
        raise
    print(f"Approved MAC {mac} for device_id {args.device} ({target['name']})")

def sync_devices(con, args):
    count, mac_count = provision_registry(con, load_registry(args.registry))
    print(f"Provisioned {count} devices and {mac_count} MAC mappings")

def add_device(con, args):
    name = args.name.strip()
    group = args.group.strip()
    mac = args.mac.lower()
    if not name or len(name) > 80 or any(ord(char) < 32 for char in name):
        raise SystemExit("invalid device name")
    if group not in ("Personal", "Kids", "IoT"):
        raise SystemExit("group must be Personal, Kids, or IoT")
    if not re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", mac):
        raise SystemExit(f"invalid MAC: {args.mac}")
    data = load_registry(args.registry)
    if any(device.get("name") == name for device in data["devices"]):
        raise SystemExit(f"device name already exists: {name}")
    if any(mac in [str(value).lower() for value in device.get("macs", [])] for device in data["devices"]):
        raise SystemExit(f"MAC already assigned: {mac}")
    device_id = max((int(device["device_id"]) for device in data["devices"]), default=0) + 1
    data["devices"].append({
        "device_id": device_id, "name": name, "group_name": group,
        "enabled": True, "macs": [mac],
    })
    save_registry(args.registry, data)
    provision_registry(con, data)
    print(f"Registered device_id {device_id}: {name} ({group})")

def delete_device(con, args):
    data = load_registry(args.registry)
    target = next((device for device in data["devices"] if device.get("device_id") == args.device), None)
    if target is None:
        raise SystemExit(f"device_id {args.device} not found in registry")
    data["devices"] = [device for device in data["devices"] if device is not target]
    save_registry(args.registry, data)
    try:
        # Keep rows that reference this ID for historical accounting and policies,
        # but make the identity ineligible for future attribution/enforcement.
        con.execute("DELETE FROM device_macs WHERE device_id=?", (args.device,))
        con.execute("UPDATE devices SET enabled=0 WHERE device_id=?", (args.device,))
        con.commit()
    except Exception:
        con.rollback()
        raise
    print(f"Removed device_id {args.device}: {target['name']}; historical usage retained")

def policy_values(con, args):
    name = args.name.strip()
    if not name or len(name) > 80 or any(ord(char) < 32 for char in name):
        raise SystemExit('--name must be 1-80 printable characters')
    if args.type not in ('service_quota', 'schedule'):
        raise SystemExit('--type must be service_quota or schedule')
    if args.service not in ('internet', 'custom'):
        raise SystemExit('--service must be internet or custom')

    domains = None
    if args.service == 'custom':
        if len(args.domains or '') > 4096:
            raise SystemExit('--domains must be no longer than 4096 characters total')
        values = [value.strip().lower().rstrip('.') for value in (args.domains or '').split(',') if value.strip()]
        hostname = r"(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        if not values or any(not re.fullmatch(hostname, value) for value in values):
            raise SystemExit('--domains must contain valid comma-separated hostnames for custom service')
        domains = ','.join(dict.fromkeys(values))
    elif args.domains:
        raise SystemExit('--domains is only valid with --service custom')

    if args.type == 'service_quota':
        if args.quota is None or args.quota <= 0:
            raise SystemExit('--quota must be positive for service_quota')
        quota = args.quota
        period, days, start, end = 'daily', None, None, None
    else:
        if args.quota is not None:
            raise SystemExit('--quota is only valid for service_quota')
        if args.start is None or args.end is None:
            raise SystemExit('--start and --end are required for schedule')
        if not 0 <= args.start <= 1439 or not 0 <= args.end <= 1439:
            raise SystemExit('--start and --end must be between 00:00 and 23:59')
        raw_days = [value.strip() for value in (args.days or '').split(',') if value.strip()]
        if not raw_days or any(not value.isdigit() or not 0 <= int(value) <= 6 for value in raw_days):
            raise SystemExit('--days must contain comma-separated weekday numbers from 0 to 6')
        days = ','.join(str(value) for value in sorted({int(value) for value in raw_days}))
        period, quota, start, end = None, None, args.start, args.end

    device_ids = []
    for raw in args.devices:
        try:
            device_id = int(raw)
        except (TypeError, ValueError):
            raise SystemExit(f'invalid device_id: {raw}')
        if device_id <= 0 or device_id in device_ids:
            raise SystemExit(f'invalid or duplicate device_id: {raw}')
        if con.execute('SELECT 1 FROM devices WHERE device_id=? AND enabled=1', (device_id,)).fetchone() is None:
            raise SystemExit(f'enabled device_id not found: {device_id}')
        device_ids.append(device_id)
    if not device_ids:
        raise SystemExit('at least one enabled device is required')

    return name, args.type, args.service, domains, quota if args.type == 'service_quota' else None, period, days, start, end, device_ids


def add_policy(con, args):
    now = int(time.time())
    name, policy_type, service, domains, quota, period, days, start, end, device_ids = policy_values(con, args)
    cur = con.execute("""INSERT INTO policy_rules
        (name,policy_type,service,domains,quota_bytes,period,days,start_minute,end_minute,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (name,policy_type,service,domains,quota,
        period,days,start,end,now,now))
    pid = cur.lastrowid
    for device_id in device_ids:
        con.execute("INSERT INTO policy_rule_devices(policy_id,device_id) VALUES(?,?)", (pid,device_id))
    con.commit()
    print(pid)


def edit_policy(con, args):
    old = con.execute(
        """SELECT policy_id,name,policy_type,service,domains,quota_bytes,period,days,
                  start_minute,end_minute
           FROM policy_rules WHERE policy_id=?""",
        (args.policy_id,),
    ).fetchone()
    if old is None:
        raise SystemExit(f"policy_id {args.policy_id} not found")
    values = policy_values(con, args)
    name, policy_type, service, domains, quota, period, days, start, end, device_ids = values
    duplicate = con.execute(
        "SELECT 1 FROM policy_rules WHERE name=? AND policy_id<>?", (name, args.policy_id)
    ).fetchone()
    if duplicate is not None:
        raise SystemExit(f'policy name already exists: {name}')

    old_devices = {
        int(row['device_id']) for row in con.execute(
            'SELECT device_id FROM policy_rule_devices WHERE policy_id=?', (args.policy_id,)
        )
    }
    scope_changed = (
        old['policy_type'], old['service'], old['domains'], old['period'], old['days'],
        old['start_minute'], old['end_minute'], old_devices
    ) != (policy_type, service, domains, period, days, start, end, set(device_ids))
    try:
        con.execute("""UPDATE policy_rules SET name=?,policy_type=?,service=?,domains=?,
            quota_bytes=?,period=?,days=?,start_minute=?,end_minute=?,updated_at=?
            WHERE policy_id=?""", (name,policy_type,service,domains,quota,period,days,start,end,
            int(time.time()),args.policy_id))
        con.execute('DELETE FROM policy_rule_devices WHERE policy_id=?', (args.policy_id,))
        for device_id in device_ids:
            con.execute(
                'INSERT INTO policy_rule_devices(policy_id,device_id) VALUES(?,?)',
                (args.policy_id, device_id),
            )
        if scope_changed:
            # A current quota measurement belongs to the old service/device scope.
            # Historical accounting remains untouched.
            con.execute('DELETE FROM policy_state WHERE policy_id=?', (args.policy_id,))
            con.execute('DELETE FROM policy_usage_charges WHERE policy_id=?', (args.policy_id,))
            con.execute('DELETE FROM policy_usage_reset_points WHERE policy_id=?', (args.policy_id,))
        con.commit()
    except Exception:
        con.rollback()
        raise
    suffix = '; today\'s quota state reset because the policy scope changed' if scope_changed else ''
    print(f'Updated policy {args.policy_id}: {name}{suffix}')

def delete_policy(con, args):
    policy = con.execute(
        "SELECT name FROM policy_rules WHERE policy_id=?", (args.policy_id,)
    ).fetchone()
    if policy is None:
        raise SystemExit(f"policy_id {args.policy_id} not found")
    try:
        # Delete dependants explicitly because older databases may not have
        # SQLite foreign-key enforcement enabled on their connection.
        con.execute("DELETE FROM policy_state WHERE policy_id=?", (args.policy_id,))
        con.execute("DELETE FROM policy_usage_charges WHERE policy_id=?", (args.policy_id,))
        con.execute("DELETE FROM policy_usage_reset_points WHERE policy_id=?", (args.policy_id,))
        con.execute("DELETE FROM policy_rule_devices WHERE policy_id=?", (args.policy_id,))
        con.execute("DELETE FROM policy_rules WHERE policy_id=?", (args.policy_id,))
        con.commit()
    except Exception:
        con.rollback()
        raise
    print(f"Deleted policy {args.policy_id}: {policy['name']}")

def quota_policy(con, policy_id):
    policy = con.execute(
        "SELECT policy_id,name,policy_type,quota_bytes FROM policy_rules WHERE policy_id=?", (policy_id,)
    ).fetchone()
    if policy is None:
        raise SystemExit(f"policy_id {policy_id} not found")
    if policy['policy_type'] != 'service_quota':
        raise SystemExit(f"policy_id {policy_id} is not a service quota policy")
    return policy

def set_quota(con, args):
    if args.quota <= 0:
        raise SystemExit('--quota must be positive')
    policy = quota_policy(con, args.policy_id)
    try:
        con.execute("UPDATE policy_rules SET quota_bytes=?,updated_at=? WHERE policy_id=?",
                    (args.quota, int(time.time()), args.policy_id))
        con.commit()
    except Exception:
        con.rollback()
        raise
    print(f"Updated daily quota for policy {args.policy_id}: {policy['name']} to {args.quota} bytes")

def reset_quota(con, args):
    """Start fresh evidence-backed measurement from the next safe bucket.

    Existing custom-policy charge records are deleted and a reset point stops
    prior destination buckets being replayed.  The currently open 15-minute
    bucket can be included on the next pass; the block is released promptly.
    """
    policy = quota_policy(con, args.policy_id)
    today = datetime.now().strftime('%Y-%m-%d')
    now = int(time.time())
    devices = con.execute("SELECT device_id FROM policy_rule_devices WHERE policy_id=?",
                          (args.policy_id,)).fetchall()
    try:
        for device in devices:
            con.execute("""DELETE FROM policy_usage_charges
                           WHERE policy_id=? AND device_id=? AND period_start=?""",
                        (args.policy_id, device['device_id'], today))
            con.execute("""INSERT INTO policy_usage_reset_points
                           (policy_id,device_id,period_start,reset_at) VALUES(?,?,?,?)
                           ON CONFLICT(policy_id,device_id,period_start) DO UPDATE SET
                               reset_at=excluded.reset_at""",
                        (args.policy_id, device['device_id'], today, now))
            con.execute("""INSERT INTO policy_state
                (policy_id,device_id,period_start,service_bytes,blocked,evaluated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(policy_id,device_id,period_start) DO UPDATE SET
                    service_bytes=0,blocked=0,evaluated_at=excluded.evaluated_at""",
                (args.policy_id, device['device_id'], today, 0, 0, now))
        con.commit()
    except Exception:
        con.rollback()
        raise
    print(f"Reset today's quota for policy {args.policy_id}: {policy['name']} ({len(devices)} device(s)); applies within 10 seconds")

def evaluate(con):
    # Schedules use the router's local time (System > Time zone). Avoid
    # zoneinfo's optional tzdata dependency on minimal OpenWrt images.
    now = datetime.now()
    minute = now.hour * 60 + now.minute
    # Python weekday is Monday=0; policy representation is Sunday=0.
    day = (now.weekday() + 1) % 7
    for p in con.execute("SELECT * FROM policy_rules WHERE enabled=1 ORDER BY policy_id"):
        devices = con.execute("SELECT d.device_id,d.name FROM policy_rule_devices x JOIN devices d ON d.device_id=x.device_id WHERE x.policy_id=? AND d.enabled=1 ORDER BY d.device_id", (p['policy_id'],)).fetchall()
        for d in devices:
            decision = 'allow'
            reason = 'outside schedule'
            if p['manual_override'] == 'block':
                decision, reason = 'block', 'manual override'
            elif p['manual_override'] == 'allow':
                reason = 'manual override'
            elif p['policy_type'] == 'schedule' and p['days']:
                start, end = p['start_minute'], p['end_minute']
                active = schedule_active(p['days'], start, end, day, minute)
                if active:
                    decision, reason = 'block', 'schedule active'
            elif p['policy_type'] == 'service_quota':
                today = now.strftime('%Y-%m-%d')
                state = con.execute("""SELECT service_bytes,blocked FROM policy_state
                    WHERE policy_id=? AND device_id=? AND period_start=?""",
                    (p['policy_id'], d['device_id'], today)).fetchone()
                used = int(state['service_bytes']) if state else 0
                quota = int(p['quota_bytes'] or 0)
                blocked = int(state['blocked']) if state else 0
                if p['manual_override'] == 'block' or blocked:
                    decision = 'block'
                elif p['manual_override'] == 'allow':
                    decision = 'allow'
                else:
                    decision = 'allow' if used < quota else 'block'
                evidence = con.execute("""SELECT domain,destination_ip FROM policy_usage_charges
                    WHERE policy_id=? AND device_id=? AND period_start=?
                    ORDER BY bucket_start_utc DESC, charged_bytes DESC LIMIT 1""",
                    (p['policy_id'], d['device_id'], today)).fetchone()
                reason = f"{used}/{quota} bytes"
                if evidence:
                    reason += f"; {evidence['domain']} -> {evidence['destination_ip']}"
            print(f"{p['name']}\t{d['device_id']}\t{d['name']}\t{decision}\t{reason}")

def main():
    p = argparse.ArgumentParser(prog='r3s-policy')
    p.add_argument('--db', default=DB_DEFAULT)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    sub.add_parser('devices', help='list valid device IDs and names')
    sub.add_parser('unresolved', help='list current IP/MAC observations without a device')
    sub.add_parser('evaluate', help='show current decisions; does not enforce')
    a = sub.add_parser('sync-devices', help='provision all devices from the registry')
    a.add_argument('--registry', default=REGISTRY_DEFAULT)
    a = sub.add_parser('add-device', help='register a new device and MAC in the registry')
    a.add_argument('--name', required=True)
    a.add_argument('--group', required=True, choices=['Personal', 'Kids', 'IoT'])
    a.add_argument('--mac', required=True)
    a.add_argument('--registry', default=REGISTRY_DEFAULT)
    a = sub.add_parser('delete-device', help='remove a device from the registry and disable it')
    a.add_argument('--device', type=int, required=True)
    a.add_argument('--registry', default=REGISTRY_DEFAULT)
    a = sub.add_parser('associate-mac', help='approve a MAC alias for an existing device')
    a.add_argument('--device', type=int, required=True)
    a.add_argument('--mac', required=True)
    a.add_argument('--registry', default=REGISTRY_DEFAULT)
    a = sub.add_parser('exclusions', help='show or replace the AdGuard export exclusion list')
    a.add_argument('--text', help='complete newline-delimited exclusion text')
    a = sub.add_parser('forget-unresolved', help='forget a stale unresolved current MAC observation')
    a.add_argument('--mac', required=True)
    a = sub.add_parser('add')
    a.add_argument('--name', required=True); a.add_argument('--type', choices=['service_quota','schedule'], required=True)
    a.add_argument('--service', choices=['internet','custom'], default='internet'); a.add_argument('--domains', help='comma-separated hostnames for custom service')
    a.add_argument('--quota', type=int)
    a.add_argument('--days', help='comma-separated weekdays, 0=Sunday')
    a.add_argument('--start', type=int, help='minutes after midnight')
    a.add_argument('--end', type=int, help='minutes after midnight')
    a.add_argument('--devices', nargs='+', required=True)
    a = sub.add_parser('edit', help='edit a policy without changing its ID')
    a.add_argument('--policy-id', type=int, required=True)
    a.add_argument('--name', required=True); a.add_argument('--type', choices=['service_quota','schedule'], required=True)
    a.add_argument('--service', choices=['internet','custom'], required=True); a.add_argument('--domains')
    a.add_argument('--quota', type=int)
    a.add_argument('--days', help='comma-separated weekdays, 0=Sunday')
    a.add_argument('--start', type=int, help='minutes after midnight')
    a.add_argument('--end', type=int, help='minutes after midnight')
    a.add_argument('--devices', nargs='+', required=True)
    a = sub.add_parser('delete', help='delete a policy and its stored state')
    a.add_argument('--policy-id', type=int, required=True)
    a = sub.add_parser('set-quota', help='change the daily limit of a quota policy')
    a.add_argument('--policy-id', type=int, required=True)
    a.add_argument('--quota', type=int, required=True, help='new daily quota in bytes')
    a = sub.add_parser('reset-quota', help="reset today's measured quota for a policy")
    a.add_argument('--policy-id', type=int, required=True)
    args = p.parse_args()
    try:
        with db(args.db) as con:
            list_policies(con) if args.command == 'list' else list_devices(con) if args.command == 'devices' else list_unresolved(con) if args.command == 'unresolved' else sync_devices(con, args) if args.command == 'sync-devices' else add_device(con, args) if args.command == 'add-device' else delete_device(con, args) if args.command == 'delete-device' else associate_mac(con, args) if args.command == 'associate-mac' else forget_unresolved(con, args) if args.command == 'forget-unresolved' else exclusions(args) if args.command == 'exclusions' else evaluate(con) if args.command == 'evaluate' else add_policy(con, args) if args.command == 'add' else edit_policy(con, args) if args.command == 'edit' else delete_policy(con, args) if args.command == 'delete' else set_quota(con, args) if args.command == 'set-quota' else reset_quota(con, args)
    except sqlite3.Error as exc:
        print(f"r3s-policy: database error: {exc}", file=sys.stderr); return 1
    return 0

if __name__ == '__main__': raise SystemExit(main())
