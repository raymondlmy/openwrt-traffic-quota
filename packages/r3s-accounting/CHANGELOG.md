# r3s-accounting changelog

## 0.6.1-5 - 2026-11-17

- **Fix (0.6.1-4 cut every guarded device off the internet):** the tether
  guard dropped TTL/hop limit 63/127, but the forward hook sees packets
  after this router's own decrement, so a device's own traffic is 63/127
  there. It now drops 62/126 (tethered client: one hop at the device, one
  here). 0.6.1-4 was live 18:08-18:45 on 2026-11-17 and blocked all Kids
  devices; the pre-enforcement probe that "saw TTL 63 from one Kids phone"
  was that phone's own traffic, not a tethered client.

## 0.6.1-4 - 2026-11-17

- Tether guard: new UCI option `tether_guard_groups` (default empty = off).
  For devices in those groups, forwarded packets with IPv4 TTL or IPv6 hop
  limit 63 or 127 are dropped: that is a hotspot/tethered client behind the
  device (64 or 128, minus the one hop through it). The device's own traffic
  is untouched. Not lifted by `/extend` or `/game`. New nft set
  `tether_guard_mac`, filled by the evaluator from the registry each run.
  A hotspot over mobile data never reaches the router and is not affected.

## 0.6.1-3 - 2026-11-16

- Game time: `r3s-policy override --action allow --lift-sites --minutes N`
  lifts that one device's internet schedules **and** its site policies
  (custom-domain quotas and site schedules, e.g. game quotas) until it
  expires. A plain allow (`/extend`) is unchanged and still leaves site
  policies enforced. Usage is still counted; blocks return on expiry.
  Replacing the override with a plain allow or a block drops the lift.
- Migration 014 adds `lift_sites` to `device_overrides` and
  `device_override_log`. postinst runs it only when the column is missing.
  Without it, overrides still work and `--lift-sites` is refused.
- `--overrides-only` fast pass again releases site-block pairs, but only for
  devices with a site-lifting allow, so game time starts within seconds.
- `r3s-policy evaluate` shows those rows as `games allowed until HH:MM`;
  `r3s-policy overrides` gains a last column (`sites` or `-`).

## 0.6.1-2 - 2026-11-15

- An `allow` device override (extra time, `/extend`) now lifts only
  whole-internet schedules. Site policies (custom-domain quotas and site
  schedules, e.g. game quotas) stay enforced for that device. Previously it
  lifted every policy, so extra time for homework also opened the game sites
  without limit until it ended. `block` overrides are unchanged. The
  `--overrides-only` fast pass no longer releases site-block pairs.
  `r3s-policy evaluate` shows site rows as still blocked.

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
