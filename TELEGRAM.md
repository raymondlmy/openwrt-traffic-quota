# Telegram reports

Package **`r3s-accounting-telegram`** sends traffic reports and alerts to a
Telegram chat. Install it alongside `r3s-accounting` (see
[INSTALL.md](INSTALL.md) step 5). It pulls in `curl` and `ca-bundle`.

| Report | When | What it says |
|---|---|---|
| **Daily traffic report** | once a day (default 06:00) | yesterday's download/upload per device and group, with hourly detail for the groups in `report_hourly_groups` |
| **Daily router health** | once a day (default 06:00) | yesterday's CPU and memory use |
| **Quota alerts** | checked every minute | a device used up a per-site quota, with the domain and IP that tipped it over |
| **Router CPU/memory alerts** | checked every minute | CPU or memory above 90% for an hour, and when it recovers |

> **Privacy:** reports contain device names and their usage, and are sent
> through Telegram's servers. Use a private chat or group you control.

## 1. Create a bot

1. In Telegram, open a chat with **@BotFather**.
2. Send `/newbot`, pick a name and a username ending in `bot`.
3. BotFather replies with a **token** like `123456789:AAE…`. Keep it secret:
   anyone with it can send messages as your bot.
4. Open a chat with your new bot and **send it any message** (e.g. `hi`). A bot
   can't message you until you've messaged it. For a group, add the bot to the
   group and post a message there.

## 2. Set it up in LuCI (recommended)

**Services → Telegram Reports**:

1. **Bot token:** paste the token and click **Save token**. The router checks
   it with Telegram first and shows the bot's name. The token is stored in
   `/etc/r3s-accounting/secrets` (readable by root only) and is never shown
   again. To replace it, paste a new one.
2. **Chat:** click **Detect**. Chats that recently messaged the bot are listed;
   click **Use** next to yours. (Or type a chat ID and click **Save chat ID**.
   Group IDs are negative, e.g. `-1001234567890`.)
3. Click **Send test message.** It should arrive within seconds.
4. **Reports:** tick the ones you want, set the time for the daily reports, and
   click **Save reports**.

LuCI users with read-only access can see the status but not change anything.

## 2. (alternative) Set it up over SSH

```sh
r3s-telegram-setup
```

It asks for the token (hidden as you type), detects the chat, sends a test
message, and offers to turn on all reports. Individual commands:

```sh
r3s-telegram-setup status                                   # JSON; never prints the token
r3s-telegram-setup detect-chat
r3s-telegram-setup test
r3s-telegram-setup schedule --daily on --daily-time 07:30 \
    --daily-health off --quota on --health on
```

The schedule is stored in root's crontab as lines ending in
`# r3s-telegram:<job>`. The tool only edits its own lines.

## Troubleshooting

```sh
logread -e r3s-telegram | tail          # every job logs success or the reason it failed
r3s-telegram-setup status               # "error" explains connection or token problems
```

| Symptom | Fix |
|---|---|
| `Telegram said: Unauthorized` | The token is wrong or was revoked. Get a new one with `/token` in @BotFather and save it again. |
| `Telegram said: Bad Request: chat not found` | Message the bot first, then use **Detect**. For groups, the bot must be a member. |
| `could not reach api.telegram.org` | The router has no internet or DNS. Check `nslookup api.telegram.org`. |
| **Detect** finds nothing | Send the bot a new message and click Detect within a few minutes. Telegram only keeps recent updates. |
| No daily report | Check that the job is ticked and that the router's time zone is right (**System → System**). |
| `could not read quota alerts: database is locked` | Rare. The job retries every minute. If it keeps happening, see the collector's log. |

## Removing

Turn the reports off on the LuCI page (or `r3s-telegram-setup schedule --daily
off --daily-health off --quota off --health off`), then
`apk del r3s-accounting-telegram`. Delete `/etc/r3s-accounting/secrets` to
remove the token, and revoke it in @BotFather (`/revoke`).
