#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Graype
"""Export AdGuard Home query logs as grouped, UK-local CSV."""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone


DEFAULT_FILE_PERSISTENT = "/opt/adguardhome/data/querylog.json"
DEFAULT_FILE_LEGACY = "/var/lib/adguardhome/data/querylog.json"
DEFAULT_FILE = (
    DEFAULT_FILE_PERSISTENT
    if os.path.exists(DEFAULT_FILE_PERSISTENT)
    else DEFAULT_FILE_LEGACY
)
DEFAULT_EXCLUDE_FILE = "/etc/r3s-accounting/excluded_str"
QUERYLOG_NAME = re.compile(r"querylog\.json(?:\.\d+)?(?:\.gz)?$")
KIDS_IP_LAST_OCTETS = {"249", "126", "245", "178", "211"}


def querylog_paths(explicit: list[str]) -> list[str]:
    if explicit:
        return explicit
    directories = sorted(
        {os.path.dirname(DEFAULT_FILE_PERSISTENT), os.path.dirname(DEFAULT_FILE_LEGACY)}
    )
    found: list[str] = []
    for directory in directories:
        try:
            names = [
                name for name in os.listdir(directory)
                if QUERYLOG_NAME.fullmatch(name)
            ]
        except OSError:
            continue
        found.extend(os.path.join(directory, name) for name in sorted(names))
    return found


def open_querylog(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "r", encoding="utf-8")


def local_date_hour(timestamp: str) -> tuple[str, int]:
    value = timestamp.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local = time.localtime(parsed.timestamp())
    return time.strftime("%Y-%m-%d", local), local.tm_hour


def date_number(value: str) -> int:
    return datetime.strptime(value, "%Y-%m-%d").date().toordinal()


def load_exclusions(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return [
                line.strip()
                for line in stream
                if line.strip() and not line.lstrip().startswith("#")
            ]
    except OSError as error:
        print(f"warning: cannot open exclusion file {path}: {error}", file=sys.stderr)
        return []


def device_mark(ip_address: str) -> str:
    if "." in ip_address and ip_address.rsplit(".", 1)[1] in KIDS_IP_LAST_OCTETS:
        return "kids"
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Export grouped AdGuard query logs as CSV")
    parser.add_argument("--date", help="UK local date to export (YYYY-MM-DD)")
    parser.add_argument("--exclude-file", default=DEFAULT_EXCLUDE_FILE, help="case-sensitive domain exclusion strings")
    parser.add_argument("files", nargs="*", help="querylog.json files; defaults to the live log and rotations")
    args = parser.parse_args()
    if args.date:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            parser.error("--date must use YYYY-MM-DD")

    exclusions = load_exclusions(args.exclude_file)
    counts: Counter[tuple[str, int, str, str, bool]] = Counter()
    skipped = 0
    for path in querylog_paths(args.files):
        try:
            stream = open_querylog(path)
        except OSError as error:
            print(f"warning: cannot open {path}: {error}", file=sys.stderr)
            continue
        with stream:
            for line in stream:
                try:
                    record = json.loads(line)
                    date, hour = local_date_hour(record["T"])
                    if args.date and date != args.date:
                        continue
                    ip_address = str(record["IP"])
                    domain = str(record["QH"])
                    if any(exclusion in domain for exclusion in exclusions):
                        continue
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    skipped += 1
                    continue
                result = record.get("Result")
                filtered = bool(result.get("IsFiltered", False)) if isinstance(result, dict) else False
                counts[(date, hour, ip_address, domain, filtered)] += 1

    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(("bst_date", "hour", "ip_address", "domain", "filtered", "count", "device_mark"))
    summary = Counter()
    for (date, _hour, ip_address, _domain, filtered), count in counts.items():
        summary[(date, ip_address, filtered)] += count
    summary_rows = sorted(
        summary,
        key=lambda item: (-summary[item], -date_number(item[0]), item[1], item[2]),
    )
    for date, ip_address, filtered in summary_rows:
        writer.writerow((date, "", ip_address, "", str(filtered).lower(), summary[(date, ip_address, filtered)], device_mark(ip_address)))
    rows = sorted(
        counts,
        key=lambda item: (-item[1], -date_number(item[0]), item[2], item[3], item[4]),
    )
    for date, hour, ip_address, domain, filtered in rows:
        writer.writerow((date, f"{hour:02d}", ip_address, domain, str(filtered).lower(), counts[(date, hour, ip_address, domain, filtered)], device_mark(ip_address)))

    if skipped:
        print(f"warning: skipped {skipped} malformed or incomplete records", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
