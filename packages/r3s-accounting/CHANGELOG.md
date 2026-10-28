# r3s-accounting changelog

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
