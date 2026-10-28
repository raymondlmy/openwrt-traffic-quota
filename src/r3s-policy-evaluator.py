#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Graype
"""Evaluate per-device service policies and update nftables state."""
from __future__ import annotations

import argparse
import base64
import datetime
import ipaddress
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

DB_DEFAULT = "/var/lib/r3s-accounting/accounting.sqlite3"
DHCP_LEASES = "/tmp/dhcp.leases"
# AdGuard Home query log (override with --adguard-querylog, set by the init
# script from the 'adguard_querylog' UCI option). /var/lib is RAM on OpenWrt,
# so prefer a persistent /opt location, then the package default.
ADGUARD_QUERYLOG_PERSISTENT = "/opt/adguardhome/data/querylog.json"
ADGUARD_QUERYLOG_LEGACY = "/var/lib/adguardhome/data/querylog.json"
ADGUARD_QUERYLOG = (
    ADGUARD_QUERYLOG_PERSISTENT
    if os.path.exists(ADGUARD_QUERYLOG_PERSISTENT)
    else ADGUARD_QUERYLOG_LEGACY
)
NFT_TABLE = "r3s_policy"
INTERNET_SERVICE = "internet"
CUSTOM_SERVICE = "custom"
# Brave may keep a video connection alive without issuing another DNS query.
# Keep the client-specific evidence long enough to cover that normal cache
# lifetime, while baselines still prevent charging bytes from before evidence.
DNS_EVIDENCE_WINDOW = 2 * 60 * 60
DESTINATION_TIMEOUT = 10 * 60
# Custom policy destinations are sourced only from live resolver answers and
# recent raw AdGuard records.  Do not retain a site-specific IP inventory.
CUSTOM_DNS_EVIDENCE_WINDOW = 2 * 60 * 60
DESTINATION_BUCKET_SECONDS = 15 * 60
MAC_RE = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}")


