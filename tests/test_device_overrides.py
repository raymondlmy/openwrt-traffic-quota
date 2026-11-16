#!/usr/bin/env python3
"""Per-device overrides: CLI validation, evaluate view, evaluator enforcement, expiry."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
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


def fresh_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript((REPO / "config/schema.sql").read_text())
    for m in sorted((REPO / "config/migrations").glob("*.sql")):
        with contextlib.suppress(sqlite3.Error):
            con.executescript(m.read_text())
    con.executemany("INSERT INTO devices(device_id,name,group_name,enabled) VALUES(?,?,?,1)",
                    [(1, "laptop-1", "Kids"), (2, "phone-1", "Kids"), (3, "tv-1", "IoT")])
    con.executemany("INSERT INTO device_macs(mac,device_id) VALUES(?,?)",
                    [("aa:bb:cc:00:00:01", 1), ("aa:bb:cc:00:00:02", 2), ("aa:bb:cc:00:00:03", 3)])
    # An all-day schedule so devices 1 and 2 are blocked whatever the test clock says.
    con.execute("""INSERT INTO policy_rules(policy_id,name,policy_type,service,days,start_minute,
                   end_minute,created_at,updated_at) VALUES(1,'night','schedule','internet',
                   '0,1,2,3,4,5,6',0,1439,0,0)""")
    con.executemany("INSERT INTO policy_rule_devices VALUES(1,?)", [(1,), (2,)])
    con.commit()
    con.close()


def cli(db: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(REPO / "src/r3s-policy.py"), "--db", str(db), *args],
                          capture_output=True, text=True, check=False)


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="r3s-overrides-"))
    db = work / "db"
    fresh_db(db)

    # ---- CLI ----
    check("allow without --minutes refused", cli(db, "override", "--device", "1", "--action", "allow").returncode != 0)
    check("unknown device refused", cli(db, "override", "--device", "9", "--action", "block").returncode != 0)
    check("minutes out of range refused",
          cli(db, "override", "--device", "1", "--action", "allow", "--minutes", "0").returncode != 0)
    check("unknown source refused",
          cli(db, "override", "--device", "1", "--action", "block", "--source", "web").returncode != 0)
    check("control characters in note refused",
          cli(db, "override", "--device", "1", "--action", "block", "--note", "a\nb").returncode != 0)
    r = cli(db, "override", "--device", "1", "--action", "allow", "--minutes", "30", "--source", "telegram")
    check("allow for 30 minutes accepted", r.returncode == 0 and "laptop-1 allowed until" in r.stdout)
    r = cli(db, "override", "--device", "3", "--action", "block")
    check("open-ended block accepted", r.returncode == 0 and "until cleared" in r.stdout)
    rows = cli(db, "overrides").stdout.splitlines()
    check("overrides lists both", len(rows) == 2 and rows[0].split("\t")[1] in ("laptop-1", "tv-1"))
    lines = cli(db, "evaluate").stdout.splitlines()
    check("evaluate lists device override rows first",
          lines[0].startswith("device override\t") and lines[1].startswith("device override\t"))
    night1 = [l for l in lines if l.startswith("night\t1\t")]
    night2 = [l for l in lines if l.startswith("night\t2\t")]
    check("allowed device's schedule row shows allow + reason",
          night1 and night1[0].split("\t")[3] == "allow" and "device override" in night1[0])
    check("other device in the same policy still blocked",
          night2 and night2[0].split("\t")[3] == "block")

    # ---- evaluator ----
    ev = load("evaluator_override_test", "src/r3s-policy-evaluator.py")
    applied: list[str] = []
    ev.apply_nft = lambda script: applied.append(script)
    con = ev.open_database(str(db))
    now = int(time.time())
    allowed, blocked = ev.load_overrides(con, now)
    check("load_overrides splits allow/block", allowed == {1} and blocked == {3})

    rows = ev.active_schedule_rows(con)
    ips = {1: {"192.168.1.31"}, 2: {"192.168.1.32"}, 3: {"192.168.1.21"}}
    macs = {1: {"aa:bb:cc:00:00:01"}, 2: {"aa:bb:cc:00:00:02"}, 3: {"aa:bb:cc:00:00:03"}}
    applied.clear()
    ev.decide_schedule_blocks(rows, ips, now, device_macs=macs, allowed_override=allowed, blocked_override=blocked)
    script = applied[-1]
    check("allowed device not in whole-device block (v4 and MAC)",
          "192.168.1.31" not in script and "aa:bb:cc:00:00:01" not in script)
    check("policy-blocked device still blocked", "192.168.1.32" in script and "aa:bb:cc:00:00:02" in script)
    check("block override blocks a device with no policy",
          "192.168.1.21" in script and "aa:bb:cc:00:00:03" in script)
    applied.clear()
    ev.decide_schedule_blocks(rows, ips, now, device_macs=macs)
    check("without overrides: schedule blocks 1 and 2 only",
          "192.168.1.31" in applied[-1] and "192.168.1.21" not in applied[-1])

    # Custom-site block: an allow override (extra time) does not lift site
    # policies, so the allowed device keeps its pairs like any other.
    con.execute("""INSERT INTO policy_rules(policy_id,name,policy_type,service,domains,quota_bytes,period,
                   manual_override,created_at,updated_at) VALUES(2,'games','service_quota','custom',
                   'example.com',100,'daily','block',0,0)""")
    con.executemany("INSERT INTO policy_rule_devices VALUES(2,?)", [(1,), (2,)])
    quota_rows = ev.active_custom_quota_rows(con)
    applied.clear()
    ev.decide_custom_blocks(con, quota_rows, [], ips, {2: {"93.184.215.14", "2606:2800:21f:cb07::1"}},
                            {}, now, macs)
    script = applied[-1]
    check("site block still applies to the allowed device",
          "192.168.1.31 . 93.184.215.14" in script and "aa:bb:cc:00:00:01 . 2606:2800:21f:cb07::1" in script)
    check("site block still applies to other device",
          "192.168.1.32 . 93.184.215.14" in script and "aa:bb:cc:00:00:02 . 2606:2800:21f:cb07::1" in script)
    con.commit()
    rows = {(r.split("\t")[0], r.split("\t")[1]): r.split("\t") for r in cli(db, "evaluate").stdout.splitlines()}
    check("evaluate: allow lifts the internet schedule", rows[("night", "1")][3] == "allow")
    check("evaluate: allow leaves the site policy blocked",
          rows[("games", "1")][3] == "block" and "device override" not in rows[("games", "1")][4])

    # /game: an allow with --lift-sites lifts site policies for that device only.
    check("--lift-sites refused with block",
          cli(db, "override", "--device", "1", "--action", "block", "--lift-sites").returncode != 0)
    r = cli(db, "override", "--device", "1", "--action", "allow", "--minutes", "60", "--lift-sites")
    check("allow --lift-sites accepted", r.returncode == 0 and "laptop-1 games allowed until" in r.stdout)
    check("load_site_lifts sees it", ev.load_site_lifts(con) == {1})
    applied.clear()
    ev.decide_custom_blocks(con, quota_rows, [], ips, {2: {"93.184.215.14", "2606:2800:21f:cb07::1"}},
                            {}, now, macs, site_lifted=ev.load_site_lifts(con))
    script = applied[-1]
    check("site-lifted device has no site pairs (v4 and MAC)",
          "192.168.1.31 ." not in script and "aa:bb:cc:00:00:01 ." not in script)
    check("other device keeps its site block", "192.168.1.32 . 93.184.215.14" in script)
    con.commit()
    rows = {(r.split("\t")[0], r.split("\t")[1]): r.split("\t") for r in cli(db, "evaluate").stdout.splitlines()}
    check("evaluate: game lifts the site policy",
          rows[("games", "1")][3] == "allow" and "games allowed until" in rows[("games", "1")][4])
    check("evaluate: game lifts the internet schedule", rows[("night", "1")][3] == "allow")
    check("evaluate: other device's site policy unchanged", rows[("games", "2")][3] == "block")
    check("overrides lists the site lift", "\tsites" in cli(db, "overrides").stdout)
    cli(db, "override", "--device", "1", "--action", "allow", "--minutes", "30")
    check("replacing /game with /extend drops the site lift", ev.load_site_lifts(con) == set())
    cli(db, "override", "--device", "1", "--action", "allow", "--minutes", "60", "--lift-sites")
    cli(db, "override", "--device", "1", "--action", "block", "--minutes", "30")
    check("replacing /game with /block drops the site lift", ev.load_site_lifts(con) == set())
    cli(db, "override", "--device", "1", "--action", "allow", "--minutes", "30")

    # Fast path: release_custom_pairs drops only the lifted device's pairs.
    ev.read_pair_set = lambda setname: (
        [("192.168.1.31", "93.184.215.14"), ("192.168.1.32", "93.184.215.14")]
        if setname == "blocked_custom_pair" else [("aa:bb:cc:00:00:01", "2606:2800:21f:cb07::1")])
    applied.clear()
    ev.release_custom_pairs({1}, ips, macs)
    check("fast path releases the lifted device's v4 and MAC pairs",
          "delete element inet" in applied[-1] and "192.168.1.31 . 93.184.215.14" in applied[-1]
          and "aa:bb:cc:00:00:01 . 2606:2800:21f:cb07::1" in applied[-1])
    check("fast path leaves other devices' pairs", "192.168.1.32" not in applied[-1])

    # Expiry: logged once as 'expire', row removed, device no longer allowed.
    con.execute("UPDATE device_overrides SET expires_at=? WHERE device_id=1", (now - 5,))
    allowed, blocked = ev.load_overrides(con, now)
    events = con.execute("SELECT device_name,event,notified_at FROM device_override_log WHERE event='expire'").fetchall()
    check("expired allow dropped", allowed == set() and blocked == {3})
    check("expiry logged once, unnotified",
          len(events) == 1 and events[0]["device_name"] == "laptop-1" and events[0]["notified_at"] is None)
    ev.load_overrides(con, now + 60)
    check("expiry not logged twice",
          con.execute("SELECT count(*) FROM device_override_log WHERE event='expire'").fetchone()[0] == 1)
    con.commit()

    # Database without migration 013: no overrides, no crash.
    old = work / "old.db"
    sqlite3.connect(old).execute("CREATE TABLE devices(device_id INTEGER, name TEXT, enabled INTEGER)")
    check("evaluator tolerates missing tables", ev.load_overrides(ev.open_database(str(old)), now) == (set(), set()))
    check("CLI evaluate tolerates missing tables", cli(old, "overrides").returncode == 0)

    # Upgrade from a 013-era database: 014 adds lift_sites, existing rows kept.
    pre = work / "pre014.db"
    c = sqlite3.connect(pre)
    c.executescript((REPO / "config/schema.sql").read_text()
                    .replace("    lift_sites INTEGER NOT NULL DEFAULT 0 CHECK (lift_sites IN (0,1)),\n", "")
                    .replace("    notified_at INTEGER,\n    lift_sites INTEGER NOT NULL DEFAULT 0\n",
                             "    notified_at INTEGER\n"))
    c.execute("INSERT INTO devices(device_id,name,group_name,enabled) VALUES(1,'laptop-1','Kids',1)")
    c.execute("INSERT INTO device_overrides(device_id,action,expires_at,source,created_at) VALUES(1,'block',NULL,'cli',0)")
    c.commit()
    has_col = lambda: any(r[1] == "lift_sites" for r in c.execute("PRAGMA table_info(device_overrides)"))
    check("pre-014 schema has no lift_sites", not has_col())
    check("pre-014: CLI lists overrides", "laptop-1\tblock" in cli(pre, "overrides").stdout)
    check("pre-014: --lift-sites refused clearly",
          "migration 014" in cli(pre, "override", "--device", "1", "--action", "allow",
                                 "--minutes", "5", "--lift-sites").stderr)
    check("pre-014: plain block still works",
          cli(pre, "override", "--device", "1", "--action", "block").returncode == 0)
    check("pre-014: evaluator sees no lifts", ev.load_site_lifts(ev.open_database(str(pre))) == set())
    c.executescript((REPO / "config/migrations/014-override-lift-sites.sql").read_text())
    check("014 adds the column and keeps rows",
          has_col() and c.execute("SELECT action,lift_sites FROM device_overrides").fetchone() == ("block", 0))
    c.close()

    print()
    print("ALL PASSED" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
