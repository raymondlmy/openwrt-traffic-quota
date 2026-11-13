#!/usr/bin/env python3
"""AdGuard query-log evidence: the binary-search seek gives the same result as a full scan."""
from __future__ import annotations

import base64
import importlib.util
import ipaddress
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
fails = 0


def check(name: str, condition: bool) -> None:
    global fails
    print(("PASS  " if condition else "FAIL  ") + name)
    fails += 0 if condition else 1


def answer(ip: str) -> str:
    """Minimal DNS response with one A record, base64 like AdGuard stores it."""
    header = (0).to_bytes(2, "big") + b"\x81\x80" + (1).to_bytes(2, "big") + (1).to_bytes(2, "big") + b"\x00\x00\x00\x00"
    question = b"\x07example\x03com\x00" + b"\x00\x01\x00\x01"
    record = b"\xc0\x0c" + b"\x00\x01\x00\x01" + b"\x00\x00\x00\x3c" + b"\x00\x04" + ipaddress.ip_address(ip).packed
    return base64.b64encode(header + question + record).decode()


def stamp(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> None:
    spec = importlib.util.spec_from_file_location("evaluator_dns_test", REPO / "src/r3s-policy-evaluator.py")
    ev = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(ev)

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT 7 AS policy_id, 'example.com' AS domains").fetchall()
    work = Path(tempfile.mkdtemp(prefix="r3s-dns-"))
    now = 1_800_000_000
    log = work / "querylog.json"

    # 12 hours, one query every 2 s; every 50th for a policy domain. Some junk lines.
    lines = []
    for i, t in enumerate(range(now - 12 * 3600, now + 60, 2)):
        host = "www.example.com" if i % 50 == 0 else f"other{i % 7}.test"
        lines.append(json.dumps({"T": stamp(t), "QH": host, "IP": f"192.168.10.{31 + i % 3}",
                                 "Answer": answer(f"93.184.{i % 200}.{i % 250}")}))
        if i % 997 == 0:
            lines.append("{not json")
            lines.append('{"T":"garbage","QH":"www.example.com"}')
    log.write_text("\n".join(lines) + "\n")

    def evidence(seek: bool):
        original = ev.seek_log_time
        if not seek:
            ev.seek_log_time = lambda handle, start: 0
        try:
            return ev.load_recent_dns_evidence(str(log), now, rows, {"192.168.10.31": 1})
        finally:
            ev.seek_log_time = original

    fast, full = evidence(True), evidence(False)
    check("seek gives the same evidence as a full scan", fast == full)
    check("evidence is not empty", len(full[1]) > 50 and len(full[2]) > 10)
    check("entries after 'now' are ignored",
          all(t <= now for times in full[1].values() for ts in times.values() for t in ts))
    with open(log, "rb") as handle:
        offset = ev.seek_log_time(handle, now - 2 * 3600 - ev.SEEK_MARGIN)
        size = handle.seek(0, 2)
        handle.seek(offset)
        first = ev._line_time(handle.readline())
    check("seek skips most of the file", offset > size * 0.75)
    check("seek lands at or before the window start",
          first is not None and first <= now - 2 * 3600 - ev.SEEK_MARGIN + 2)

    # Edge cases: empty file, everything old, everything new, unparseable.
    for name, content, expected in (
        ("empty log", "", 0),
        ("all lines unparseable", "junk\n" * 50000, 0),
    ):
        log.write_text(content)
        with open(log, "rb") as handle:
            check(f"{name}: seek returns start", ev.seek_log_time(handle, now) == expected)
    log.write_text("\n".join(json.dumps({"T": stamp(now - 86400 + i), "QH": "x.test"}) for i in range(40000)) + "\n")
    with open(log, "rb") as handle:
        offset = ev.seek_log_time(handle, now)
        check("all lines old: seek goes near the end", offset > log.stat().st_size - 70000)
    log.write_text("\n".join(json.dumps({"T": stamp(now + i), "QH": "x.test"}) for i in range(40000)) + "\n")
    with open(log, "rb") as handle:
        check("all lines new: seek returns start", ev.seek_log_time(handle, now - 3600) == 0)
    check("missing log: no evidence", ev.load_recent_dns_evidence(str(work / "none"), now, rows) == ({}, {}, {}))
    check("no site policies: no evidence, no read", ev.load_recent_dns_evidence(str(log), now, []) == ({}, {}, {}))

    print()
    print("ALL PASSED" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