def fail(message: str) -> None:
    print(f"r3s-policy-evaluator: ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def command_path(name: str, preferred: str | None = None) -> str:
    if preferred and os.access(preferred, os.X_OK):
        return preferred
    found = shutil.which(name)
    if found:
        return found
    fail(f"command not found: {name}")
    raise AssertionError


NFT = command_path("nft", "/usr/sbin/nft")
IP = command_path("ip", "/usr/sbin/ip")


def run_command(command: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            input=stdin,
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


def open_database(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def load_dhcp_ips(device_lookup: dict[str, int]) -> dict[int, set[str]]:
    try:
        text = Path(DHCP_LEASES).read_text()
    except OSError:
        return {}
    result: dict[int, set[str]] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        mac = fields[1].lower()
        ip = fields[2]
        device_id = device_lookup.get(mac)
        if device_id is None:
            continue
        try:
            ip = str(ipaddress.ip_address(ip))
        except ValueError:
            continue
        result.setdefault(device_id, set()).add(ip)
    return result


def load_neigh_ips(device_lookup: dict[str, int]) -> dict[int, set[str]]:
    output = run_command([IP, "neigh", "show"]).stdout
    result: dict[int, set[str]] = {}
    mac_re = re.compile(r"lladdr ([0-9a-fA-F:]{17})")
    for line in output.splitlines():
        fields = line.split()
        if not fields:
            continue
        ip = fields[0]
        match = mac_re.search(line)
        if not match:
            continue
        mac = match.group(1).lower()
        device_id = device_lookup.get(mac)
        if device_id is None:
            continue
        try:
            ip = str(ipaddress.ip_address(ip))
        except ValueError:
            continue
        result.setdefault(device_id, set()).add(ip)
    return result


def merge_device_ips(primary: dict[int, set[str]], secondary: dict[int, set[str]]) -> dict[int, set[str]]:
    merged = {device_id: set(ips) for device_id, ips in primary.items()}
    for device_id, ips in secondary.items():
        merged.setdefault(device_id, set()).update(ips)
    return merged


def _skip_dns_name(packet: bytes, offset: int) -> int | None:
    """Return the offset after one DNS name, including compressed names."""
    seen: set[int] = set()
    while offset < len(packet):
        length = packet[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:
            return offset + 2
        if length & 0xC0 or length > 63:
            return None
        offset += 1 + length
        if offset in seen:
            return None
        seen.add(offset)
    return None


def _dns_a_answers(answer: str) -> set[str]:
    """Decode A and AAAA answers from AdGuard's base64 DNS response."""
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
    except (ValueError, base64.binascii.Error):
        return set()


def _query_time(value: str) -> int | None:
    try:
        return int(datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError, OverflowError):
        return None


def load_recent_dns_evidence(
    path: str, now: int, custom_rows: list[sqlite3.Row],
    ip_devices: dict[str, int] | None = None,
) -> tuple[
    dict[tuple[int, str], set[str]],
    dict[tuple[int, str, str], dict[str, set[int]]],
    dict[tuple[int, int, str], dict[str, set[int]]],
]:
    """Read AdGuard once for custom-policy client DNS evidence.

    Evidence is keyed by the querying client IP and, when that IP belongs to
    a known device, also by device. A dual-stack client often resolves over
    IPv4 and then connects over IPv6, so the device key is what ties the two.
    """
    ip_devices = ip_devices or {}
    domains_by_policy: dict[int, set[str]] = {}
    for row in custom_rows:
        domains_by_policy.setdefault(int(row["policy_id"]), set()).update(
            custom_policy_domains(row)
        )
    custom: dict[tuple[int, str], set[str]] = {}
    custom_times: dict[tuple[int, str, str], dict[str, set[int]]] = {}
    device_times: dict[tuple[int, int, str], dict[str, set[int]]] = {}
    try:
        handle = open(path, encoding="utf-8")
    except OSError:
        return custom, custom_times, device_times
    with handle:
        for line in handle:
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    continue
                observed_at = _query_time(str(record.get("T", "")))
                client = str(record.get("IP", ""))
                domain = str(record.get("QH", "")).lower().rstrip(".")
                if (observed_at is None or observed_at > now
                        or observed_at < now - max(DNS_EVIDENCE_WINDOW, CUSTOM_DNS_EVIDENCE_WINDOW)):
                    continue
                client = str(ipaddress.ip_address(client))
                answers = _dns_a_answers(str(record.get("Answer", "")))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not answers:
                continue
            if observed_at >= now - CUSTOM_DNS_EVIDENCE_WINDOW:
                for policy_id, domains in domains_by_policy.items():
                    if any(domain == configured or domain.endswith("." + configured)
                           for configured in domains):
                        custom.setdefault((policy_id, client), set()).update(answers)
                        device_id = ip_devices.get(client)
                        for destination in answers:
                            custom_times.setdefault(
                                (policy_id, client, destination), {}
                            ).setdefault(domain, set()).add(observed_at)
                            if device_id is not None:
                                device_times.setdefault(
                                    (policy_id, device_id, destination), {}
                                ).setdefault(domain, set()).add(observed_at)
    return custom, custom_times, device_times


def read_nft_set(setname: str) -> dict[str, dict[str, int]]:
    raw = run_command([NFT, "-j", "list", "set", "inet", NFT_TABLE, setname]).stdout
    try:
        document: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"invalid nft JSON for {setname}: {exc}")
    counters: dict[str, dict[str, int]] = {}
    nftables = document.get("nftables", []) if isinstance(document, dict) else []
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
            if value is None:
                continue
            ip = str(value)
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                continue
            counter = element.get("counter") if isinstance(element.get("counter"), dict) else {}
            counters[ip] = {
                "packets": int(counter.get("packets", 0) or 0),
                "bytes": int(counter.get("bytes", 0) or 0),
            }
    return counters


def apply_nft(script: str) -> None:
    if script.strip():
        run_command([NFT, "-f", "-"], stdin=script)


def active_custom_quota_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        """SELECT p.policy_id,p.name,p.domains,p.quota_bytes,p.manual_override,
                  d.device_id,d.name AS device_name
           FROM policy_rules p JOIN policy_rule_devices pd ON pd.policy_id=p.policy_id
           JOIN devices d ON d.device_id=pd.device_id
           WHERE p.enabled=1 AND d.enabled=1 AND p.policy_type='service_quota'
             AND p.service=? ORDER BY p.policy_id,d.device_id""",
        (CUSTOM_SERVICE,),
    ).fetchall()


def active_custom_schedule_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        """SELECT p.policy_id,p.name,p.domains,p.days,p.start_minute,p.end_minute,p.manual_override,
                  d.device_id,d.name AS device_name
           FROM policy_rules p JOIN policy_rule_devices pd ON pd.policy_id=p.policy_id
           JOIN devices d ON d.device_id=pd.device_id
           WHERE p.enabled=1 AND d.enabled=1 AND p.policy_type='schedule'
             AND p.service=? ORDER BY p.policy_id,d.device_id""",
        (CUSTOM_SERVICE,),
    ).fetchall()


def active_schedule_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT p.policy_id,p.name,p.days,p.start_minute,p.end_minute,p.manual_override,
               d.device_id,d.name AS device_name
        FROM policy_rules p
        JOIN policy_rule_devices pd ON pd.policy_id=p.policy_id
        JOIN devices d ON d.device_id=pd.device_id
        WHERE p.enabled=1
          AND d.enabled=1
          AND p.policy_type='schedule'
          AND p.service=?
        ORDER BY p.policy_id,d.device_id
        """,
        (INTERNET_SERVICE,),
    ).fetchall()


def mac_device_lookup(con: sqlite3.Connection, device_ids: set[int]) -> dict[str, int]:
    if not device_ids:
        return {}
    placeholders = ",".join("?" for _ in device_ids)
    rows = con.execute(
        f"""
        SELECT dm.mac,dm.device_id
        FROM device_macs dm
        JOIN devices d ON d.device_id=dm.device_id
        WHERE d.enabled=1 AND dm.device_id IN ({placeholders})
        """,
        tuple(sorted(device_ids)),
    ).fetchall()
    return {str(row["mac"]).lower(): int(row["device_id"]) for row in rows}


def local_period_start(now: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now))


def local_day_and_minute(now: int) -> tuple[int, int]:
    current = time.localtime(now)
    day = (current.tm_wday + 1) % 7
    minute = current.tm_hour * 60 + current.tm_min
    return day, minute


def ip_sort_key(value: str) -> tuple[int, int]:
    parsed = ipaddress.ip_address(value)
    return parsed.version, int(parsed)


def by_family(addresses: set[str], version: int) -> set[str]:
    return {ip for ip in addresses if ipaddress.ip_address(ip).version == version}


def schedule_active(days_text: str | None, start: int | None, end: int | None, day: int, minute: int) -> bool:
    if not days_text:
        return False
    days = {int(part) for part in days_text.split(",") if part.strip().isdigit()}
    if start is None or end is None or not days:
        return False
    if start <= end:
        return day in days and start <= minute < end
    prev_day = (day - 1) % 7
    return (day in days and minute >= start) or (prev_day in days and minute < end)


def sync_counter_set(setname: str, desired_ips: set[str], existing_ips: set[str]) -> None:
    commands: list[str] = []
    for ip in sorted(existing_ips - desired_ips, key=ip_sort_key):
        commands.append(f"delete element inet {NFT_TABLE} {setname} {{ {ip} }}")
    for ip in sorted(desired_ips - existing_ips, key=ip_sort_key):
        commands.append(f"add element inet {NFT_TABLE} {setname} {{ {ip} }}")
    if commands:
        apply_nft("\n".join(commands) + "\n")


def custom_policy_domains(row: sqlite3.Row) -> set[str]:
    return {
        domain.strip().lower().rstrip(".")
        for domain in str(row["domains"] or "").split(",")
        if domain.strip()
    }


def resolve_custom_domains(rows: list[sqlite3.Row]) -> dict[int, set[str]]:
    """Return live resolver answers for each configured custom policy domain."""
    addresses: dict[int, set[str]] = {}
    domains_by_policy: dict[int, set[str]] = {}
    for row in rows:
        policy_id = int(row["policy_id"])
        domains = custom_policy_domains(row)
        if not domains:
            continue
        domains_by_policy.setdefault(policy_id, set()).update(domains)
    for policy_id, domains in domains_by_policy.items():
        addresses.setdefault(policy_id, set())
        for domain in domains:
            try:
                infos = socket.getaddrinfo(domain, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
            except socket.gaierror:
                continue
            for info in infos:
                try:
                    addresses[policy_id].add(str(ipaddress.ip_address(info[4][0])))
                except ValueError:
                    continue

    return addresses


def sync_custom_destinations(addresses_by_policy: dict[int, set[str]], existing_ips: set[str]) -> None:
    addresses = set().union(*addresses_by_policy.values()) if addresses_by_policy else set()
    commands: list[str] = []
    for version, setname in ((4, "custom_dst"), (6, "custom_dst6")):
        wanted = by_family(addresses, version)
        current = by_family(existing_ips, version)
        commands += [f"delete element inet {NFT_TABLE} {setname} {{ {ip} }}"
                     for ip in sorted(current - wanted, key=ip_sort_key)]
        commands += [f"add element inet {NFT_TABLE} {setname} {{ {ip} timeout {DESTINATION_TIMEOUT}s }}"
                     for ip in sorted(wanted - current, key=ip_sort_key)]
    if commands:
        apply_nft("\n".join(commands) + "\n")


def sample_custom_usage_from_accounting(
    con: sqlite3.Connection,
    policy_rows: list[sqlite3.Row],
    custom_dns_times: dict[tuple[int, str, str], dict[str, set[int]]],
    now: int,
    device_dns_times: dict[tuple[int, int, str], dict[str, set[int]]] | None = None,
) -> None:
    """Persist evidence-backed custom-policy usage from destination/IP bytes.

    The nft custom quota sets are intentionally not used here. They aggregate
    every configured custom destination into one counter per client, which can
    attribute one custom policy's traffic to another. The collector stores
    client/destination bytes in 15-minute buckets, and each bucket is matched
    to actual recent AdGuard DNS-answer timestamps for that policy. Legacy
    hourly rows remain readable during the retention window.

    Destination accounting is sampled by the collector, so quota decisions
    can lag traffic by at most the normal collector interval.
    """
    today = local_period_start(now)
    if not policy_rows:
        return

    usage_rows = con.execute(
        """
        SELECT hour_start_utc AS bucket_start_utc,
               3600 AS bucket_seconds,
               device_id,client_ip,destination_ip,(rx_bytes + tx_bytes) AS bytes
        FROM destination_usage_hourly
        WHERE local_date=? AND device_id IS NOT NULL
        UNION ALL
        SELECT quarter_start_utc AS bucket_start_utc,
               ? AS bucket_seconds,
               device_id,client_ip,destination_ip,(rx_bytes + tx_bytes) AS bytes
        FROM destination_usage_quarterly
        WHERE local_date=? AND device_id IS NOT NULL
        """,
        (today, DESTINATION_BUCKET_SECONDS, today),
    ).fetchall()

    for usage in usage_rows:
        device_id = int(usage["device_id"])
        client_ip = str(usage["client_ip"])
        destination_ip = str(usage["destination_ip"])
        bucket_start = int(usage["bucket_start_utc"])
        bucket_seconds = int(usage["bucket_seconds"])
        for policy in policy_rows:
            policy_id = int(policy["policy_id"])
            reset = con.execute(
                """SELECT reset_at FROM policy_usage_reset_points
                   WHERE policy_id=? AND device_id=? AND period_start=?""",
                (policy_id, device_id, today),
            ).fetchone()
            if reset and bucket_start + bucket_seconds <= int(reset["reset_at"]):
                continue
            evidence_by_domain = custom_dns_times.get(
                (policy_id, client_ip, destination_ip), {}
            ) or (device_dns_times or {}).get(
                (policy_id, device_id, destination_ip), {}
            )
            matches = [
                (observed_at, domain)
                for domain, evidence_times in evidence_by_domain.items()
                for observed_at in evidence_times
                if (bucket_start < observed_at + CUSTOM_DNS_EVIDENCE_WINDOW
                    and bucket_start + bucket_seconds > observed_at)
            ]
            if not matches:
                continue
            observed_at, domain = max(matches)
            con.execute(
                """
                INSERT INTO policy_usage_charges
                    (policy_id,device_id,period_start,bucket_start_utc,bucket_seconds,
                     client_ip,destination_ip,domain,observed_at,charged_bytes,recorded_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(policy_id,device_id,period_start,bucket_start_utc,client_ip,destination_ip)
                DO UPDATE SET
                    charged_bytes=max(policy_usage_charges.charged_bytes,excluded.charged_bytes),
                    domain=CASE WHEN excluded.charged_bytes >= policy_usage_charges.charged_bytes
                                THEN excluded.domain ELSE policy_usage_charges.domain END,
                    observed_at=max(policy_usage_charges.observed_at,excluded.observed_at),
                    recorded_at=excluded.recorded_at
                """,
                (policy_id, device_id, today, bucket_start, bucket_seconds,
                 client_ip, destination_ip, domain, observed_at,
                 int(usage["bytes"] or 0), now),
            )

    for policy in policy_rows:
        key = (int(policy["policy_id"]), int(policy["device_id"]))
        charged = con.execute(
            """SELECT COALESCE(SUM(charged_bytes),0) AS bytes
               FROM policy_usage_charges
               WHERE policy_id=? AND device_id=? AND period_start=?""",
            (key[0], key[1], today),
        ).fetchone()
        used = int(charged["bytes"])
        con.execute(
            """
            INSERT INTO policy_state
                (policy_id,device_id,period_start,service_bytes,blocked,evaluated_at)
            VALUES(?,?,?,?,0,?)
            ON CONFLICT(policy_id,device_id,period_start) DO UPDATE SET
                -- Keep an in-flight legacy state until the operator resets it;
                -- fresh evidence charges are otherwise the source of truth.
                service_bytes=max(policy_state.service_bytes,excluded.service_bytes),
                blocked=policy_state.blocked,
                evaluated_at=excluded.evaluated_at
            """,
            (key[0], key[1], today, used, now),
        )


def decide_schedule_blocks(
    schedule_rows: list[sqlite3.Row],
    device_ips: dict[int, set[str]],
    now: int,
    blocked_set: str = "blocked_internet_src",
    device_macs: dict[int, set[str]] | None = None,
    blocked_mac_set: str = "blocked_internet_mac6",
) -> None:
    day, minute = local_day_and_minute(now)
    device_macs = device_macs or {}
    blocked_devices = {
        int(row["device_id"])
        for row in schedule_rows
        if (
            row["manual_override"] == "block"
            or (
                row["manual_override"] != "allow"
                and schedule_active(
                    str(row["days"]) if row["days"] is not None else None,
                    int(row["start_minute"]) if row["start_minute"] is not None else None,
                    int(row["end_minute"]) if row["end_minute"] is not None else None,
                    day,
                    minute,
                )
            )
        )
    }
    blocked_ips = sorted(
        {ip for device_id in blocked_devices
         for ip in by_family(device_ips.get(device_id, set()), 4)},
        key=ip_sort_key,
    )
    blocked_macs = sorted(
        {mac for device_id in blocked_devices for mac in device_macs.get(device_id, set())}
    )
    commands = [f"flush set inet {NFT_TABLE} {blocked_set}"]
    if blocked_ips:
        commands.append(
            f"add element inet {NFT_TABLE} {blocked_set} {{ {', '.join(blocked_ips)} }}"
        )
    commands.append(f"flush set inet {NFT_TABLE} {blocked_mac_set}")
    if blocked_macs:
        commands.append(
            f"add element inet {NFT_TABLE} {blocked_mac_set} {{ {', '.join(blocked_macs)} }}"
        )
    apply_nft("\n".join(commands) + "\n")


def queue_quota_alerts(con: sqlite3.Connection, quota_rows: list[sqlite3.Row], now: int) -> None:
    """Queue one alert per bandwidth policy/device/local day at the limit."""
    today = local_period_start(now)
    for row in quota_rows:
        policy_id = int(row["policy_id"])
        device_id = int(row["device_id"])
        quota = int(row["quota_bytes"] or 0)
        if quota <= 0:
            continue
        state = con.execute(
            "SELECT service_bytes FROM policy_state WHERE policy_id=? AND device_id=? AND period_start=?",
            (policy_id, device_id, today),
        ).fetchone()
        used = int(state["service_bytes"]) if state else 0
        if used < quota:
            continue
        con.execute(
            """INSERT OR IGNORE INTO policy_quota_alerts
               (policy_id,device_id,period_start,event_time,device_name,policy_name,quota_bytes,used_bytes)
               VALUES(?,?,?,?,?,?,?,?)""",
            (policy_id, device_id, today, now, str(row["device_name"]), str(row["name"]), quota, used),
        )


def decide_custom_blocks(
    con: sqlite3.Connection,
    quota_rows: list[sqlite3.Row],
    schedule_rows: list[sqlite3.Row],
    device_ips: dict[int, set[str]],
    policy_destinations: dict[int, set[str]],
    client_dns_destinations: dict[tuple[int, str], set[str]],
    now: int,
    device_macs: dict[int, set[str]] | None = None,
) -> None:
    """Block only the device/destination pairs owned by an active custom policy."""
    today = local_period_start(now)
    device_macs = device_macs or {}
    blocked_pairs: set[tuple[str, str]] = set()
    blocked_pairs6: set[tuple[str, str]] = set()

    def block_policy_device(policy_id: int, device_id: int) -> None:
        sources = device_ips.get(device_id, set())
        destinations = set(policy_destinations.get(policy_id, set()))
        for source in sources:
            destinations.update(client_dns_destinations.get((policy_id, source), set()))
        for source in by_family(sources, 4):
            for destination in by_family(destinations, 4):
                blocked_pairs.add((source, destination))
        for mac in device_macs.get(device_id, set()):
            for destination in by_family(destinations, 6):
                blocked_pairs6.add((mac, destination))

    for row in quota_rows:
        policy_id = int(row["policy_id"])
        device_id = int(row["device_id"])
        state = con.execute(
            "SELECT service_bytes FROM policy_state WHERE policy_id=? AND device_id=? AND period_start=?",
            (policy_id, device_id, today),
        ).fetchone()
        used = int(state["service_bytes"]) if state else 0
        quota = int(row["quota_bytes"] or 0)
        override = str(row["manual_override"])
        blocked = int(override == "block" or (override != "allow" and quota > 0 and used >= quota))
        if blocked:
            block_policy_device(policy_id, device_id)
        con.execute(
            """INSERT INTO policy_state(policy_id,device_id,period_start,service_bytes,blocked,evaluated_at)
               VALUES(?,?,?,?,?,?) ON CONFLICT(policy_id,device_id,period_start) DO UPDATE SET
               blocked=excluded.blocked,evaluated_at=excluded.evaluated_at""",
            (policy_id, device_id, today, used, blocked, now),
        )
    day, minute = local_day_and_minute(now)
    for row in schedule_rows:
        if row["manual_override"] == "block" or (
            row["manual_override"] != "allow"
            and schedule_active(
                str(row["days"]) if row["days"] is not None else None,
                int(row["start_minute"]) if row["start_minute"] is not None else None,
                int(row["end_minute"]) if row["end_minute"] is not None else None,
                day,
                minute,
            )
        ):
            block_policy_device(int(row["policy_id"]), int(row["device_id"]))
    commands = [f"flush set inet {NFT_TABLE} blocked_custom_pair"]
    if blocked_pairs:
        elements = ", ".join(
            f"{source} . {destination}"
            for source, destination in sorted(
                blocked_pairs,
                key=lambda pair: (ip_sort_key(pair[0]), ip_sort_key(pair[1])),
            )
        )
        commands.append(f"add element inet {NFT_TABLE} blocked_custom_pair {{ {elements} }}")
    commands.append(f"flush set inet {NFT_TABLE} blocked_custom_pair6")
    if blocked_pairs6:
        elements = ", ".join(
            f"{mac} . {destination}"
            for mac, destination in sorted(
                blocked_pairs6, key=lambda pair: (pair[0], ip_sort_key(pair[1]))
            )
        )
        commands.append(f"add element inet {NFT_TABLE} blocked_custom_pair6 {{ {elements} }}")
    apply_nft("\n".join(commands) + "\n")


def verify_nft() -> None:
    run_command([NFT, "list", "table", "inet", NFT_TABLE])


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="r3s-policy-evaluator",
        description="Evaluate per-device service policies",
    )
    parser.add_argument("--db", default=DB_DEFAULT)
    parser.add_argument("--adguard-querylog", default=ADGUARD_QUERYLOG)
    args = parser.parse_args()
    now = int(time.time())
    verify_nft()
    with open_database(args.db) as con:
        custom_quota_rows = active_custom_quota_rows(con)
        custom_schedule_rows = active_custom_schedule_rows(con)
        schedule_rows = active_schedule_rows(con)
        device_ids = {int(row["device_id"]) for row in custom_quota_rows}
        device_ids.update(int(row["device_id"]) for row in custom_schedule_rows)
        device_ids.update(int(row["device_id"]) for row in schedule_rows)
        device_lookup = mac_device_lookup(con, device_ids)
        device_ips = merge_device_ips(
            load_dhcp_ips(device_lookup),
            load_neigh_ips(device_lookup),
        )
        device_macs: dict[int, set[str]] = {}
        for mac, device_id in device_lookup.items():
            # MACs are written into nft commands: accept only canonical form.
            if MAC_RE.fullmatch(mac):
                device_macs.setdefault(device_id, set()).add(mac)
        ip_devices = {
            ip: device_id for device_id, ips in device_ips.items() for ip in ips
        }

        custom_rows = custom_quota_rows + custom_schedule_rows
        custom_dns, custom_dns_times, device_dns_times = load_recent_dns_evidence(
            args.adguard_querylog, now, custom_rows, ip_devices
        )
        custom_addresses = resolve_custom_domains(custom_rows)
        for (policy_id, _client), addresses in custom_dns.items():
            custom_addresses.setdefault(policy_id, set()).update(addresses)
        custom_existing = set(read_nft_set("custom_dst")) | set(read_nft_set("custom_dst6"))
        sync_custom_destinations(custom_addresses, custom_existing)
        sample_custom_usage_from_accounting(
            con, custom_quota_rows, custom_dns_times, now, device_dns_times
        )
        # Custom quota usage is now read from destination accounting. Remove
        # any legacy aggregate counters so they cannot continue accumulating.
        custom_tx = read_nft_set("custom_quota_src")
        custom_rx = read_nft_set("custom_quota_dst")
        sync_counter_set("custom_quota_src", set(), set(custom_tx))
        sync_counter_set("custom_quota_dst", set(), set(custom_rx))
        decide_custom_blocks(
            con,
            custom_quota_rows,
            custom_schedule_rows,
            device_ips,
            custom_addresses,
            custom_dns,
            now,
            device_macs,
        )
        # Only quota rows are passed here: schedules never produce Telegram
        # alerts, even when their block is currently active.
        queue_quota_alerts(con, custom_quota_rows, now)
        decide_schedule_blocks(schedule_rows, device_ips, now, device_macs=device_macs)
        con.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
