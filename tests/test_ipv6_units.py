#!/usr/bin/env python3
"""Unit checks for IPv6 handling that the netns test cannot reach (no AdGuard)."""
from __future__ import annotations

import base64
import contextlib
import importlib.util
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load(name: str, path: str):
    real_which = shutil.which
    shutil.which = lambda command: "/fake/" + command
    try:
        spec = importlib.util.spec_from_file_location(name, REPO / path)
        module = importlib.util.module_from_spec(spec)
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        shutil.which = real_which
    return module


def dns_response(name: str, records: list[tuple[int, bytes]]) -> str:
    """Build a minimal DNS response: one question, then the given answers."""
    qname = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"
    packet = b"\x12\x34\x81\x80" + (1).to_bytes(2, "big") + len(records).to_bytes(2, "big") + b"\0\0\0\0"
    packet += qname + b"\0\1\0\1"
    for rtype, data in records:
        packet += b"\xc0\x0c" + rtype.to_bytes(2, "big") + b"\0\1" + b"\0\0\0\x3c"
        packet += len(data).to_bytes(2, "big") + data
    return base64.b64encode(packet).decode()


V4 = bytes([203, 0, 113, 2])
V6 = bytes.fromhex("20010db8000200000000000000000002")


def test_dns_answers(collector, evaluator) -> None:
    answer = dns_response("video.example.test", [(1, V4), (28, V6), (5, b"\x03foo\0")])
    expected = {"203.0.113.2", "2001:db8:2::2"}
    assert collector.dns_a_answers(answer) == expected, collector.dns_a_answers(answer)
    assert evaluator._dns_a_answers(answer) == expected, evaluator._dns_a_answers(answer)
    # Wrong-length AAAA must be ignored, not mis-parsed.
    bad = dns_response("x.test", [(28, V4)])
    assert collector.dns_a_answers(bad) == set()
    assert evaluator._dns_a_answers(bad) == set()
    print("PASS  A and AAAA answers decoded; malformed AAAA ignored")


def test_sort_and_family(evaluator) -> None:
    mixed = ["2001:db8::2", "192.168.1.9", "192.168.1.10", "2001:db8::1"]
    assert sorted(mixed, key=evaluator.ip_sort_key) == [
        "192.168.1.9", "192.168.1.10", "2001:db8::1", "2001:db8::2"]
    assert evaluator.by_family(set(mixed), 6) == {"2001:db8::1", "2001:db8::2"}
    print("PASS  mixed-family sorting and filtering")


def test_dual_stack_quota_charge(evaluator) -> None:
    """Device resolves over IPv4, then downloads over IPv6: must be charged."""
    work = Path(tempfile.mkdtemp(prefix="r3s-ipv6-unit-"))
    try:
        db = work / "accounting.sqlite3"
        con = sqlite3.connect(db)
        con.executescript((REPO / "config/schema.sql").read_text())
        for migration in sorted((REPO / "config/migrations").glob("*.sql")):
            with contextlib.suppress(sqlite3.Error):
                con.executescript(migration.read_text())
        con.row_factory = sqlite3.Row
        now = 1_800_000_000
        today = evaluator.local_period_start(now)
        bucket = now - now % evaluator.DESTINATION_BUCKET_SECONDS
        con.execute("INSERT INTO devices(device_id,name,group_name,enabled) VALUES(1,'tablet-1','Kids',1)")
        con.execute(
            "INSERT INTO policy_rules(policy_id,name,policy_type,service,domains,quota_bytes,"
            "period,created_at,updated_at) VALUES(1,'video','service_quota','custom',"
            "'example.test',1000000,'daily',0,0)")
        con.execute("INSERT INTO policy_rule_devices(policy_id,device_id) VALUES(1,1)")
        con.execute(
            "INSERT INTO destination_usage_quarterly(quarter_start_utc,local_date,local_hour,"
            "local_minute,identity_key,device_id,client_ip,destination_ip,rx_bytes,tx_bytes) "
            "VALUES(?,?,0,0,'device:1',1,'2001:db8:1::10','2001:db8:2::2',5000,500)",
            (bucket, today))
        querylog = work / "querylog.json"
        querylog.write_text(json.dumps({
            "T": "2027-01-15T08:00:00Z", "IP": "192.168.1.10", "QH": "video.example.test",
            "Answer": dns_response("video.example.test", [(1, V4), (28, V6)]),
        }) + "\n")
        observed = 1_800_000_000 - 60
        evaluator._query_time = lambda _value: observed
        rows = evaluator.active_custom_quota_rows(con)

        # Without device-level evidence (old behaviour) nothing is charged.
        _, by_ip, _ = evaluator.load_recent_dns_evidence(str(querylog), now, rows, {})
        evaluator.sample_custom_usage_from_accounting(con, rows, by_ip, now, {})
        used = con.execute("SELECT COALESCE(SUM(charged_bytes),0) FROM policy_usage_charges").fetchone()[0]
        assert used == 0, used

        # With the device map, the IPv6 download is charged to the policy.
        ip_devices = {"192.168.1.10": 1, "2001:db8:1::10": 1}
        _, by_ip, by_device = evaluator.load_recent_dns_evidence(str(querylog), now, rows, ip_devices)
        evaluator.sample_custom_usage_from_accounting(con, rows, by_ip, now, by_device)
        used = con.execute("SELECT COALESCE(SUM(charged_bytes),0) FROM policy_usage_charges").fetchone()[0]
        assert used == 5500, used
        print("PASS  IPv4 DNS lookup + IPv6 download is charged to the quota (5500 bytes)")
    finally:
        shutil.rmtree(work)


def main() -> None:
    collector = load("collector_ipv6_unit", "src/collector.py")
    evaluator = load("evaluator_ipv6_unit", "src/r3s-policy-evaluator.py")
    test_dns_answers(collector, evaluator)
    test_sort_and_family(evaluator)
    test_dual_stack_quota_charge(evaluator)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
