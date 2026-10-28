#!/usr/bin/env python3
"""Regression simulation for nft counter generations and missing elements."""
import contextlib
import importlib.util
import io
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MB = 1_000_000


def load_collector(work):
    real_which = shutil.which
    shutil.which = lambda name: "/fake/" + name
    try:
        spec = importlib.util.spec_from_file_location(
            "collector_under_test", REPO / "src/collector.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        shutil.which = real_which
    module.DB = str(work / "accounting.sqlite3")
    module.DHCP_LEASES = str(work / "dhcp.leases")
    module.ADGUARD_QUERYLOG = str(work / "missing-querylog.json")
    module.ACCOUNTING_UCI = str(work / "missing-uci")
    module.verify_nft_table = lambda: None
    return module


def create_database(path):
    con = sqlite3.connect(path)
    con.executescript((REPO / "config/schema.sql").read_text())
    for migration in sorted((REPO / "config/migrations").glob("*.sql")):
        with contextlib.suppress(sqlite3.Error):
            con.executescript(migration.read_text())
    con.executemany(
        "INSERT INTO devices(device_id,name,group_name,enabled) VALUES(?,?,?,1)",
        [(1, "KidTablet", "Kids"), (2, "NewPhone", "Personal")],
    )
    con.executemany(
        "INSERT INTO device_macs(mac,device_id) VALUES(?,?)",
        [("aa:bb:cc:dd:ee:fd", 1), ("aa:bb:cc:dd:ee:fe", 2)],
    )
    con.commit()
    con.close()


def main():
    work = Path(tempfile.mkdtemp(prefix="r3s-collector-sim-"))
    create_database(work / "accounting.sqlite3")
    (work / "dhcp.leases").write_text(
        "0 aa:bb:cc:dd:ee:fd 192.168.1.50 tablet *\n"
        "0 aa:bb:cc:dd:ee:fe 192.168.1.60 phone *\n"
    )
    collector = load_collector(work)
    names = ("tx_by_ip", "rx_by_ip", "tx_by_destination", "rx_by_destination",
             "tx_by_ip6", "rx_by_ip6", "tx_by_destination6", "rx_by_destination6")
    sets = {name: {} for name in names}
    table_handle = [7]
    clock = [1_760_000_000]
    real_time = time.time
    time.time = lambda: clock[0]

    def nft_json(name):
        elements = []
        for key, count in sets[name].items():
            value = {"concat": list(key)} if isinstance(key, tuple) else key
            elements.append({"elem": {"val": value, "counter": {
                "packets": count // 1000, "bytes": count}}})
        return json.dumps({"nftables": [{"set": {
            "family": "inet", "table": "r3s_accounting", "name": name,
            "elem": elements}}]})

    def fake_run(command):
        if command[-1] == "tables":
            output = {"nftables": [{"table": {
                "family": "inet", "name": "r3s_accounting",
                "handle": table_handle[0]}}]}
        else:
            output = json.loads(nft_json(command[-1]))
        return subprocess.CompletedProcess(command, 0, json.dumps(output), "")

    collector.run_command = fake_run

    def traffic(ip, destination, rx, tx):
        for direction, count in (("rx", rx), ("tx", tx)):
            sets[f"{direction}_by_ip"][ip] = sets[f"{direction}_by_ip"].get(ip, 0) + count
            pair = (ip, destination)
            sets[f"{direction}_by_destination"][pair] = sets[f"{direction}_by_destination"].get(pair, 0) + count

    def sample():
        with contextlib.redirect_stdout(io.StringIO()):
            collector.main()
        clock[0] += 60

    def reload_firewall():
        for values in sets.values():
            values.clear()
        table_handle[0] += 1

    def totals():
        con = sqlite3.connect(work / "accounting.sqlite3")
        result = con.execute(
            "SELECT (SELECT COALESCE(SUM(rx_bytes),0) FROM usage_daily) + "
            "(SELECT COALESCE(SUM(rx_bytes),0) FROM unresolved_usage), "
            "(SELECT COALESCE(SUM(tx_bytes),0) FROM usage_daily) + "
            "(SELECT COALESCE(SUM(tx_bytes),0) FROM unresolved_usage), "
            "(SELECT COALESCE(SUM(rx_bytes+tx_bytes),0) FROM destination_usage_quarterly)"
        ).fetchone()
        con.close()
        return result

    try:
        # First sample is baseline-only; later new elements are full charges.
        traffic("192.168.1.50", "1.1.1.1", 10 * MB, 1 * MB); sample()
        for _ in range(5):
            traffic("192.168.1.50", "1.1.1.1", 10 * MB, 1 * MB); sample()
        traffic("192.168.1.50", "2.2.2.2", 20 * MB, 1 * MB); sample()
        traffic("192.168.1.50", "2.2.2.2", 20 * MB, 1 * MB); sample()
        traffic("192.168.1.60", "3.3.3.3", 30 * MB, 2 * MB); sample()
        traffic("192.168.1.60", "3.3.3.3", 30 * MB, 2 * MB); sample()
        reload_firewall()
        traffic("192.168.1.50", "1.1.1.1", 40 * MB, 4 * MB); sample()
        traffic("192.168.1.50", "1.1.1.1", 10 * MB, 1 * MB); sample()
        traffic("192.168.1.60", "5.5.5.5", 2 * MB, 1 * MB); sample()
        reload_firewall()
        traffic("192.168.1.60", "5.5.5.5", 90 * MB, 3 * MB)
        traffic("192.168.1.50", "1.1.1.1", 90 * MB, 3 * MB); sample()
        traffic("192.168.1.50", "4.4.4.4", 5 * MB, 1 * MB); sample()
        for direction in ("rx", "tx"):
            sets[f"{direction}_by_destination"].pop(("192.168.1.50", "4.4.4.4"), None)
        sample()
        traffic("192.168.1.50", "4.4.4.4", 20 * MB, 2 * MB); sample()
        sets["rx_by_destination"].clear(); sets["tx_by_destination"].clear(); sample()
        traffic("192.168.1.60", "6.6.6.6", 7 * MB, 1 * MB); sample()
        before_upgrade = totals()

        # Trusted cutover makes the next sample baseline-only.
        con = sqlite3.connect(work / "accounting.sqlite3")
        con.executescript("""BEGIN IMMEDIATE;
            DELETE FROM usage_hourly; DELETE FROM usage_daily;
            DELETE FROM unresolved_usage; DELETE FROM counter_baselines;
            DELETE FROM counter_epochs; DELETE FROM destination_usage_quarterly;
            DELETE FROM destination_counter_baselines;
            DELETE FROM schema_meta WHERE key IN
              ('ip_counters_initialized','destination_counters_initialized',
               'nft_table_handle','nft_table_recreated_pending');
            INSERT INTO counter_epochs VALUES(1,strftime('%s','now'),'cutover');
            COMMIT;""")
        con.close()
        sample(); traffic("192.168.1.50", "1.1.1.1", 3 * MB, 1 * MB); sample()
        post_cutover = totals()
        assert post_cutover[:2] == (3 * MB, 1 * MB), post_cutover

        # r11 -> r12: initialized baselines, no handle, recreated table,
        # new counter is larger than the old baseline before first r12 sample.
        con = sqlite3.connect(work / "accounting.sqlite3")
        con.execute("DELETE FROM schema_meta WHERE key IN ('nft_table_handle','nft_table_recreated_pending')")
        con.commit(); con.close()
        traffic("192.168.1.50", "7.7.7.7", 2 * MB, 1 * MB); sample()
        reload_firewall()
        traffic("192.168.1.50", "7.7.7.7", 90 * MB, 3 * MB)
        con = sqlite3.connect(work / "accounting.sqlite3")
        con.execute("INSERT INTO schema_meta(key,value) VALUES('nft_table_recreated_pending','1')")
        con.commit(); con.close()
        before = totals(); sample(); after = totals()
        assert after[0] - before[0] == 90 * MB, (before, after)
        con = sqlite3.connect(work / "accounting.sqlite3")
        values = dict(con.execute("SELECT key,value FROM schema_meta"))
        con.close()
        assert "nft_table_recreated_pending" not in values
        assert values.get("nft_table_handle") == str(table_handle[0])
        print("PASS collector undercount simulation")
        return 0
    finally:
        time.time = real_time
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
