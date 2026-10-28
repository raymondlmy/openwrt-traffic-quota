#!/usr/bin/env python3
"""Drive the real collector/evaluator inside the ipv6_netns_e2e.sh router namespace.

Usage: ipv6_netns_driver.py WORKDIR {init|collect|evaluate|pairs|unpairs}
"""
from __future__ import annotations

import contextlib
import importlib.util
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLIENT_MAC = "aa:bb:cc:00:00:10"


def load(name: str, path: str, work: Path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    module.DHCP_LEASES = str(work / "dhcp.leases")
    return module


def init(work: Path) -> None:
    con = sqlite3.connect(work / "accounting.sqlite3")
    con.executescript((REPO / "config/schema.sql").read_text())
    for migration in sorted((REPO / "config/migrations").glob("*.sql")):
        with contextlib.suppress(sqlite3.Error):
            con.executescript(migration.read_text())
    con.execute("INSERT INTO devices(device_id,name,group_name,enabled) VALUES(1,'laptop-1','Kids',1)")
    con.execute("INSERT INTO device_macs(mac,device_id) VALUES(?,1)", (CLIENT_MAC,))
    con.commit()
    con.close()
    (work / "dhcp.leases").write_text("")


def collect(work: Path) -> None:
    collector = load("collector_e2e", "src/collector.py", work)
    collector.DB = str(work / "accounting.sqlite3")
    collector.ADGUARD_QUERYLOG = str(work / "missing-querylog.json")
    collector.ACCOUNTING_UCI = str(work / "missing-uci")
    collector.main()


def evaluate(work: Path, override: str) -> None:
    con = sqlite3.connect(work / "accounting.sqlite3")
    con.execute("DELETE FROM policy_rule_devices")
    con.execute("DELETE FROM policy_rules")
    con.execute(
        "INSERT INTO policy_rules(policy_id,name,policy_type,service,days,start_minute,"
        "end_minute,manual_override,created_at,updated_at) "
        "VALUES(1,'e2e-block','schedule','internet','0,1,2,3,4,5,6',0,1,?,0,0)",
        (override,),
    )
    con.execute("INSERT INTO policy_rule_devices(policy_id,device_id) VALUES(1,1)")
    con.commit()
    con.close()
    evaluator = load("evaluator_e2e", "src/r3s-policy-evaluator.py", work)
    sys.argv = ["r3s-policy-evaluator", "--db", str(work / "accounting.sqlite3"),
                "--adguard-querylog", str(work / "missing-querylog.json")]
    evaluator.main()


def pairs(work: Path, blocked: bool) -> None:
    evaluator = load("evaluator_e2e", "src/r3s-policy-evaluator.py", work)
    con = sqlite3.connect(work / "accounting.sqlite3")
    con.row_factory = sqlite3.Row
    con.execute("DELETE FROM policy_rule_devices")
    con.execute("DELETE FROM policy_rules")
    con.execute(
        "INSERT INTO policy_rules(policy_id,name,policy_type,service,domains,quota_bytes,"
        "period,manual_override,created_at,updated_at) "
        "VALUES(2,'e2e-quota','service_quota','custom','example.test',1,'daily',?,0,0)",
        ("block" if blocked else "allow",),
    )
    con.execute("INSERT INTO policy_rule_devices(policy_id,device_id) VALUES(2,1)")
    rows = evaluator.active_custom_quota_rows(con)
    evaluator.decide_custom_blocks(
        con, rows, [],
        {1: {"192.168.1.10", "2001:db8:1::10"}},
        {2: {"203.0.113.2", "2001:db8:2::2"}},
        {}, 0,
        device_macs={1: {CLIENT_MAC}},
    )
    con.commit()
    con.close()


def main() -> None:
    work, action = Path(sys.argv[1]), sys.argv[2]
    if action == "init":
        init(work)
    elif action == "collect":
        collect(work)
    elif action == "evaluate":
        evaluate(work, "block")
    elif action == "unevaluate":
        evaluate(work, "allow")
    elif action == "pairs":
        pairs(work, True)
    elif action == "unpairs":
        pairs(work, False)
    else:
        raise SystemExit(f"unknown action {action}")


if __name__ == "__main__":
    main()
