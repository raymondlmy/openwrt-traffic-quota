#!/usr/bin/env python3
"""r3s-telegram-setup against a fake curl: validation, secrets file safety,
token never on a command line, chat detection, cron editing."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SETUP = REPO / "scripts/telegram-setup.py"
# Fake test tokens, assembled at runtime so the source never contains a
# token-shaped string (secret scanners flag those even when obviously fake).
FAKE_SECRET = "AAE" + "abcdefghijklmnopqrstuvwxyz" + "012345"
TOKEN = "1234567" + ":" + FAKE_SECRET
REJECTED_TOKEN = "0000000" + ":" + FAKE_SECRET
FAKE_CURL = r'''#!/usr/bin/env python3
import json, os, sys
log = os.environ["FAKE_LOG"]
config = sys.stdin.read()
with open(log, "a") as f:
    f.write(json.dumps({"argv": sys.argv, "config": config}) + "\n")
url = [l for l in config.splitlines() if l.startswith("url")][0]
method = url.rstrip('"').rsplit("/", 1)[1]
if "BADTOKEN" in url or "0000000:" in url:
    print(json.dumps({"ok": False, "description": "Unauthorized"})); sys.exit(0)
replies = {
    "getMe": {"ok": True, "result": {"username": "test_quota_bot"}},
    "getUpdates": {"ok": True, "result": [{"message": {"chat": {"id": 42424242, "type": "private", "first_name": "Alex"}}}]},
    "sendMessage": {"ok": True, "result": {}},
}
print(json.dumps(replies[method]))
'''
fails = 0


def check(name: str, condition: bool) -> None:
    global fails
    print(("PASS  " if condition else "FAIL  ") + name)
    fails += 0 if condition else 1


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="r3s-tg-"))
    curl = work / "curl"
    curl.write_text(FAKE_CURL)
    curl.chmod(0o755)
    env = dict(os.environ, R3S_TELEGRAM_SECRETS=str(work / "secrets"), R3S_CRONTAB_FILE=str(work / "crontab"),
               R3S_CURL=str(curl), FAKE_LOG=str(work / "log"))
    (work / "crontab").write_text("0 3 * * * /usr/bin/other-job\n")

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-I", str(SETUP), *args], env=env, capture_output=True, text=True)

    def pending(text: str) -> None:
        (work / "secrets.new").write_text(text)

    pending(f"TELEGRAM_BOT_TOKEN={TOKEN}\n")
    r = run("apply")
    check("apply saves a valid token and names the bot", r.returncode == 0 and "@test_quota_bot" in r.stdout)
    check("apply removes the pending file", not (work / "secrets.new").exists())
    mode = stat.S_IMODE((work / "secrets").stat().st_mode)
    check(f"secrets file is mode 600 (got {oct(mode)})", mode == 0o600)

    for bad in ("TELEGRAM_BOT_TOKEN=123:short\n", "TELEGRAM_BOT_TOKEN=$(reboot)\n",
                "TELEGRAM_CHAT_ID=1; rm -rf /\n", "TELEGRAM_CHAT_ID='`id`'\n"):
        before = (work / "secrets").read_text()
        pending(bad)
        r = run("apply")
        check(f"rejects {bad.strip()!r} and keeps the old secrets",
              r.returncode == 1 and (work / "secrets").read_text() == before and not (work / "secrets.new").exists())

    pending(f"TELEGRAM_BOT_TOKEN={REJECTED_TOKEN}\n")
    r = run("apply")
    check("a token Telegram rejects is not saved", r.returncode == 1 and TOKEN in (work / "secrets").read_text())

    r = run("detect-chat")
    check("detect-chat lists chats that messaged the bot",
          r.returncode == 0 and json.loads(r.stdout) == [{"id": "42424242", "label": "Alex (private)"}])
    pending("TELEGRAM_CHAT_ID=42424242\n")
    r = run("apply")
    check("chat ID saved without re-entering the token", r.returncode == 0 and "42424242" in (work / "secrets").read_text()
          and TOKEN in (work / "secrets").read_text())

    sourced = subprocess.run(["sh", "-c", '. "$1"; printf "%s|%s" "$TELEGRAM_BOT_TOKEN" "$TELEGRAM_CHAT_ID"', "sh",
                              str(work / "secrets")], capture_output=True, text=True)
    check("report scripts can source the secrets file", sourced.stdout == f"{TOKEN}|42424242")

    r = run("test")
    check("test sends a message", r.returncode == 0)

    calls = [json.loads(line) for line in (work / "log").read_text().splitlines()]
    check(f"token never appears in curl's command line ({len(calls)} calls)",
          all(TOKEN not in " ".join(c["argv"]) for c in calls))

    r = run("schedule", "--daily", "on", "--quota", "on", "--daily-time", "07:30")
    cron = (work / "crontab").read_text()
    check("schedule adds marked jobs and keeps unrelated cron lines",
          "/usr/bin/other-job" in cron and "30 7 * * * /usr/bin/r3s-telegram-daily-report" in cron
          and "* * * * * /usr/bin/r3s-telegram-quota-alerts" in cron and "health-alerts" not in cron)
    run("schedule", "--quota", "off")
    cron = (work / "crontab").read_text()
    check("turning one job off leaves the others", "quota-alerts" not in cron and "30 7 * * *" in cron and "other-job" in cron)
    r = run("schedule", "--daily-time", "25:00")
    check("invalid time rejected", r.returncode == 1)

    status = json.loads(run("status").stdout)
    check("status reports bot, chat, jobs and time without the token",
          status["bot"] == "@test_quota_bot" and status["chat_id"] == "42424242" and status["jobs"]["daily"]
          and not status["jobs"]["quota"] and status["daily_time"] == "07:30" and TOKEN not in json.dumps(status))

    print()
    print("ALL PASSED" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
