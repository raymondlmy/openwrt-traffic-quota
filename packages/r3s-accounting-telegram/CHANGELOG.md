# r3s-accounting-telegram changelog

## 0.6.0-2 - 2026-11-13

- Telegram commands (`r3s-telegram-bot`, off by default): `/status`,
  `/block NAME [30m|2h|23:00]`, `/extend NAME 30m|2h|23:00`, `/unblock NAME`,
  `/devices`. Long polling over outbound HTTPS only; no port or webhook.
  Obeyed only from the configured chat when it is a private chat with the
  bot; other senders are ignored and reported (at most 5 reports an hour).
  Commands queued for more than 2 minutes (bot or router down) are skipped,
  not replayed. Changes go through `r3s-policy`, then the evaluator's fast
  pass. Expired overrides are announced once.
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
