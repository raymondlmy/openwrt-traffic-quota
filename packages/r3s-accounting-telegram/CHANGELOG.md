# r3s-accounting-telegram changelog

## 0.6.1-4 - 2026-11-18

- Health alerts also deliver game DNS block alerts (`game_dns_alerts`, from
  r3s-accounting 0.6.1-7): once when AdGuard stops accepting updates (e.g.
  password changed), once when it recovers. Skipped if the table is absent.

## 0.6.1-3 - 2026-11-16

- `/game NAME 1h`: game time for one device. Lifts its internet schedules
  and its game/site limits until it ends (3 hours at most; `/unblock` ends
  it early). Other devices in the same game policy stay limited. `/extend`
  is unchanged: game/site limits still apply. Needs the matching
  `r3s-accounting`. Added to the command menu.

## 0.6.1-2 - 2026-11-15

- `/extend` lifts internet schedules only; game/site quotas and site
  schedules stay enforced (needs the matching `r3s-accounting`). The reply
  names any site policy still blocking the device.
- Bot polls with a 50 s long-poll timeout (was 25 s): half the requests to
  Telegram (about 1,700 a day instead of 3,500). Commands still arrive at
  once; an "override ended" notice can take up to ~50 s.

## 0.6.1-1 - 2026-11-13

- Telegram commands (`r3s-telegram-bot`, off by default): `/status`,
  `/block NAME [30m|2h|23:00]`, `/extend NAME 30m|2h|23:00`, `/unblock NAME`,
  `/devices`. Long polling over outbound HTTPS only; no port or webhook.
  Commands appear in Telegram's "/" menu. Names may contain spaces and need
  no quotes (`/block living room tv 30m`); a unique start of a name is enough.
  Obeyed only from the configured chat when it is a private chat with the
  bot; other senders are ignored and reported (at most 5 reports an hour).
  Commands queued for more than 2 minutes (bot or router down) are skipped,
  not replayed. Changes go through `r3s-policy`, then the evaluator's fast
  pass. Expired overrides are announced once.
- Upgrades restart a running bot on the new code (post-install script).
- `r3s-telegram-setup schedule --commands on|off`; `status` reports
  `commands`. `detect-chat` pauses the command service while it reads
  updates.

## 0.6.0-1 - 2026-10-28

First public release, from a private home-router project (last private
release 0.5.0-7).

- `r3s-telegram-setup`: interactive or LuCI-driven setup. It checks the token
  with Telegram, detects the chat ID, sends a test message, and turns each
  report on or off (cron lines marked `# r3s-telegram:<job>`). The token is
  never passed on a command line. Token and chat ID are validated, because the
  report scripts source the secrets file.
- `r3s-telegram-status`: read-only status for LuCI.
- Quota and health alerts wait up to 5 s for a database lock and log sqlite's
  own error text.
- No built-in chat ID default.
