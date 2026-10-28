#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Graype
"""R3S Traffic Accounting Collector v2.0.0.

Canonical Python collector for OpenWrt R3S accounting.

Reads nftables JSON counters, resolves current IP identities from DHCP and
neighbour state, records immutable IP-to-MAC observations, and commits usage
and baselines atomically to SQLite.

The collector is deliberately one-shot. The init service invokes it once
per sampling interval.
"""
from __future__ import annotations

import ipaddress
import base64
import binascii
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

DB = "/var/lib/r3s-accounting/accounting.sqlite3"
NFT_TABLE = "r3s_accounting"
DHCP_LEASES = "/tmp/dhcp.leases"
# AdGuard Home query log, used for per-site (custom domain) quotas. The
# 'adguard_querylog' UCI option wins; otherwise prefer persistent /opt (/var/lib
# is RAM on OpenWrt), then the package default.
ADGUARD_QUERYLOG_PERSISTENT = "/opt/adguardhome/data/querylog.json"
ADGUARD_QUERYLOG_LEGACY = "/var/lib/adguardhome/data/querylog.json"
ADGUARD_QUERYLOG = (
    ADGUARD_QUERYLOG_PERSISTENT
    if os.path.exists(ADGUARD_QUERYLOG_PERSISTENT)
    else ADGUARD_QUERYLOG_LEGACY
)
ACCOUNTING_UCI = "/etc/config/r3s-accounting"
PROC_STAT = "/proc/stat"
PROC_MEMINFO = "/proc/meminfo"
PROC_LOADAVG = "/proc/loadavg"
RESOURCE_ALERT_THRESHOLD_X100 = 9000
RESOURCE_ALERT_DURATION = 3600
RESOURCE_SAMPLE_GAP = 90
DEFAULT_RETENTION_DAYS = 14
DESTINATION_BUCKET_SECONDS = 15 * 60
QUERYLOG_INITIAL_READ_BYTES = 4 * 1024 * 1024


