#!/usr/bin/env python3
"""Game DNS block: domain list parsing, rule rendering, merge with user rules,
client addresses (groups, /game lift, history), AdGuard sync."""
from __future__ import annotations

import contextlib
import importlib.util
import sqlite3
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


def main() -> None:
    ev = load("evaluator_game_dns_test", "src/r3s-policy-evaluator.py")
    work = Path(tempfile.mkdtemp(prefix="r3s-game-dns-"))

    # ---- domain list ----
    block, allow = ev.load_game_dns_list(str(REPO / "config/game-dns.list"))
    check("shipped list blocks warthunder.com", "warthunder.com" in block)
    check("shipped list blocks steamserver.net", "steamserver.net" in block)
    check("shipped list allows cdn.gaijin.net", "cdn.gaijin.net" in allow)
    check("wildcard label accepted", "aws-yup-distr-*.gaijinent.com" in allow)
    odd = work / "odd.list"
    odd.write_text("block Example.COM.\nblock bad_domain!\nblock a b c\nfoo x.com\n"
                   "allow ok.example.com # comment\nblock example.com\n")
    b2, a2 = ev.load_game_dns_list(str(odd))
    check("list normalises case, trailing dot; dedupes", b2 == ["example.com"])
    check("list skips invalid and unknown lines", a2 == ["ok.example.com"])
    check("missing list file = nothing", ev.load_game_dns_list(str(work / "none")) == ([], []))

    # ---- rendering / merge ----
    rules = ev.render_game_dns_rules(["a.com"], ["cdn.a.com"], ["192.168.10.33", "fd00::5"])
    check("rules wrapped in markers", rules[0] == ev.GAME_DNS_BEGIN and rules[-1] == ev.GAME_DNS_END)
    check("block rule has client list", "||a.com^$client=192.168.10.33|fd00::5" in rules)
    check("allow rule is an exception", "@@||cdn.a.com^$client=192.168.10.33|fd00::5" in rules)
    check("no clients = no rules", ev.render_game_dns_rules(["a.com"], [], []) == [])
    check("no domains = no rules", ev.render_game_dns_rules([], [], ["1.2.3.4"]) == [])
    user = ["||google.cn^", ev.GAME_DNS_BEGIN, "||old^", ev.GAME_DNS_END, "||keep.me^"]
    merged = ev.merge_game_dns_rules(user, rules)
    check("merge keeps user rules, replaces section",
          merged[:2] == ["||google.cn^", "||keep.me^"] and merged[2:] == rules)
    check("merge with empty section removes it",
          ev.merge_game_dns_rules(user, []) == ["||google.cn^", "||keep.me^"])
    check("merge is idempotent", ev.merge_game_dns_rules(merged, rules) == merged)

    # ---- client addresses ----
    con = sqlite3.connect(work / "db")
    con.executescript((REPO / "config/schema.sql").read_text())
    for m in sorted((REPO / "config/migrations").glob("*.sql")):
        with contextlib.suppress(sqlite3.Error):
            con.executescript(m.read_text())
    con.executemany("INSERT INTO devices(device_id,name,group_name,enabled) VALUES(?,?,?,?)",
                    [(1, "laptop", "Kids", 1), (2, "phone", "Kids", 1),
                     (3, "adult", "Personal", 1), (4, "old", "Kids", 0)])
    con.executemany("INSERT INTO device_macs(device_id,mac) VALUES(?,?)",
                    [(1, "aa:bb:cc:00:00:01"), (2, "aa:bb:cc:00:00:02"),
                     (3, "aa:bb:cc:00:00:03"), (4, "aa:bb:cc:00:00:04")])
    now = int(time.time())
    con.executemany("INSERT INTO ip_mac_history(observed_at,ip,mac) VALUES(?,?,?)",
                    [(now - 3600, "192.168.10.33", "aa:bb:cc:00:00:01"),
                     (now - 10 * 86400, "192.168.10.99", "aa:bb:cc:00:00:01"),  # too old
                     (now - 3600, "fd00::2", "aa:bb:cc:00:00:02"),
                     (now - 3 * 86400, "fd00::22", "aa:bb:cc:00:00:02"),        # v6 too old
                     (now - 3600, "192.168.10.50", "aa:bb:cc:00:00:03"),
                     (now - 3600, "192.168.10.60", "aa:bb:cc:00:00:04")])
    con.commit()
    con.row_factory = sqlite3.Row
    current = {1: {"192.168.10.34", "fe80::1"}, 2: {"192.168.10.31"}}
    ev.device_addresses = lambda c, ids: ({d: current.get(d, set()) for d in ids}, {})
    ips = ev.game_dns_client_ips(con, ["Kids"], set())
    check("group devices, current + history, no link-local, no stale",
          ips == ["192.168.10.31", "192.168.10.33", "192.168.10.34", "fd00::2"])
    check("/game device left out", ev.game_dns_client_ips(con, ["Kids"], {1}) == ["192.168.10.31", "fd00::2"])
    check("no groups = nobody", ev.game_dns_client_ips(con, [], set()) == [])

    # ---- sync against a fake AdGuard ----
    live = {"user_rules": ["||google.cn^"]}
    posts: list[list[str]] = []

    def fake_request(base, user, password, path, body=None):
        if path == "/control/filtering/status":
            return {"user_rules": list(live["user_rules"])}
        posts.append(body["rules"])
        live["user_rules"] = list(body["rules"])
        return None

    logs: list[str] = []
    ev.log = logs.append
    ev.adguard_request = fake_request
    ev.adguard_base_url = lambda: "http://127.0.0.1:3000"
    ev.uci_get = lambda option: "Kids" if option == "game_dns_groups" else ""
    listfile = work / "g.list"
    listfile.write_text("block warthunder.com\nallow cdn.gaijin.net\n")
    ev.GAME_DNS_LIST = str(listfile)
    ev.load_game_dns_list.__defaults__ = (str(listfile),)
    ev.ADGUARD_API_SECRETS = str(work / "nocreds")
    ev.sync_game_dns(con, set())
    check("no credentials: nothing pushed, logged", not posts and any("not applied" in m for m in logs))
    creds = work / "adguard-api"
    creds.write_text("ADGUARD_USER=admin\nADGUARD_PASSWORD='x'\n")
    ev.ADGUARD_API_SECRETS = str(creds)
    ev.sync_game_dns(con, set())
    check("first sync pushes rules", len(posts) == 1 and posts[0][0] == "||google.cn^"
          and "||warthunder.com^$client=192.168.10.31|192.168.10.33|192.168.10.34|fd00::2" in posts[0])
    ev.sync_game_dns(con, set())
    check("unchanged: no second push", len(posts) == 1)
    ev.sync_game_dns(con, {1})
    check("/game lift pushes rules without that device",
          len(posts) == 2 and "||warthunder.com^$client=192.168.10.31|fd00::2" in posts[1])
    ev.uci_get = lambda option: ""
    ev.sync_game_dns(con, set())
    check("groups off: section removed, user rules kept", posts[-1] == ["||google.cn^"])

    def broken(*_args, **_kwargs):
        raise OSError("connection refused")

    ev.uci_get = lambda option: "Kids" if option == "game_dns_groups" else ""
    ev.adguard_request = broken
    ev.sync_game_dns(con, set())
    check("API failure is logged, not raised", any("sync failed" in m for m in logs))

    print(f"\n{'OK' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
