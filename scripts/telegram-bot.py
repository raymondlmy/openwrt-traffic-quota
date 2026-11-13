#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Graype
"""Telegram commands for per-device overrides: block, unblock, extend, status.

Runs as a procd service (r3s-telegram-bot). It long-polls the Bot API with
getUpdates, so every connection is outbound HTTPS to api.telegram.org:
no port is opened and no webhook is exposed.

Only the chat in /etc/r3s-accounting/secrets (TELEGRAM_CHAT_ID) is obeyed.
Anything else is ignored and reported to that chat. All changes go through
r3s-policy (validation, audit log) and r3s-policy-evaluator --overrides-only
(applies within seconds); the bot never touches nft or the database schema.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta

SECRETS = os.environ.get("R3S_TELEGRAM_SECRETS", "/etc/r3s-accounting/secrets")
CURL = os.environ.get("R3S_CURL", "curl")
POLICY = os.environ.get("R3S_POLICY", "/usr/bin/r3s-policy")
EVALUATOR = os.environ.get("R3S_EVALUATOR", "/usr/bin/r3s-policy-evaluator")
DB = os.environ.get("R3S_DB", "/var/lib/r3s-accounting/accounting.sqlite3")
STATE_DIR = os.environ.get("R3S_BOT_STATE", "/tmp/lib/r3s-accounting")
OFFSET_FILE = os.path.join(STATE_DIR, "telegram-bot.offset")
POLL_SECONDS = 25
MAX_AGE = 120            # ignore commands older than this (queued while the bot was down)
MAX_EXTEND = 12 * 60     # minutes
MAX_BLOCK = 7 * 24 * 60  # minutes
INTRUDER_REPORT_EVERY = 3600  # per sender
INTRUDER_REPORTS_PER_HOUR = 5  # in total

HELP = """Commands:
/status - what is blocked right now, and why
/block NAME [30m|2h|23:00] - block a device (until /unblock, or for a while)
/extend NAME 30m|2h|23:00 - let a device online despite its schedules/quotas
/unblock NAME - remove your block/extend; normal policies apply again
/devices - device names

