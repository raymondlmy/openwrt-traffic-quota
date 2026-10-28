# r3s-accounting-telegram changelog

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
