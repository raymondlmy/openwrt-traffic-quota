#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Graype
"""Configure Telegram delivery for traffic accounting reports.

  r3s-telegram-setup                  interactive setup (prompts; token hidden)
  r3s-telegram-setup status           JSON status (never prints the token)
  r3s-telegram-setup apply            install values from secrets.new (LuCI)
  r3s-telegram-setup detect-chat      list chats that recently messaged the bot
  r3s-telegram-setup test             send a test message
  r3s-telegram-setup schedule [--daily on|off] [--daily-time HH:MM]
                              [--daily-health on|off] [--quota on|off] [--health on|off]

The secrets file is sourced by the shell report scripts, so the token and chat
ID are validated strictly and the token is never passed on a command line:
curl reads the request URL from stdin.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import subprocess
import sys
import tempfile

SECRETS = os.environ.get("R3S_TELEGRAM_SECRETS", "/etc/r3s-accounting/secrets")
PENDING = SECRETS + ".new"
CRONTAB_FILE = os.environ.get("R3S_CRONTAB_FILE", "")   # tests: edit a file, not crontab
CURL = os.environ.get("R3S_CURL", "curl")
TOKEN_RE = re.compile(r"[0-9]{5,15}:[A-Za-z0-9_-]{30,64}")
CHAT_RE = re.compile(r"-?[0-9]{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31}")
TIME_RE = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")
MARK = "# r3s-telegram:"
JOBS = {
    # name: (default schedule, command)
    "daily": ("0 6 * * *", "/usr/bin/r3s-telegram-daily-report"),
    "daily-health": ("0 6 * * *", "/usr/bin/r3s-telegram-daily-health-report"),
    "quota": ("* * * * *", "/usr/bin/r3s-telegram-quota-alerts"),
    "health": ("* * * * *", "/usr/bin/r3s-telegram-health-alerts"),
}


class SetupError(Exception):
    pass


def read_values(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                key, sep, value = line.strip().partition("=")
                if sep and key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
                    values[key] = value.strip().strip("'\"")
    except OSError:
        pass
    return values


def check(token: str | None, chat: str | None) -> None:
    if token and not TOKEN_RE.fullmatch(token):
        raise SetupError("the bot token should look like 123456789:AAE... (from @BotFather)")
    if chat and not CHAT_RE.fullmatch(chat):
        raise SetupError("the chat ID should be a number (e.g. 123456789 or -100...) or @channelname")


def write_secrets(token: str, chat: str) -> None:
    check(token, chat)
    directory = os.path.dirname(SECRETS) or "."
    os.makedirs(directory, exist_ok=True)
    old = os.umask(0o077)
    try:
        fd, tmp = tempfile.mkstemp(prefix=".secrets.", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            # Values are validated above, so single quotes are safe for `. file`.
            stream.write(f"TELEGRAM_BOT_TOKEN='{token}'\nTELEGRAM_CHAT_ID='{chat}'\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, SECRETS)
    finally:
        os.umask(old)


def api(token: str, method: str, fields: dict[str, str] | None = None) -> dict:
    """Call the Bot API; the token travels in curl's stdin config, not argv."""
    config = f'url = "https://api.telegram.org/bot{token}/{method}"\n'
    for key, value in (fields or {}).items():
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        config += f'data-urlencode = "{key}={escaped}"\n'
    try:
        result = subprocess.run([CURL, "-sS", "--connect-timeout", "10", "--max-time", "20", "-K", "-"],
                                input=config, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise SetupError("curl is not installed") from None
    if result.returncode != 0:
        raise SetupError("could not reach api.telegram.org (check the router's internet and DNS)")
    try:
        reply = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise SetupError("unexpected reply from Telegram") from None
    if not reply.get("ok"):
        raise SetupError("Telegram said: " + str(reply.get("description", "request rejected")))
    return reply


def bot_name(token: str) -> str:
    user = api(token, "getMe").get("result", {})
    return "@" + str(user.get("username", "?"))


# ---- crontab ----
def cron_read() -> list[str]:
    if CRONTAB_FILE:
        try:
            return open(CRONTAB_FILE, encoding="utf-8").read().splitlines()
        except OSError:
            return []
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
    return result.stdout.splitlines() if result.returncode == 0 else []


def cron_write(lines: list[str]) -> None:
    text = "\n".join(lines) + ("\n" if lines else "")
    if CRONTAB_FILE:
        open(CRONTAB_FILE, "w", encoding="utf-8").write(text)
        return
    subprocess.run(["crontab", "-"], input=text, text=True, check=True)


def cron_jobs() -> dict[str, str]:
    """Enabled jobs -> their schedule ('m h * * *')."""
    jobs: dict[str, str] = {}
    for line in cron_read():
        if MARK in line:
            name = line.split(MARK, 1)[1].strip()
            if name in JOBS:
                jobs[name] = " ".join(line.split()[:5])
    return jobs


# ---- commands ----
def cmd_status(_args) -> int:
    values = read_values(SECRETS)
    token, chat = values.get("TELEGRAM_BOT_TOKEN", ""), values.get("TELEGRAM_CHAT_ID", "")
    status = {"token_set": bool(token), "chat_id": chat, "bot": None, "error": None,
              "jobs": {name: name in cron_jobs() for name in JOBS},
              "daily_time": None}
    daily = cron_jobs().get("daily")
    if daily:
        minute, hour = daily.split()[:2]
        if minute.isdigit() and hour.isdigit():
            status["daily_time"] = f"{int(hour):02d}:{int(minute):02d}"
    if token:
        try:
            status["bot"] = bot_name(token)
        except SetupError as exc:
            status["error"] = str(exc)
    print(json.dumps(status))
    return 0


def cmd_apply(_args) -> int:
    """Merge secrets.new (written by LuCI) into the secrets file, then delete it."""
    try:
        pending = read_values(PENDING)
    finally:
        try:
            os.remove(PENDING)
        except OSError:
            pass
    if not pending:
        raise SetupError("nothing to apply")
    current = read_values(SECRETS)
    token = pending.get("TELEGRAM_BOT_TOKEN") or current.get("TELEGRAM_BOT_TOKEN", "")
    chat = pending.get("TELEGRAM_CHAT_ID", current.get("TELEGRAM_CHAT_ID", ""))
    if not token:
        raise SetupError("enter the bot token first")
    check(token, chat)
    name = bot_name(token) if "TELEGRAM_BOT_TOKEN" in pending else None
    write_secrets(token, chat)
    print(f"Saved. Bot {name}." if name else "Saved.")
    return 0


def cmd_detect_chat(_args) -> int:
    token = read_values(SECRETS).get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise SetupError("save the bot token first")
    chats: dict[str, str] = {}
    for update in api(token, "getUpdates").get("result", []):
        for key in ("message", "channel_post", "my_chat_member"):
            chat = (update.get(key) or {}).get("chat")
            if chat and "id" in chat:
                label = chat.get("title") or " ".join(
                    filter(None, [chat.get("first_name"), chat.get("last_name")])) or chat.get("username") or ""
                chats[str(chat["id"])] = f"{label} ({chat.get('type', '?')})"
    print(json.dumps([{"id": cid, "label": label} for cid, label in chats.items()]))
    return 0


def cmd_test(_args) -> int:
    values = read_values(SECRETS)
    token, chat = values.get("TELEGRAM_BOT_TOKEN", ""), values.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        raise SetupError("save the bot token and chat ID first")
    api(token, "sendMessage", {"chat_id": chat, "text": "✅ Traffic quota reports are set up on this router."})
    print("Test message sent.")
    return 0


def cmd_schedule(args) -> int:
    changes = {"daily": args.daily, "daily-health": args.daily_health, "quota": args.quota, "health": args.health}
    if args.daily_time and not TIME_RE.fullmatch(args.daily_time):
        raise SetupError("--daily-time must be HH:MM")
    enabled = cron_jobs()
    lines = [line for line in cron_read() if MARK not in line]
    for name, (default, command) in JOBS.items():
        want = changes[name] == "on" if changes[name] else name in enabled
        if not want:
            continue
        schedule = enabled.get(name, default)
        if name in ("daily", "daily-health") and args.daily_time:
            hour, minute = args.daily_time.split(":")
            schedule = f"{int(minute)} {int(hour)} * * *"
        lines.append(f"{schedule} {command} >/dev/null 2>&1 {MARK}{name}")
    cron_write(lines)
    print("Schedule saved: " + (", ".join(n for n in JOBS if any(l.endswith(MARK + n) for l in lines)) or "nothing enabled"))
    return 0


def cmd_interactive(_args) -> int:
    current = read_values(SECRETS)
    print("Telegram setup. Create a bot with @BotFather first and copy its token.")
    token = getpass.getpass("Bot token (input hidden, Enter keeps the current one): ").strip() \
        or current.get("TELEGRAM_BOT_TOKEN", "")
    check(token, None)
    print("Bot:", bot_name(token))
    write_secrets(token, current.get("TELEGRAM_CHAT_ID", ""))
    chat = input("Chat ID (Enter = detect: send any message to your bot first): ").strip()
    if not chat:
        cmd_detect_chat(None)
        chat = input("Chat ID from the list above: ").strip()
    check(token, chat)
    write_secrets(token, chat)
    cmd_test(None)
    if input("Enable all reports (daily 06:00, quota and health alerts)? [Y/n] ").strip().lower() in ("", "y", "yes"):
        cmd_schedule(argparse.Namespace(daily="on", daily_health="on", quota="on", health="on", daily_time=None))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="r3s-telegram-setup", description="Configure Telegram reports")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status")
    sub.add_parser("apply")
    sub.add_parser("detect-chat")
    sub.add_parser("test")
    s = sub.add_parser("schedule")
    for opt in ("--daily", "--daily-health", "--quota", "--health"):
        s.add_argument(opt, choices=("on", "off"))
    s.add_argument("--daily-time")
    args = parser.parse_args()
    handlers = {"status": cmd_status, "apply": cmd_apply, "detect-chat": cmd_detect_chat,
                "test": cmd_test, "schedule": cmd_schedule, None: cmd_interactive}
    try:
        return handlers[args.command](args)
    except SetupError as exc:
        print(f"r3s-telegram-setup: {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        return 130


if __name__ == "__main__":
    sys.exit(main())