NAME can be the start of a name, e.g. /extend lap 30m"""


# Shown in Telegram's "/" menu (setMyCommands), so the commands need no memorising.
MENU = [
    ("status", "What is blocked right now, and why"),
    ("extend", "Extra time: /extend NAME 30m (or 2h, 23:00)"),
    ("block", "Block a device: /block NAME [30m|2h|23:00]"),
    ("unblock", "Remove your block or extra time: /unblock NAME"),
    ("devices", "Device names"),
    ("help", "How to use these commands"),
]


class BotError(Exception):
    pass


def log(message: str) -> None:
    subprocess.run(["logger", "-t", "r3s-telegram-bot", message], check=False)


def read_secrets() -> tuple[str, str]:
    values: dict[str, str] = {}
    try:
        with open(SECRETS, encoding="utf-8") as stream:
            for line in stream:
                key, sep, value = line.strip().partition("=")
                if sep and key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
                    values[key] = value.strip().strip("'\"")
    except OSError:
        pass
    return values.get("TELEGRAM_BOT_TOKEN", ""), values.get("TELEGRAM_CHAT_ID", "")


def api(token: str, method: str, fields: dict[str, str] | None = None, max_time: int = 20) -> dict:
    """Call the Bot API; the token travels in curl's stdin config, not argv."""
    config = f'url = "https://api.telegram.org/bot{token}/{method}"\n'
    for key, value in (fields or {}).items():
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        config += f'data-urlencode = "{key}={escaped}"\n'
    result = subprocess.run([CURL, "-sS", "--connect-timeout", "10", "--max-time", str(max_time), "-K", "-"],
                            input=config, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise BotError(f"curl exit {result.returncode}")
    try:
        reply = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise BotError("unexpected reply from Telegram") from None
    if not reply.get("ok"):
        raise BotError(f"Telegram error {reply.get('error_code')}: {reply.get('description', '')}")
    return reply


def send(token: str, chat: str, text: str) -> None:
    api(token, "sendMessage", {"chat_id": chat, "text": text[:4000]})


def policy(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([POLICY, *args], capture_output=True, text=True, check=False, timeout=30)


def apply_now() -> bool:
    try:
        return subprocess.run([EVALUATOR, "--overrides-only"], capture_output=True, text=True,
                              check=False, timeout=60).returncode == 0
    except subprocess.TimeoutExpired:
        return False


# ---- parsing ----
def devices() -> list[tuple[int, str, str]]:
    out = policy("devices").stdout
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 4 and parts[0].isdigit() and parts[3] == "1":
            rows.append((int(parts[0]), parts[1], parts[2]))
    return rows


def find_device(name: str) -> tuple[int, str]:
    wanted = " ".join(name.strip().strip("'\"‘’“”").split()).lower()
    if not wanted:
        raise BotError("Which device? Send /devices for the names.")
    rows = devices()
    for matcher in (lambda n: n == wanted, lambda n: n.startswith(wanted), lambda n: wanted in n):
        found = [(d, n) for d, n, _g in rows if matcher(n.lower())]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise BotError("Which one? " + ", ".join(n for _d, n in found))
    raise BotError(f"No device called '{name}'. Send /devices for the names.")


DURATION_RE = re.compile(r"(\d{1,4})\s*(m|min|mins|minutes?|h|hr|hrs|hours?)?", re.IGNORECASE)
CLOCK_RE = re.compile(r"([01]?[0-9]|2[0-3]):([0-5][0-9])")


def parse_duration(text: str, now: datetime | None = None) -> int | None:
    """'30', '30m', '2h', '23:00' (next occurrence) -> minutes; None if not a duration."""
    text = text.strip()
    clock = CLOCK_RE.fullmatch(text)
    if clock:
        now = now or datetime.now()
        target = now.replace(hour=int(clock.group(1)), minute=int(clock.group(2)), second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return max(1, int((target - now).total_seconds() + 59) // 60)
    match = DURATION_RE.fullmatch(text)
    if not match:
        return None
    value = int(match.group(1))
    return value * 60 if (match.group(2) or "m").lower().startswith("h") else value


def split_name_duration(rest: str) -> tuple[str, int | None]:
    words = rest.split()
    if len(words) > 1:
        minutes = parse_duration(words[-1])
        if minutes is not None:
            return " ".join(words[:-1]), minutes
    return rest, None


def decisions() -> list[list[str]]:
    return [line.split("\t") for line in policy("evaluate").stdout.splitlines() if line.count("\t") >= 4]


def policy_block(row: list[str]) -> bool:
    """A block caused by a policy itself (not the override row, not flipped by one)."""
    return row[3] == "block" and row[0] != "device override" and "device override" not in row[4]


def blocked_by(device_id: int, rows: list[list[str]] | None = None) -> list[str]:
    rows = decisions() if rows is None else rows
    return [r[0] for r in rows if r[1] == str(device_id) and policy_block(r)]


# ---- commands ----
def cmd_status() -> str:
    rows = decisions()
    overrides = {r[1]: (r[2], r[4]) for r in rows if r[0] == "device override"}
    blocked: dict[str, list[str]] = {}
    for r in rows:
        if policy_block(r):
            blocked.setdefault(r[2], []).append(r[0])
    for device_id, (name, reason) in overrides.items():
        if reason.startswith("blocked"):
            blocked.setdefault(name, []).append("blocked by you")
    lines = []
    if blocked:
        lines.append("🔴 Blocked now:")
        lines += [f"• {name}: {', '.join(reasons)}" for name, reasons in sorted(blocked.items())]
    else:
        lines.append("✅ Nothing is blocked right now.")
    if overrides:
        lines.append("")
        lines.append("✋ Your overrides:")
        lines += [f"• {name}: {reason}" for name, reason in sorted(overrides.values())]
    return "\n".join(lines)


def cmd_devices() -> str:
    groups: dict[str, list[str]] = {}
    for _d, name, group in devices():
        groups.setdefault(group, []).append(name)
    return "\n".join(f"{group}: {', '.join(sorted(names))}" for group, names in sorted(groups.items())) or "No devices."


def change(args: list[str], who: str) -> str:
    result = policy(*args, "--source", "telegram", "--note", f"by {who}"[:80])
    if result.returncode != 0:
        lines = (result.stderr or result.stdout).strip().splitlines()
        raise BotError(lines[-1] if lines else "r3s-policy failed")
    applied = apply_now()
    message = result.stdout.strip()
    return message + ("" if applied else "\n(Applies within 2 minutes.)")


def cmd_block(rest: str, who: str) -> str:
    name, minutes = split_name_duration(rest)
    device_id, device = find_device(name)
    args = ["override", "--device", str(device_id), "--action", "block"]
    if minutes is not None:
        if not 1 <= minutes <= MAX_BLOCK:
            raise BotError("A timed block can last up to 7 days. Leave the time off to block until /unblock.")
        args += ["--minutes", str(minutes)]
    return "🔴 " + change(args, who)


def cmd_extend(rest: str, who: str) -> str:
    name, minutes = split_name_duration(rest)
    if minutes is None:
        raise BotError("How long? e.g. /extend " + (name or "NAME") + " 30m  (or 2h, or 23:00)")
    if not 1 <= minutes <= MAX_EXTEND:
        raise BotError("Extra time can be up to 12 hours.")
    device_id, device = find_device(name)
    reply = "🟢 " + change(["override", "--device", str(device_id), "--action", "allow",
                             "--minutes", str(minutes)], who)
    return reply + "\nSchedules and quotas apply again afterwards; usage is still counted."


def cmd_unblock(rest: str, who: str) -> str:
    device_id, device = find_device(rest)
    reply = "↩️ " + change(["clear-override", "--device", str(device_id)], who)
    still = blocked_by(device_id)
    if still:
        reply += (f"\nStill blocked by {', '.join(still)}. "
                  f"To let it online anyway: /extend {device} 30m")
    return reply


def handle(text: str, who: str) -> str:
    command, _sep, rest = text.strip().partition(" ")
    command = command.split("@", 1)[0].lower()
    rest = rest.strip()
    if command == "/status":
        return cmd_status()
    if command == "/devices":
        return cmd_devices()
    if command == "/block":
        return cmd_block(rest, who)
    if command in ("/extend", "/allow"):
        return cmd_extend(rest, who)
    if command == "/unblock":
        return cmd_unblock(rest, who)
    return HELP


# ---- expiry notices ----
def report_expiries(token: str, chat: str) -> None:
    try:
        con = sqlite3.connect(DB, timeout=5.0)
        con.row_factory = sqlite3.Row
        rows = con.execute("""SELECT log_id,device_id,device_name FROM device_override_log
                              WHERE event='expire' AND notified_at IS NULL ORDER BY log_id LIMIT 10""").fetchall()
    except sqlite3.Error:
        return
    try:
        current = decisions() if rows else []
        for row in rows:
            still = blocked_by(int(row["device_id"]), current)
            state = f"now blocked by {', '.join(still)}" if still else "now online"
            send(token, chat, f"⏰ {row['device_name']}: override ended; normal policies apply ({state}).")
            con.execute("UPDATE device_override_log SET notified_at=? WHERE log_id=?",
                        (int(time.time()), int(row["log_id"])))
            con.commit()
    finally:
        con.close()


# ---- main loop ----
def load_offset() -> int:
    try:
        return int(open(OFFSET_FILE, encoding="utf-8").read().strip())
    except (OSError, ValueError):
        return 0


def save_offset(offset: int) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(OFFSET_FILE, "w", encoding="utf-8") as stream:
        stream.write(str(offset))


def process(update: dict, token: str, chat: str, intruders: dict[str, float]) -> None:
    message = update.get("message") or {}
    text = message.get("text") or ""
    sender = message.get("from") or {}
    origin = message.get("chat") or {}
    if not text.startswith("/"):
        return
    who = sender.get("first_name") or sender.get("username") or str(sender.get("id", "?"))
    # Only a private chat that is the configured chat: there, sender == chat.
    # Commands from groups or anyone else are never executed.
    authorised = (str(origin.get("id")) == chat and origin.get("type") == "private"
                  and str(sender.get("id")) == chat)
    if not authorised:
        key, now = str(sender.get("id")), time.time()
        recent = [t for t in intruders.values() if now - t < 3600]
        if now - intruders.get(key, 0) > INTRUDER_REPORT_EVERY and len(recent) < INTRUDER_REPORTS_PER_HOUR:
            intruders[key] = now
            send(token, chat, f"⚠️ Ignored a command from someone else: {who} "
                              f"(@{sender.get('username', '-')}, id {sender.get('id')}): {text[:40]}")
        log(f"ignored command from id {sender.get('id')}")
        return
    if time.time() - int(message.get("date", 0)) > MAX_AGE:
        send(token, chat, f"Skipped an old command (sent while the router was busy or offline): {text[:40]}\nSend it again if you still want it.")
        return
    try:
        reply = handle(text, who)
    except BotError as exc:
        reply = str(exc)
    except Exception as exc:  # keep serving; the details go to syslog
        log(f"command failed: {text[:40]}: {exc!r}")
        reply = "Something went wrong; nothing was changed. Details are in the router log."
    log(f"{who}: {text[:60]}")
    send(token, chat, reply)


def main() -> int:
    offset = load_offset()
    intruders: dict[str, float] = {}
    backoff = 10
    menu_for = ""  # token the menu was registered for
    while True:
        token, chat = read_secrets()
        if not token or not chat or not re.fullmatch(r"[0-9]{1,20}", chat):
            # Commands need a private chat (positive numeric ID); see HELP in the LuCI page.
            time.sleep(60)
            continue
        try:
            if menu_for != token:
                commands = [{"command": c, "description": d} for c, d in MENU]
                api(token, "setMyCommands", {"commands": json.dumps(commands)})
                menu_for = token
            report_expiries(token, chat)
            updates = api(token, "getUpdates", {"offset": str(offset), "timeout": str(POLL_SECONDS),
                                                "allowed_updates": '["message"]'},
                          max_time=POLL_SECONDS + 15).get("result", [])
            backoff = 10
        except BotError as exc:
            # 409: another getUpdates (e.g. setup's detect-chat) is running.
            log(f"poll failed: {exc}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
            continue
        for update in updates:
            offset = max(offset, int(update.get("update_id", 0)) + 1)
            save_offset(offset)
            try:
                process(update, token, chat, intruders)
            except BotError as exc:
                log(f"reply failed: {exc}")


if __name__ == "__main__":
    sys.exit(main())
