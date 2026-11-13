# r3s-accounting changelog

## 0.6.1-1 - 2026-11-13

- Per-device overrides: `r3s-policy override --device ID --action allow|block
  [--minutes N]`, `clear-override` and `overrides`. An override decides for
  that one device across all its policies (the per-policy `manual_override`
  applies to every device in the policy). `allow` needs `--minutes`; usage is
  still counted and blocks return when it expires. `block` covers a device
  with no policy too. Every change and expiry is logged in
  `device_override_log` (migration 013).
- `r3s-policy-evaluator --overrides-only`: fast pass (about a second) that
  rebuilds whole-device blocks and releases site blocks of allowed devices,
  so a change applies without waiting for the full run. The full run reads
  overrides just before applying, so a run already in progress cannot undo
  a newer change.
- `r3s-policy evaluate` lists `device override` rows first and marks policy
  rows an override changed; `r3s-policy-ro overrides` for read-only users.
- New option `telegram_bot` (default `0`) for the Telegram commands service.
- Evaluator about 4x faster (58 s -> 14 s per run on the R3S): it decoded the
  whole AdGuard query log every run (180-360 MB, 0.5-1 M lines) to use the
  last 2 hours. It now binary-searches the time-ordered log for the window
  start, matches the domain before decoding DNS answers, and reads quota reset
  points once per run instead of once per usage row. Schedules and quotas
  react within about 75 s instead of about 2 minutes. Verified on a frozen copy
  of the live log: evidence identical to the previous version.
- Install warning fixed: the "database on router flash" check never matched
  the root mount `/`, so it stayed silent on routers whose `/` is the flash
  overlay, the case it exists for.

## 0.6.0-1 - 2026-10-28

First public release, from a private home-router project (last private
release 0.5.0-16).

- IPv4 and IPv6 accounting and enforcement. IPv6 blocks match the device MAC,
  so rotated temporary addresses stay blocked.
- LAN/WAN interfaces come from `/etc/config/r3s-accounting` (`lan_device`,
  `wan_device`), auto-detected when empty. `r3s-accounting-render-nft` writes
  the accounting rules; names are validated before they reach nft.
- `data_dir` and `adguard_querylog` are configurable. Unused options are gone.
- No kernel 6.3 requirement: the rules use add-then-delete instead of
  `destroy table`.
- The installer warns about low space, flash storage and flow offloading.
- The live device registry is never packaged. An upgrade restores it from a
  pre-upgrade copy if it was removed, and a fresh install starts empty.
- `r3s-policy-ro`: read-only entry point for LuCI users with read access.
- Configurable device groups (`groups`) and report layout
  (`report_hourly_groups`, `report_device_groups`). Previously only Personal,
  Kids and IoT existed, and other groups were missing from reports.
- `interval` sets the collector and evaluator cycle (default 60 s).
- AdGuard CSV export marks each query with the device's group from the
  accounting database, instead of a fixed list of IP addresses.
