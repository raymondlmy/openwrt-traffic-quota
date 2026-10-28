#!/usr/bin/env python3
"""Configurable device groups: defaults, custom groups, report layout, CLI."""
from __future__ import annotations

import contextlib
import importlib.util
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
fails = 0


def check(name: str, condition: bool) -> None:
    global fails
    print(("PASS  " if condition else "FAIL  ") + name)
    fails += 0 if condition else 1


def load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="r3s-groups-"))
    con = sqlite3.connect(work / "db")
    con.executescript((REPO / "config/schema.sql").read_text())
    for m in sorted((REPO / "config/migrations").glob("*.sql")):
        with contextlib.suppress(sqlite3.Error):
            con.executescript(m.read_text())
    con.executemany("INSERT INTO devices(device_id,name,group_name,enabled) VALUES(?,?,?,1)",
                    [(1, "a", "Kids"), (2, "b", "IoT"), (3, "c", "Guests"), (4, "d", "Personal")])
    con.commit()
    con.row_factory = sqlite3.Row
    report = load("report_groups_test", "src/r3s-accounting-report.py")
    policy = load("policy_groups_test", "src/r3s-policy.py")

    report.ACCOUNTING_UCI = policy.ACCOUNTING_UCI = str(work / "missing")
    check("default report layout: Kids hourly, IoT per device, every group summarised",
          report.report_groups(con) == (["Kids"], ["Personal", "IoT", "Guests"], ["IoT"]))
    check("default assignable groups", policy.allowed_groups() == ["Personal", "Kids", "IoT"])

    uci = work / "uci"
    uci.write_text("config accounting 'main'\n    option groups 'Adults Kids Guests IoT bad;group'\n"
                   "    option report_hourly_groups 'Guests Kids'\n    option report_device_groups ''\n")
    report.ACCOUNTING_UCI = policy.ACCOUNTING_UCI = str(uci)
    hourly, summary, detail = report.report_groups(con)
    check("custom hourly groups honoured", hourly == ["Guests", "Kids"])
    check("summary follows the configured order, unknown groups last", summary == ["IoT", "Personal"])
    check("empty report_device_groups falls back to the default", detail == ["IoT"])
    check("invalid group names are dropped", policy.allowed_groups() == ["Adults", "Kids", "Guests", "IoT"])
    print()
    print("ALL PASSED" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