def fail(message: str) -> None:
    print(f"r3s-accounting: ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def command_path(name: str, preferred: str | None = None) -> str:
    """Return a usable command path without assuming one OpenWrt layout."""
    if preferred and os.access(preferred, os.X_OK):
        return preferred
    found = shutil.which(name)
    if found:
        return found
    fail(f"command not found: {name}")
    raise AssertionError


NFT = command_path("nft", "/usr/sbin/nft")
IP = command_path("ip", "/usr/sbin/ip")


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        fail(f"command not found: {command[0]}")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        fail(
            f"command failed ({exc.returncode}): "
            f"{' '.join(command)}" + (f": {detail}" if detail else "")
        )
    raise AssertionError


def read_nft_set(setname: str) -> dict[str, dict[str, int]]:
    result = run_command(
        [NFT, "-j", "list", "set", "inet", NFT_TABLE, setname]
    )

    try:
        document: Any = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        fail(f"invalid nft JSON for {setname}: {exc}")

    if not isinstance(document, dict):
        fail(f"unexpected nft JSON root for {setname}")

    nftables = document.get("nftables", [])
    if not isinstance(nftables, list):
        fail(f"unexpected nftables JSON structure for {setname}")

    counters: dict[str, dict[str, int]] = {}

    for item in nftables:
        if not isinstance(item, dict):
            continue
        set_object = item.get("set")
        if not isinstance(set_object, dict):
            continue
        elements = set_object.get("elem", [])
        if not isinstance(elements, list):
            continue

        for wrapper in elements:
            if not isinstance(wrapper, dict):
                continue
            element = wrapper.get("elem")
            if not isinstance(element, dict):
                continue

            value = element.get("val")
            counter = element.get("counter")
            if value is None or not isinstance(counter, dict):
                continue

            ip = str(value)
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                # Ignore non-IP set elements rather than corrupting accounting.
                continue

            try:
                packets = int(counter.get("packets", 0))
                bytes_count = int(counter.get("bytes", 0))
            except (TypeError, ValueError):
                fail(f"invalid nft counter for {setname} element {ip}")

            if packets < 0 or bytes_count < 0:
                fail(f"negative nft counter for {setname} element {ip}")

            counters[ip] = {"packets": packets, "bytes": bytes_count}

    return counters


def read_nft_destination_set(
    setname: str,
) -> dict[tuple[str, str], dict[str, int]]:
    """Read nft concatenated client/destination counters (one IP family per set)."""
    result = run_command(
        [NFT, "-j", "list", "set", "inet", NFT_TABLE, setname]
    )

    try:
        document: Any = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        fail(f"invalid nft JSON for {setname}: {exc}")

    if not isinstance(document, dict):
        fail(f"unexpected nft JSON root for {setname}")

    counters: dict[tuple[str, str], dict[str, int]] = {}
    for item in document.get("nftables", []):
        if not isinstance(item, dict):
            continue
        set_object = item.get("set")
        if not isinstance(set_object, dict):
            continue
        for wrapper in set_object.get("elem", []):
            if not isinstance(wrapper, dict):
                continue
            element = wrapper.get("elem")
            if not isinstance(element, dict):
                continue
            value = element.get("val")
            if isinstance(value, dict) and isinstance(value.get("concat"), list):
                value = value["concat"]
            if not isinstance(value, list) or len(value) != 2:
                continue
            client_ip, destination_ip = (str(value[0]), str(value[1]))
            try:
                if (ipaddress.ip_address(client_ip).version !=
                        ipaddress.ip_address(destination_ip).version):
                    continue
            except ValueError:
                continue
            counter = element.get("counter")
            if not isinstance(counter, dict):
                continue
            try:
                packets = int(counter.get("packets", 0))
                bytes_count = int(counter.get("bytes", 0))
            except (TypeError, ValueError):
                fail(f"invalid nft counter for {setname} element {value}")
            if packets < 0 or bytes_count < 0:
                fail(f"negative nft counter for {setname} element {value}")
            counters[(client_ip, destination_ip)] = {
                "packets": packets,
                "bytes": bytes_count,
            }
    return counters


def _skip_dns_name(packet: bytes, offset: int) -> int | None:
    while offset < len(packet):
        length = packet[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:
            return offset + 2
        if length & 0xC0 or length > 63:
            return None
        offset += 1 + length
    return None


def dns_a_answers(answer: str) -> set[str]:
    """Decode A and AAAA answers from an AdGuard base64 DNS response."""
    try:
        packet = base64.b64decode(answer, validate=True)
        if len(packet) < 12:
            return set()
        questions = int.from_bytes(packet[4:6], "big")
        answers = int.from_bytes(packet[6:8], "big")
        offset = 12
        for _ in range(questions):
            offset = _skip_dns_name(packet, offset) or len(packet)
            if offset + 4 > len(packet):
                return set()
            offset += 4
        result: set[str] = set()
        for _ in range(answers):
            offset = _skip_dns_name(packet, offset) or len(packet)
            if offset + 10 > len(packet):
                break
            record_type = int.from_bytes(packet[offset:offset + 2], "big")
            record_class = int.from_bytes(packet[offset + 2:offset + 4], "big")
            length = int.from_bytes(packet[offset + 8:offset + 10], "big")
            offset += 10
            if offset + length > len(packet):
                break
            if record_class == 1 and (
                (record_type == 1 and length == 4) or (record_type == 28 and length == 16)
            ):
                result.add(str(ipaddress.ip_address(packet[offset:offset + length])))
            offset += length
        return result
    except (ValueError, binascii.Error):
        return set()


def query_time(value: str) -> int | None:
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError, OverflowError):
        return None


def ingest_adguard_querylog(db: sqlite3.Connection, now: int) -> None:
    """Incrementally retain unique client DNS-name to IP observations."""
    candidates = [p for p in (uci_option("adguard_querylog"),
                              ADGUARD_QUERYLOG, ADGUARD_QUERYLOG_LEGACY) if p]
    path = next((p for p in candidates if os.path.exists(p)), ADGUARD_QUERYLOG)
    try:
        stat = os.stat(path)
        handle = open(path, "rb")
    except OSError:
        return

    row = db.execute(
        "SELECT inode,offset,pending FROM adguard_querylog_cursor WHERE path=?",
        (path,),
    ).fetchone()
    inode = int(stat.st_ino)
    offset = int(row[1]) if row is not None else max(
        0, int(stat.st_size) - QUERYLOG_INITIAL_READ_BYTES
    )
    pending = str(row[2]) if row is not None else ""
    if row is None or int(row[0]) != inode or int(stat.st_size) < offset:
        offset = 0
        pending = ""

    try:
        handle.seek(offset)
        data = pending.encode("utf-8") + handle.read()
        new_offset = handle.tell()
    finally:
        handle.close()

    if b"\n" in data:
        complete, remainder = data.rsplit(b"\n", 1)
        lines = complete.splitlines()
        pending = remainder.decode("utf-8", errors="replace")
    else:
        lines = []
        pending = data.decode("utf-8", errors="replace")

    for line in lines:
        try:
            record = json.loads(line)
            client_ip = str(record["IP"])
            domain = str(record["QH"]).rstrip(".")
            observed_at = query_time(str(record["T"]))
            answers = dns_a_answers(str(record.get("Answer", "")))
            if not client_ip or not domain or observed_at is None:
                continue
            client_ip = str(ipaddress.ip_address(client_ip))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        for destination_ip in answers:
            db.execute(
                "INSERT INTO destination_domain_observations "
                "(client_ip,destination_ip,domain,first_seen,last_seen,query_count) "
                "VALUES(?,?,?,?,?,1) ON CONFLICT(client_ip,destination_ip,domain) "
                "DO UPDATE SET first_seen=min(first_seen,excluded.first_seen), "
                "last_seen=max(last_seen,excluded.last_seen), "
                "query_count=query_count+1",
                (client_ip, destination_ip, domain, observed_at, observed_at),
            )

    db.execute(
        "INSERT INTO adguard_querylog_cursor(path,inode,offset,pending) "
        "VALUES(?,?,?,?) ON CONFLICT(path) DO UPDATE SET inode=excluded.inode, "
        "offset=excluded.offset,pending=excluded.pending",
        (path, inode, new_offset, pending),
    )


def load_dhcp_macs() -> dict[str, str]:
    """Read DHCP leases once per sample."""
    try:
        text = Path(DHCP_LEASES).read_text()
    except OSError:
        return {}

    result: dict[str, str] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        ip = fields[2]
        mac = fields[1].lower()
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            continue
        if re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", mac):
            result[ip] = mac
    return result


def load_neighbour_macs(ips: set[str]) -> dict[str, str]:
    """Resolve missing IPs in one neighbour-table read."""
    if not ips:
        return {}

    try:
        result = run_command([IP, "neigh", "show"])
    except SystemExit:
        raise

    resolved: dict[str, str] = {}
    mac_re = re.compile(r"lladdr ([0-9a-fA-F:]{17})")

    for line in result.stdout.splitlines():
        fields = line.split()
        if not fields or fields[0] not in ips:
            continue
        match = mac_re.search(line)
        if not match:
            continue
        mac = match.group(1).lower()
        if re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", mac):
            resolved[fields[0]] = mac

    return resolved


def resolve_macs(ips: set[str]) -> dict[str, str]:
    """Resolve identities from current DHCP/neighbour state only.

    Historical observations are deliberately not used as a current-IP
    fallback: an old IP-to-MAC mapping must never silently attribute new
    traffic to the wrong device after an address is reassigned.
    """
    dhcp = load_dhcp_macs()
    resolved = {ip: dhcp[ip] for ip in ips if ip in dhcp}
    missing = ips - set(resolved)
    resolved.update(load_neighbour_macs(missing))
    return resolved


def local_date(ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def local_hour(ts: int) -> int:
    return time.localtime(ts).tm_hour


def hour_start_utc(ts: int) -> int:
    """UTC epoch bucket, matching the established accounting schema."""
    return (ts // 3600) * 3600


def read_cpu_counters() -> tuple[int, int] | None:
    try:
        first = Path(PROC_STAT).read_text().splitlines()[0].split()
        if first[0] != "cpu" or len(first) < 5:
            return None
        values = [int(value) for value in first[1:]]
    except (IndexError, OSError, ValueError):
        return None

    # Linux reports idle and iowait separately. Both represent time not
    # actively spent executing work for this utilization calculation.
    total_ticks = sum(values)
    idle_ticks = values[3] + (values[4] if len(values) > 4 else 0)
    return total_ticks, idle_ticks


def read_memory() -> tuple[int, int] | None:
    values: dict[str, int] = {}
    try:
        for line in Path(PROC_MEMINFO).read_text().splitlines():
            key, _, raw = line.partition(":")
            if key in {"MemTotal", "MemAvailable"}:
                values[key] = int(raw.strip().split()[0]) * 1024
    except (IndexError, OSError, ValueError):
        return None

    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    if total <= 0 or available < 0:
        return None
    return total - available, total


def read_load_x100() -> int | None:
    try:
        value = float(Path(PROC_LOADAVG).read_text().split()[0])
    except (IndexError, OSError, ValueError):
        return None
    if value < 0:
        return None
    return int(round(value * 100))


def record_router_health(db: sqlite3.Connection, now: int) -> dict[str, int]:
    """Best-effort resource telemetry; never make routing depend on it."""
    memory = read_memory()
    load_x100 = read_load_x100()
    if memory is None or load_x100 is None:
        return {}

    memory_used, memory_total = memory
    cpu_percent_x100: int | None = None
    cpu = read_cpu_counters()
    if cpu is not None:
        total_ticks, idle_ticks = cpu
        previous = db.execute(
            "SELECT total_ticks,idle_ticks FROM router_health_cpu_baseline "
            "WHERE singleton=1"
        ).fetchone()
        if previous is not None:
            total_delta = total_ticks - int(previous[0])
            idle_delta = idle_ticks - int(previous[1])
            if total_delta > 0 and 0 <= idle_delta <= total_delta:
                cpu_percent_x100 = round(
                    (total_delta - idle_delta) * 10000 / total_delta
                )
        db.execute(
            "INSERT INTO router_health_cpu_baseline "
            "(singleton,total_ticks,idle_ticks,sampled_at) VALUES(1,?,?,?) "
            "ON CONFLICT(singleton) DO UPDATE SET total_ticks=excluded.total_ticks, "
            "idle_ticks=excluded.idle_ticks,sampled_at=excluded.sampled_at",
            (total_ticks, idle_ticks, now),
        )

    bucket = hour_start_utc(now)
    local = time.localtime(now)
    row = db.execute(
        "SELECT cpu_sum_x100,cpu_min_x100,cpu_max_x100,cpu_samples,"
        "memory_used_sum_bytes,memory_used_min_bytes,memory_used_max_bytes,"
        "sample_count,load_sum_x100,load_min_x100,load_max_x100 "
        "FROM router_health_hourly WHERE hour_start_utc=?",
        (bucket,),
    ).fetchone()
    if row is None:
        cpu_sum = cpu_percent_x100 or 0
        cpu_min = cpu_percent_x100
        cpu_max = cpu_percent_x100
        cpu_samples = 1 if cpu_percent_x100 is not None else 0
        db.execute(
            "INSERT INTO router_health_hourly "
            "(hour_start_utc,local_date,local_hour,cpu_sum_x100,cpu_min_x100,"
            "cpu_max_x100,cpu_samples,memory_used_sum_bytes,memory_used_min_bytes,"
            "memory_used_max_bytes,memory_total_bytes,load_sum_x100,load_min_x100,"
            "load_max_x100,sample_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                bucket,
                time.strftime("%Y-%m-%d", local),
                local.tm_hour,
                cpu_sum,
                cpu_min,
                cpu_max,
                cpu_samples,
                memory_used,
                memory_used,
                memory_used,
                memory_total,
                load_x100,
                load_x100,
                load_x100,
                1,
            ),
        )
        return {
            "memory": round(memory_used * 10000 / memory_total),
            **({"cpu": cpu_percent_x100} if cpu_percent_x100 is not None else {}),
        }

    cpu_sum = int(row[0]) + (cpu_percent_x100 or 0)
    cpu_samples = int(row[3]) + (1 if cpu_percent_x100 is not None else 0)
    cpu_min = row[1]
    cpu_max = row[2]
    if cpu_percent_x100 is not None:
        cpu_min = cpu_percent_x100 if cpu_min is None else min(int(cpu_min), cpu_percent_x100)
        cpu_max = cpu_percent_x100 if cpu_max is None else max(int(cpu_max), cpu_percent_x100)
    db.execute(
        "UPDATE router_health_hourly SET cpu_sum_x100=?,cpu_min_x100=?,"
        "cpu_max_x100=?,cpu_samples=?,memory_used_sum_bytes=?,"
        "memory_used_min_bytes=?,memory_used_max_bytes=?,memory_total_bytes=?,"
        "load_sum_x100=?,load_min_x100=?,load_max_x100=?,sample_count=? "
        "WHERE hour_start_utc=?",
        (
            cpu_sum,
            cpu_min,
            cpu_max,
            cpu_samples,
            int(row[4]) + memory_used,
            min(int(row[5]), memory_used),
            max(int(row[6]), memory_used),
            memory_total,
            int(row[8]) + load_x100,
            min(int(row[9]), load_x100),
            max(int(row[10]), load_x100),
            int(row[7]) + 1,
            bucket,
        ),
    )
    return {
        "memory": round(memory_used * 10000 / memory_total),
        **({"cpu": cpu_percent_x100} if cpu_percent_x100 is not None else {}),
    }


def update_router_health_alert(
    db: sqlite3.Connection, resource: str, value_x100: int, now: int
) -> None:
    row = db.execute(
        "SELECT high_since,last_sample_at,alerted FROM router_health_alert_state "
        "WHERE resource=?",
        (resource,),
    ).fetchone()
    if row is None:
        db.execute(
            "INSERT INTO router_health_alert_state "
            "(resource,high_since,last_sample_at,alerted,last_value_x100) "
            "VALUES(?,?,?,?,?)",
            (
                resource,
                now if value_x100 > RESOURCE_ALERT_THRESHOLD_X100 else None,
                now,
                0,
                value_x100,
            ),
        )
        return

    high_since = int(row[0]) if row[0] is not None else None
    last_sample_at = int(row[1]) if row[1] is not None else None
    alerted = int(row[2])
    if value_x100 > RESOURCE_ALERT_THRESHOLD_X100:
        if (
            high_since is None
            or last_sample_at is None
            or now - last_sample_at > RESOURCE_SAMPLE_GAP
        ):
            high_since = now
            alerted = 0
        if not alerted and now - high_since >= RESOURCE_ALERT_DURATION:
            db.execute(
                "INSERT INTO router_health_alerts "
                "(event_time,resource,event_type,value_x100) VALUES(?,?,?,?)",
                (now, resource, "warning", value_x100),
            )
            alerted = 1
    else:
        if alerted:
            db.execute(
                "INSERT INTO router_health_alerts "
                "(event_time,resource,event_type,value_x100) VALUES(?,?,?,?)",
                (now, resource, "recovery", value_x100),
            )
        high_since = None
        alerted = 0

    db.execute(
        "UPDATE router_health_alert_state SET high_since=?,last_sample_at=?,"
        "alerted=?,last_value_x100=? WHERE resource=?",
        (high_since, now, alerted, value_x100, resource),
    )


def open_database() -> sqlite3.Connection:
    try:
        db = sqlite3.connect(DB, timeout=5.0)
    except sqlite3.Error as exc:
        fail(f"cannot open SQLite database: {exc}")

    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def verify_database(db: sqlite3.Connection) -> None:
    row = db.execute("PRAGMA integrity_check").fetchone()
    integrity = row[0] if row else None
    if integrity != "ok":
        fail(f"SQLite integrity check failed: {integrity}")


def verify_nft_table() -> None:
    result = subprocess.run(
        [NFT, "list", "table", "inet", NFT_TABLE],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail(f"nftables table inet {NFT_TABLE} does not exist")


def record_identity(
    db: sqlite3.Connection,
    now: int,
    ip: str,
    mac: str | None,
) -> int | None:
    if not mac:
        return None

    row = db.execute(
        "SELECT device_id FROM device_macs WHERE mac=? LIMIT 1",
        (mac,),
    ).fetchone()
    device_id = int(row[0]) if row else None

    db.execute(
        "INSERT INTO ip_assignments(ip,mac,first_seen,last_seen) "
        "VALUES(?,?,?,?) "
        "ON CONFLICT(ip) DO UPDATE SET "
        "mac=excluded.mac,last_seen=excluded.last_seen",
        (ip, mac, now, now),
    )

    db.execute(
        "INSERT OR IGNORE INTO ip_mac_history(observed_at,ip,mac) "
        "VALUES(?,?,?)",
        (now, ip, mac),
    )

    return device_id


def record_usage(
    db: sqlite3.Connection,
    direction: str,
    ip: str,
    delta: int,
    device_id: int | None,
    now: int,
) -> None:
    if delta <= 0:
        return

    bucket = hour_start_utc(now)
    date = local_date(now)
    hour = local_hour(now)

    if device_id is not None:
        if direction == "rx":
            db.execute(
                "INSERT INTO usage_hourly "
                "(hour_start_utc,local_date,local_hour,device_id,rx_bytes,tx_bytes) "
                "VALUES(?,?,?,?,?,0) "
                "ON CONFLICT(hour_start_utc,device_id) DO UPDATE SET "
                "rx_bytes=rx_bytes+excluded.rx_bytes",
                (bucket, date, hour, device_id, delta),
            )
            db.execute(
                "INSERT INTO usage_daily "
                "(local_date,device_id,rx_bytes,tx_bytes) VALUES(?,?,?,0) "
                "ON CONFLICT(local_date,device_id) DO UPDATE SET "
                "rx_bytes=rx_bytes+excluded.rx_bytes",
                (date, device_id, delta),
            )
        else:
            db.execute(
                "INSERT INTO usage_hourly "
                "(hour_start_utc,local_date,local_hour,device_id,rx_bytes,tx_bytes) "
                "VALUES(?,?,?,?,0,?) "
                "ON CONFLICT(hour_start_utc,device_id) DO UPDATE SET "
                "tx_bytes=tx_bytes+excluded.tx_bytes",
                (bucket, date, hour, device_id, delta),
            )
            db.execute(
                "INSERT INTO usage_daily "
                "(local_date,device_id,rx_bytes,tx_bytes) VALUES(?,?,0,?) "
                "ON CONFLICT(local_date,device_id) DO UPDATE SET "
                "tx_bytes=tx_bytes+excluded.tx_bytes",
                (date, device_id, delta),
            )
    elif direction == "rx":
        db.execute(
            "INSERT INTO unresolved_usage "
            "(bucket_start_utc,local_date,ip,rx_bytes,tx_bytes) "
            "VALUES(?,?,?,?,0) "
            "ON CONFLICT(bucket_start_utc,ip) DO UPDATE SET "
            "rx_bytes=rx_bytes+excluded.rx_bytes",
            (bucket, date, ip, delta),
        )
    else:
        db.execute(
            "INSERT INTO unresolved_usage "
            "(bucket_start_utc,local_date,ip,rx_bytes,tx_bytes) "
            "VALUES(?,?,?,?,?) "
            "ON CONFLICT(bucket_start_utc,ip) DO UPDATE SET "
            "tx_bytes=tx_bytes+excluded.tx_bytes",
            (bucket, date, ip, 0, delta),
        )


def record_destination_usage(
    db: sqlite3.Connection,
    direction: str,
    client_ip: str,
    destination_ip: str,
    delta: int,
    device_id: int | None,
    now: int,
) -> None:
    if delta <= 0:
        return
    bucket = (now // DESTINATION_BUCKET_SECONDS) * DESTINATION_BUCKET_SECONDS
    bucket_local = time.localtime(bucket)
    identity_key = (
        f"device:{device_id}" if device_id is not None else f"ip:{client_ip}"
    )
    column = "rx_bytes" if direction == "rx" else "tx_bytes"
    db.execute(
        "INSERT INTO destination_usage_quarterly "
        "(quarter_start_utc,local_date,local_hour,local_minute,identity_key,"
        "device_id,client_ip,destination_ip,rx_bytes,tx_bytes) VALUES(?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(quarter_start_utc,identity_key,destination_ip) DO UPDATE SET "
        f"{column}={column}+excluded.{column}",
        (
            bucket,
            time.strftime("%Y-%m-%d", bucket_local),
            bucket_local.tm_hour,
            bucket_local.tm_min,
            identity_key,
            device_id,
            client_ip,
            destination_ip,
            delta if direction == "rx" else 0,
            delta if direction == "tx" else 0,
        ),
    )


def read_nft_table_handle() -> int | None:
    """Return the kernel handle for the accounting table, when available."""
    result = run_command([NFT, "-j", "list", "tables"])
    try:
        document: Any = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        fail(f"invalid nft JSON for table list: {exc}")
    for item in document.get("nftables", []) if isinstance(document, dict) else []:
        table = item.get("table") if isinstance(item, dict) else None
        if (isinstance(table, dict) and table.get("family") == "inet"
                and table.get("name") == NFT_TABLE):
            try:
                return int(table["handle"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


def meta_get(db: sqlite3.Connection, key: str) -> str | None:
    row = db.execute("SELECT value FROM schema_meta WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else None


def meta_set(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        "INSERT INTO schema_meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def counters_initialized(db: sqlite3.Connection, key: str, table: str) -> bool:
    """Recognise both persistent markers and pre-marker upgraded databases."""
    if meta_get(db, key) == "1":
        return True
    return db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None


def counter_delta(
    row: tuple | None,
    current_bytes: int,
    epoch: int,
    initialized: bool,
    recreated: bool,
) -> tuple[int, bool]:
    """Return bytes to record and whether a counter reset was observed."""
    if not initialized:
        return 0, False
    if recreated:
        return current_bytes, row is not None
    if row is None:
        return current_bytes, False
    if int(row[0]) != epoch:
        return 0, True
    previous_bytes = int(row[2])
    if current_bytes >= previous_bytes:
        return current_bytes - previous_bytes, False
    return current_bytes, True


def process_direction(
    db: sqlite3.Connection,
    direction: str,
    data: dict[str, dict[str, int]],
    identities: dict[str, str],
    now: int,
    epoch: int,
    initialized: bool,
    recreated: bool,
) -> int:
    total = 0

    for ip, current in data.items():
        row = db.execute(
            "SELECT epoch_id,packets,bytes FROM counter_baselines "
            "WHERE direction=? AND ip=? LIMIT 1",
            (direction, ip),
        ).fetchone()

        delta, reset = counter_delta(
            row, current["bytes"], epoch, initialized, recreated
        )

        mac = identities.get(ip)
        device_id = record_identity(db, now, ip, mac)

        if reset:
            db.execute(
                "INSERT INTO health_events "
                "(event_time,severity,event_type,message) VALUES(?,?,?,?)",
                (now, "warning", "counter_reset", f"{direction} counter reset for {ip}"),
            )
        record_usage(db, direction, ip, delta, device_id, now)

        db.execute(
            "INSERT INTO counter_baselines "
            "(direction,ip,epoch_id,packets,bytes,sampled_at) "
            "VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(direction,ip) DO UPDATE SET "
            "epoch_id=excluded.epoch_id,packets=excluded.packets, "
            "bytes=excluded.bytes,sampled_at=excluded.sampled_at",
            (
                direction,
                ip,
                epoch,
                current["packets"],
                current["bytes"],
                now,
            ),
        )

        total += delta

    return total


def process_destination_direction(
    db: sqlite3.Connection,
    direction: str,
    data: dict[tuple[str, str], dict[str, int]],
    identities: dict[str, str],
    now: int,
    epoch: int,
    initialized: bool,
    recreated: bool,
) -> int:
    total = 0
    for (client_ip, destination_ip), current in data.items():
        row = db.execute(
            "SELECT epoch_id,packets,bytes FROM destination_counter_baselines "
            "WHERE direction=? AND client_ip=? AND destination_ip=?",
            (direction, client_ip, destination_ip),
        ).fetchone()
        delta, reset = counter_delta(
            row, current["bytes"], epoch, initialized, recreated
        )

        mac = identities.get(client_ip)
        device_id = record_identity(db, now, client_ip, mac)
        if reset:
            db.execute(
                "INSERT INTO health_events "
                "(event_time,severity,event_type,message) VALUES(?,?,?,?)",
                (
                    now,
                    "warning",
                    "destination_counter_reset",
                    f"{direction} destination counter reset for "
                    f"{client_ip} -> {destination_ip}",
                ),
            )
        record_destination_usage(
            db,
            direction,
            client_ip,
            destination_ip,
            delta,
            device_id,
            now,
        )

        db.execute(
            "INSERT INTO destination_counter_baselines "
            "(direction,client_ip,destination_ip,epoch_id,packets,bytes,sampled_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(direction,client_ip,destination_ip) "
            "DO UPDATE SET epoch_id=excluded.epoch_id,packets=excluded.packets, "
            "bytes=excluded.bytes,sampled_at=excluded.sampled_at",
            (
                direction,
                client_ip,
                destination_ip,
                epoch,
                current["packets"],
                current["bytes"],
                now,
            ),
        )
        total += delta
    return total


def uci_option(name: str) -> str:
    """Read one option from the accounting UCI file ('' when unset)."""
    try:
        text = Path(ACCOUNTING_UCI).read_text()
    except OSError:
        return ""
    match = re.search(rf"option\s+{re.escape(name)}\s+['\"]?([^'\"\n]*)", text)
    return match.group(1).strip() if match else ""


def retention_days() -> int:
    try:
        text = Path(ACCOUNTING_UCI).read_text()
        match = re.search(r"option\s+retention_days\s+['\"]?(\d+)", text)
        if match:
            return max(1, min(365, int(match.group(1))))
    except (OSError, ValueError):
        pass
    return DEFAULT_RETENTION_DAYS


def cleanup_retention(db: sqlite3.Connection, now: int) -> None:
    """Bound accounting and destination evidence to the configured window."""
    cutoff = now - retention_days() * 86400
    cutoff_date = local_date(cutoff)
    db.execute("DELETE FROM usage_hourly WHERE hour_start_utc < ?", (cutoff,))
    db.execute("DELETE FROM unresolved_usage WHERE bucket_start_utc < ?", (cutoff,))
    db.execute("DELETE FROM usage_daily WHERE local_date < ?", (cutoff_date,))
    db.execute(
        "DELETE FROM destination_usage_hourly WHERE hour_start_utc < ?",
        (cutoff,),
    )
    db.execute(
        "DELETE FROM destination_usage_quarterly WHERE quarter_start_utc < ?",
        (cutoff,),
    )
    db.execute(
        "DELETE FROM destination_domain_observations WHERE last_seen < ?",
        (cutoff,),
    )
    # ip_mac_history is append-only by design (PRIMARY KEY on observed_at);
    # without this it grows forever. Safe: per-day exports only need the
    # window above, and live mappings survive in ip_assignments.
    db.execute("DELETE FROM ip_mac_history WHERE observed_at < ?", (cutoff,))
    db.execute(
        "DELETE FROM destination_counter_baselines WHERE sampled_at < ?",
        (cutoff,),
    )
    db.execute("DELETE FROM router_health_hourly WHERE hour_start_utc < ?", (cutoff,))
    db.execute("DELETE FROM health_events WHERE event_time < ?", (cutoff,))
    db.execute("DELETE FROM router_health_alerts WHERE event_time < ?", (cutoff,))
    db.execute("DELETE FROM policy_quota_alerts WHERE event_time < ?", (cutoff,))
    db.execute("DELETE FROM policy_usage_charges WHERE recorded_at < ?", (cutoff,))
    db.execute("DELETE FROM policy_usage_reset_points WHERE reset_at < ?", (cutoff,))


def main() -> None:
    now = int(time.time())
    db = open_database()

    try:
        verify_database(db)
        verify_nft_table()

        # Read both sets before starting the SQLite transaction. This keeps
        # external command failures from leaving an open accounting transaction.
        # Read the table handle on both sides so a reload during sampling
        # cannot mix counters from two table generations.
        table_handle = read_nft_table_handle()
        # IPv4 and IPv6 live in separate sets; their keys never collide.
        tx = read_nft_set("tx_by_ip") | read_nft_set("tx_by_ip6")
        rx = read_nft_set("rx_by_ip") | read_nft_set("rx_by_ip6")
        tx_destinations = (read_nft_destination_set("tx_by_destination")
                           | read_nft_destination_set("tx_by_destination6"))
        rx_destinations = (read_nft_destination_set("rx_by_destination")
                           | read_nft_destination_set("rx_by_destination6"))
        if read_nft_table_handle() != table_handle:
            fail("nftables accounting table was reloaded during sampling")

        destination_clients = {
            client_ip
            for client_ip, _destination_ip in (
                set(tx_destinations) | set(rx_destinations)
            )
        }
        identities = resolve_macs(set(tx) | set(rx) | destination_clients)

        epoch_row = db.execute(
            "SELECT epoch_id FROM counter_epochs "
            "ORDER BY epoch_id DESC LIMIT 1"
        ).fetchone()
        epoch = int(epoch_row[0]) if epoch_row else 1

        db.execute("BEGIN IMMEDIATE")

        if epoch_row is None:
            db.execute(
                "INSERT INTO counter_epochs(epoch_id,started_at,reason) "
                "VALUES(?,?,?)",
                (epoch, now, "initial"),
            )

        ip_initialized = counters_initialized(
            db, "ip_counters_initialized", "counter_baselines"
        )
        destination_initialized = counters_initialized(
            db, "destination_counters_initialized", "destination_counter_baselines"
        )
        previous_handle = meta_get(db, "nft_table_handle")
        pending_recreation = meta_get(db, "nft_table_recreated_pending") == "1"
        recreated = pending_recreation or (
            table_handle is not None
            and previous_handle is not None
            and previous_handle != str(table_handle)
        )
        if recreated:
            reason = "package-upgrade marker" if pending_recreation else (
                f"handle changed {previous_handle} -> {table_handle}"
            )
            db.execute(
                "INSERT INTO health_events "
                "(event_time,severity,event_type,message) VALUES(?,?,?,?)",
                (now, "warning", "counter_table_recreated",
                 f"nft accounting table recreated ({reason})"),
            )

        rx_total = process_direction(
            db, "rx", rx, identities, now, epoch, ip_initialized, recreated
        )
        tx_total = process_direction(
            db, "tx", tx, identities, now, epoch, ip_initialized, recreated
        )
        process_destination_direction(
            db, "rx", rx_destinations, identities, now, epoch,
            destination_initialized, recreated,
        )
        process_destination_direction(
            db, "tx", tx_destinations, identities, now, epoch,
            destination_initialized, recreated,
        )

        # Missing elements have expired or been removed.  If they return,
        # there is no valid old counter baseline to compare against.
        db.execute("DELETE FROM counter_baselines WHERE sampled_at != ?", (now,))
        db.execute(
            "DELETE FROM destination_counter_baselines WHERE sampled_at != ?",
            (now,),
        )
        meta_set(db, "ip_counters_initialized", "1")
        meta_set(db, "destination_counters_initialized", "1")
        if table_handle is not None:
            meta_set(db, "nft_table_handle", str(table_handle))
        if pending_recreation:
            # This is inside the same transaction as the recreated counters;
            # rollback leaves it for a safe retry.
            db.execute(
                "DELETE FROM schema_meta WHERE key='nft_table_recreated_pending'"
            )
        ingest_adguard_querylog(db, now)
        resource_values = record_router_health(db, now)
        for resource, value_x100 in resource_values.items():
            update_router_health_alert(db, resource, value_x100, now)

        cleanup_retention(db, now)

        db.execute(
            "INSERT INTO health_events "
            "(event_time,severity,event_type,message) VALUES(?,?,?,?)",
            (
                now,
                "info",
                "sample_ok",
                f"Accounting sample completed: RX={rx_total} TX={tx_total}",
            ),
        )

        db.commit()
    except SystemExit:
        try:
            db.rollback()
        except sqlite3.Error:
            pass
        raise
    except sqlite3.Error as exc:
        try:
            db.rollback()
        except sqlite3.Error:
            pass
        fail(f"SQLite transaction failed: {exc}")
    except Exception as exc:
        try:
            db.rollback()
        except sqlite3.Error:
            pass
        fail(f"collector failed: {exc}")
    finally:
        db.close()

    print(
        "r3s-accounting: sample OK\n"
        f"time={now}\n"
        f"rx_delta={rx_total}\n"
        f"tx_delta={tx_total}\n"
        f"epoch={epoch}"
    )


if __name__ == "__main__":
    main()
