#!/usr/bin/env python3
"""Tether guard: group -> MAC set sync and the nft rules that use it."""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
fails = 0


def check(name: str, condition: bool) -> None:
    global fails
    print(("PASS  " if condition else "FAIL  ") + name)
    fails += 0 if condition else 1


def main() -> None:
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("ev_tether", REPO / "src/r3s-policy-evaluator.py")
    ev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ev)
    applied: list[str] = []
    ev.apply_nft = lambda script: applied.append(script)

    check("empty groups = off", ev.parse_groups("") == [])
    check("'none' = off", ev.parse_groups("none") == [])
    check("space/comma separated", ev.parse_groups("Kids, Guests") == ["Kids", "Guests"])
    check("unsafe names dropped", ev.parse_groups("Kids x;drop") == ["Kids"])

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript((REPO / "config/schema.sql").read_text())
    con.executemany("INSERT INTO devices(device_id,name,group_name,enabled) VALUES(?,?,?,?)",
                    [(1, "phone-1", "Kids", 1), (2, "laptop-1", "Kids", 1),
                     (3, "tv-1", "IoT", 1), (4, "tablet-1", "Kids", 0)])
    con.executemany("INSERT INTO device_macs(mac,device_id) VALUES(?,?)",
                    [("aa:bb:cc:00:00:01", 1), ("AA:BB:CC:00:00:02", 2),
                     ("aa:bb:cc:00:00:03", 3), ("aa:bb:cc:00:00:04", 4)])

    macs = ev.tether_guard_macs(con, ["Kids"])
    check("only enabled Kids devices, lower-cased",
          macs == {"aa:bb:cc:00:00:01", "aa:bb:cc:00:00:02"})
    check("no groups -> no MACs", ev.tether_guard_macs(con, []) == set())

    ev.sync_tether_guard(macs)
    check("set flushed then filled",
          applied[-1].startswith("flush set inet r3s_policy tether_guard_mac\n")
          and "add element inet r3s_policy tether_guard_mac { aa:bb:cc:00:00:01, aa:bb:cc:00:00:02 }" in applied[-1])
    ev.sync_tether_guard(set())
    check("off -> flush only", applied[-1] == "flush set inet r3s_policy tether_guard_mac\n")

    nft = (REPO / "config/nftables/ruleset-pre/20-r3s-policy.nft").read_text()
    check("ruleset declares the set", "set tether_guard_mac {" in nft)
    check("IPv4 rule drops TTL 62/126 (forward hook, after our decrement)",
          "ether saddr @tether_guard_mac ip ttl { 62, 126 } counter drop" in nft)
    check("IPv6 rule drops hop limit 62/126",
          "ether saddr @tether_guard_mac ip6 hoplimit { 62, 126 } counter drop" in nft)
    check("own traffic (63/127 in forward hook) never matched",
          "63" not in nft.split("tether_guard_mac ip ttl")[1].split("\n")[0]
          and "127" not in nft.split("tether_guard_mac ip6 hoplimit")[1].split("\n")[0])

    print()
    print("ALL PASSED" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
