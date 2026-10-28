#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Graype
"""Create an authenticated, device/date-scoped AdGuard query archive."""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timedelta

DB_DEFAULT = "/var/lib/r3s-accounting/accounting.sqlite3"
# AdGuard moved to persistent storage (2026-09-21): /var/lib is tmpfs on
# OpenWrt and the 90d querylog filled RAM. Prefer /opt, fall back to legacy.
QUERYLOG_PERSISTENT = "/opt/adguardhome/data/querylog.json"
QUERYLOG_LEGACY = "/var/lib/adguardhome/data/querylog.json"
QUERYLOG_DEFAULT = (
    QUERYLOG_PERSISTENT if os.path.exists(QUERYLOG_PERSISTENT) else QUERYLOG_LEGACY
)
EXCLUDE_DEFAULT = "/etc/r3s-accounting/excluded_str"
QUERYLOG_NAME = re.compile(r"querylog\.json(?:\.\d+)?(?:\.gz)?$")
MAX_EXCLUSION_BYTES = 16384


def querylog_paths(path: str) -> list[str]:
    directories = sorted(
        {
            os.path.dirname(path),
            os.path.dirname(QUERYLOG_PERSISTENT),
            os.path.dirname(QUERYLOG_LEGACY),
        }
    )
    found: list[str] = []
    for directory in directories:
        try:
            names = [
                name for name in os.listdir(directory) if QUERYLOG_NAME.fullmatch(name)
            ]
        except OSError:
            continue
        found.extend(os.path.join(directory, name) for name in sorted(names))
    return found


def open_querylog(path: str):
    return gzip.open(path, "rt", encoding="utf-8") if path.endswith(".gz") else open(path, encoding="utf-8")


def local_date(timestamp: str) -> str:
    value = timestamp.strip().replace("Z", "+00:00")
    return time.strftime("%Y-%m-%d", time.localtime(datetime.fromisoformat(value).timestamp()))


def load_exclusions(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as stream:
            return [line.strip() for line in stream if line.strip() and not line.lstrip().startswith("#")]
    except OSError:
        return []


def device_ips(con: sqlite3.Connection, device_id: int, start_date: str, end_date: str) -> set[str]:
    start_day = datetime.strptime(start_date, "%Y-%m-%d")
    end_day = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
    start = int(time.mktime(start_day.timetuple()))
    end = int(time.mktime(end_day.timetuple()))
    rows = con.execute("""SELECT DISTINCT h.ip FROM ip_mac_history h
        JOIN device_macs dm ON dm.mac=h.mac
        WHERE dm.device_id=? AND h.observed_at>=? AND h.observed_at<?
        UNION SELECT DISTINCT ia.ip FROM ip_assignments ia
        JOIN device_macs dm ON dm.mac=ia.mac
        WHERE dm.device_id=? AND ia.first_seen<? AND ia.last_seen>=?""",
        (device_id, start, end, device_id, end, start)).fetchall()
    return {str(row[0]) for row in rows}


def add_file(tar: tarfile.TarFile, path: str, name: str) -> None:
    tar.add(path, arcname=name, recursive=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export one device's AdGuard queries for a local date range")
    parser.add_argument("--db", default=DB_DEFAULT)
    parser.add_argument("--querylog", default=QUERYLOG_DEFAULT)
    dates = parser.add_mutually_exclusive_group(required=True)
    dates.add_argument("--date", help="single local date (YYYY-MM-DD)")
    dates.add_argument("--from-date", help="first local date (YYYY-MM-DD)")
    parser.add_argument("--to-date", help="last local date, inclusive; requires --from-date")
    device_group = parser.add_mutually_exclusive_group(required=True)
    device_group.add_argument("--device-id", type=int)
    device_group.add_argument("--device", help="exact enabled device name")
    device_group.add_argument("--group", help="enabled device group")
    parser.add_argument("--exclude-file", default=EXCLUDE_DEFAULT)
    parser.add_argument("--base64", action="store_true", help="write base64 instead of raw archive bytes")
    args = parser.parse_args()
    if args.date:
        if args.to_date:
            parser.error("--to-date requires --from-date")
        start_date = end_date = args.date
    else:
        start_date = args.from_date
        end_date = args.to_date or args.from_date
    try:
        start_day = datetime.strptime(start_date, "%Y-%m-%d")
        end_day = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        parser.error("dates must use YYYY-MM-DD")
    if end_day < start_day:
        parser.error("--to-date cannot be before --from-date")
    with sqlite3.connect(args.db) as con:
        con.row_factory = sqlite3.Row
        if args.device_id is not None:
            matches = con.execute(
                "SELECT device_id,name,group_name FROM devices WHERE device_id=? AND enabled=1",
                (args.device_id,),
            ).fetchall()
            missing = f"enabled device_id {args.device_id} not found"
        else:
            if args.device is not None:
                matches = con.execute(
                    "SELECT device_id,name,group_name FROM devices WHERE name=? AND enabled=1 ORDER BY device_id",
                    (args.device,),
                ).fetchall()
                missing = f"enabled device {args.device!r} not found"
            else:
                matches = con.execute(
                    "SELECT device_id,name,group_name FROM devices WHERE group_name=? AND enabled=1 ORDER BY device_id",
                    (args.group,),
                ).fetchall()
                missing = f"enabled device group {args.group!r} not found"
        if not matches:
            raise SystemExit(missing)
        if args.device is not None and len(matches) > 1:
            raise SystemExit(f"device name {args.device!r} is ambiguous; use --device-id")
        device = matches[0]
        ips = set()
        for match in matches:
            ips.update(device_ips(con, int(match["device_id"]), start_date, end_date))
    exclusions = load_exclusions(args.exclude_file)
    temp = tempfile.mkdtemp(prefix="r3s-adguard-export-")
    try:
        jsonl_path = os.path.join(temp, "querylog.json")
        with open(jsonl_path, "w", encoding="utf-8") as jsonl:
            for path in querylog_paths(args.querylog):
                try:
                    stream = open_querylog(path)
                except OSError:
                    continue
                with stream:
                    for line in stream:
                        try:
                            record = json.loads(line)
                            timestamp = str(record["T"])
                            record_date = local_date(timestamp)
                            if record_date < start_date or record_date > end_date or str(record["IP"]) not in ips:
                                continue
                            domain = str(record["QH"])
                            if any(item in domain for item in exclusions):
                                continue
                            jsonl.write(line.rstrip("\r\n") + "\n")
                        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                            pass
        archive_path = os.path.join(temp, "export.tar.gz")
        with tarfile.open(archive_path, "w:gz") as archive:
            add_file(archive, jsonl_path, "querylog.json")
        with open(archive_path, "rb") as stream:
            if args.base64:
                while True:
                    chunk = stream.read(49152)
                    if not chunk:
                        break
                    sys.stdout.write(base64.b64encode(chunk).decode("ascii"))
            else:
                shutil.copyfileobj(stream, sys.stdout.buffer)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
