#!/usr/bin/env python3
"""Telegram command bot: authorisation, commands, durations, expiry notices.

Runs the real r3s-policy against a temporary database; Telegram sends are
captured instead of made, and the evaluator is a no-op.
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHAT = "42424242"
fails = 0


def check(name: str, condition: bool) -> None:
    global fails
    print(("PASS  " if condition else "FAIL  ") + name)
    fails += 0 if condition else 1


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="r3s-bot-"))
    db = work / "db"
    con = sqlite3.connect(db)
    con.executescript((REPO / "config/schema.sql").read_text())
    for m in sorted((REPO / "config/migrations").glob("*.sql")):
        with contextlib.suppress(sqlite3.Error):
            con.executescript(m.read_text())
    con.executemany("INSERT INTO devices(device_id,name,group_name,enabled) VALUES(?,?,?,1)",
                    [(1, "laptop-1", "Kids"), (2, "laptop-2", "Kids"), (3, "phone-1", "Kids"), (4, "tv-1", "IoT")])
    con.execute("""INSERT INTO policy_rules(policy_id,name,policy_type,service,days,start_minute,
                   end_minute,created_at,updated_at) VALUES(1,'night','schedule','internet',
                   '0,1,2,3,4,5,6',0,1439,0,0)""")
    con.executemany("INSERT INTO policy_rule_devices VALUES(1,?)", [(1,), (3,)])
    con.commit()

    policy = work / "r3s-policy"
    policy.write_text(f"#!/bin/sh\nexec {sys.executable} {REPO / 'src/r3s-policy.py'} --db {db} \"$@\"\n")
    policy.chmod(0o755)
    evaluator_log = work / "evaluator.log"
    evaluator = work / "evaluator"
    evaluator.write_text(f"#!/bin/sh\necho \"$@\" >> {evaluator_log}\n")
    evaluator.chmod(0o755)
    os.environ.update(R3S_POLICY=str(policy), R3S_EVALUATOR=str(evaluator), R3S_DB=str(db),
                      R3S_BOT_STATE=str(work / "state"), R3S_TELEGRAM_SECRETS=str(work / "secrets"))

    spec = importlib.util.spec_from_file_location("telegram_bot_test", REPO / "scripts/telegram-bot.py")
    bot = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(bot)
    sent: list[str] = []
    bot.send = lambda token, chat, text: sent.append(text)
    bot.log = lambda message: None
    intruders: dict[str, float] = {}

    def say(text: str, sender: str = CHAT, chat: str = CHAT, kind: str = "private", age: int = 0) -> str:
        sent.clear()
        bot.process({"update_id": 1, "message": {
            "text": text, "date": int(time.time()) - age,
            "from": {"id": int(sender), "first_name": "Owner" if sender == CHAT else "Stranger", "username": "x"},
            "chat": {"id": int(chat), "type": kind}}}, "TOKEN", CHAT, intruders)
        return "\n".join(sent)

    def override(device_id: int):
        return sqlite3.connect(db).execute(
            "SELECT action,expires_at,source,note FROM device_overrides WHERE device_id=?", (device_id,)).fetchone()

    # ---- authorisation ----
    reply = say("/block tv", sender="999", chat="999")
    check("stranger's command not executed", override(4) is None)
    check("owner told about the stranger", "Ignored a command from someone else" in reply and "999" in reply)
    check("stranger reported once per hour", say("/block tv", sender="999", chat="999") == "")
    say("/block tv", sender="999", chat="-100123", kind="group")
    check("group chat never executes", override(4) is None)
    reply = say("/block tv", sender="777", chat=CHAT)
    check("someone else in the owner's chat id is refused", override(4) is None)
    reply = say("/block tv", age=600)
    check("old queued command skipped, not executed", override(4) is None and "Skipped an old command" in reply)
    check("plain text ignored silently", say("hello") == "")

    # ---- commands ----
    check("/help lists commands", "/extend NAME" in say("/help"))
    check("unknown command shows help", "/status" in say("/whatever"))
    reply = say("/status")
    check("/status lists schedule blocks", "laptop-1: night" in reply and "phone-1: night" in reply)
    check("/devices groups names", "Kids: laptop-1, laptop-2, phone-1" in say("/devices"))
    check("ambiguous name asks which", "Which one? laptop-1, laptop-2" in say("/block laptop"))
    check("unknown name explained", "No device called" in say("/block fridge"))
    check("/extend needs a duration", "How long?" in say("/extend laptop-1") and override(1) is None)
    check("/extend over 12h refused", "up to 12 hours" in say("/extend laptop-1 13h") and override(1) is None)

    reply = say("/extend LAPTOP-1 30m")
    row = override(1)
    check("/extend sets a 30-minute allow from telegram",
          row is not None and row[0] == "allow" and row[2] == "telegram" and row[3] == "by Owner"
          and 1790 <= row[1] - time.time() <= 1800)
    check("/extend reply explains", reply.startswith("🟢 laptop-1 allowed until") and "usage is still counted" in reply)
    check("evaluator fast path run", "--overrides-only" in evaluator_log.read_text())
    reply = say("/status")
    check("/status no longer lists the extended device as blocked",
          "laptop-1: night" not in reply and "phone-1: night" in reply and "laptop-1: allowed until" in reply)

    reply = say("/block tv-1")
    check("/block with no time lasts until cleared", override(4)[0] == "block" and override(4)[1] is None
          and "until cleared" in reply)
    check("/status shows blocked-by-you for a device with no policy", "tv-1: blocked by you" in say("/status"))
    reply = say("/block phone 2h")
    check("/block with a duration", override(3)[0] == "block" and 7190 <= override(3)[1] - time.time() <= 7200)
    reply = say("/unblock tv")
    check("/unblock clears the override", override(4) is None and "override cleared" in reply)
    reply = say("/unblock laptop-1")
    check("/unblock of a scheduled device says it is still blocked",
          override(1) is None and "Still blocked by night" in reply and "/extend laptop-1 30m" in reply)

    # ---- durations ----
    now = datetime(2026, 11, 13, 21, 0, 30)
    check("23:00 -> 120 minutes", bot.parse_duration("23:00", now) == 120)
    check("07:00 tomorrow -> 600 minutes", bot.parse_duration("07:00", now) == 600)
    check("2h -> 120", bot.parse_duration("2h") == 120)
    check("45 -> 45", bot.parse_duration("45") == 45)
    check("not a duration -> None", bot.parse_duration("tv") is None)
    check("names with spaces", bot.split_name_duration("Nest Hub 30m") == ("Nest Hub", 30))

    # ---- expiry notices ----
    say("/extend laptop-1 30m")
    c = sqlite3.connect(db)
    c.execute("""INSERT INTO device_override_log(event_time,device_id,device_name,event,expires_at,source)
                 VALUES(?,1,'laptop-1','expire',?,'telegram')""", (int(time.time()), int(time.time())))
    c.execute("DELETE FROM device_overrides WHERE device_id=1")
    c.commit()
    sent.clear()
    bot.report_expiries("TOKEN", CHAT)
    check("expiry announced with the policy now in force",
          len(sent) == 1 and "laptop-1: override ended" in sent[0] and "now blocked by night" in sent[0])
    sent.clear()
    bot.report_expiries("TOKEN", CHAT)
    check("expiry announced only once", sent == [])

    print()
    print("ALL PASSED" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
