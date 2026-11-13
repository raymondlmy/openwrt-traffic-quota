# luci-app-r3s changelog

## 0.6.0-2 - 2026-11-13

- **Policies by Device** table under Policy Management: one row per device
  listing its schedules and quotas (disabled ones marked) and whether it is
  blocked right now, and by which policy. Previously a device's schedule was
  only visible by scanning every policy's device list.

## 0.6.0-1 - 2026-10-28

First public release, from a private home-router project (last private
release 0.5.0-16).

- Menu entry **Services → Traffic Quotas**.
- Read-only LuCI users can view the page but not change anything: the page
  loads through `r3s-policy-ro`, and changes need write access.
- Removed the unused ucode controller.
- **Services → Telegram Reports**: enter the bot token (never shown again),
  detect the chat ID, send a test message, and choose which reports run.
- Device groups come from the `groups` option instead of a fixed list.
