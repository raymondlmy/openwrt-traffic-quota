# openwrt-traffic-quota

Per-device traffic accounting, internet schedules and per-site daily quotas
for OpenWrt routers, over **IPv4 and IPv6**.

- **See who used what.** Every forwarded byte is counted per device (by MAC
  address), kept as hourly history in SQLite, and summarised by `bw-rpt`.
- **Schedules.** Block a device's whole internet access on chosen days and
  times, e.g. a TV overnight or a child's devices at bedtime.
- **Per-site quotas.** Give a device a daily budget for a set of domains, e.g.
  30 MB of a games site. When it's used up, only that device/site pair is
  blocked until midnight.
- **LuCI page** (Services → Traffic Quotas) to register devices, approve new
  MAC addresses and edit policies. Read-only LuCI users can view but not change
  anything.

Two packages: **`r3s-accounting`** (collector, reports, policy evaluator, CLI)
and **`luci-app-r3s`** (web page). The `r3s` names are historical: the project
started on a NanoPi R3S. Nothing in it is specific to that board.

## How it works

```
LAN devices ──► nftables (inet r3s_accounting)  counts bytes per IP and per IP→destination
             ──► nftables (inet r3s_policy)      drops/rejects blocked devices and pairs
                     ▲                 │
                     │ sets            │ counters, every 60 s
               r3s-policy-evaluator   collector.py ──► SQLite (persistent storage)
                     ▲                 │                   ▲
                     └──── policies ◄──┴── IP → MAC ◄──────┘ DHCP leases + neighbour table
```

- **Measurement only, never routing.** Both nft tables sit beside fw4's table,
  at priorities just before fw4's forward chain. The accounting table never
  accepts or drops anything.
- **Identity is the MAC address.** IPv4 addresses are mapped through DHCP
  leases, IPv6 addresses through the neighbour table. IPv6 blocks match the
  **MAC**, so devices that rotate temporary IPv6 addresses stay blocked.
- **Per-site quotas need DNS evidence.** A destination IP is only charged to a
  site's quota if AdGuard Home recently answered that device's query for the
  site with that IP. This avoids charging unrelated traffic that shares a CDN.

## Requirements

| | |
|---|---|
| OpenWrt | **25.12** (apk). Tested on 25.12.5 (rockchip/armv8). 24.10 (opkg) is untested. |
| Storage | **Persistent, writable storage that isn't the router's flash**: an SD card, eMMC, USB or SSD, with about 50–150 MB free. The database is written every minute. Typical SBCs (NanoPi, Raspberry Pi, x86) qualify. Most 16–128 MB flash routers don't. |
| Packages | `python3`, `sqlite3-cli` and `nftables-json`, pulled in automatically. |
| Per-site quotas | **AdGuard Home** with its query log enabled. Schedules and whole-device blocks work without it. |
| Firewall | **Flow offloading off.** Offloaded connections skip nftables, so they would be neither counted nor blocked. The installer warns if it's on. |
| Network | Devices must reach the router with their own MAC. Wi-Fi access points must be in bridge mode, not routing/NAT. |

## Limitations

- **New or randomised MACs** are not covered by policies until you approve them
  on the LuCI page (they appear under *Unresolved MACs*).
- **Per-site quotas are DNS-based.** Clients using their own DNS-over-HTTPS
  bypass AdGuard and so are not charged. Block DoH in AdGuard if that matters.
- **Counters are sampled every 60 s.** A quota can overshoot by up to about a
  minute of traffic before the block applies.
- **Blocks are emptied on a firewall reload** until the evaluator's next pass,
  up to 60 s later.
- **IPv6 privacy addresses** are attributed through the neighbour table. An
  address that has already left that table when it is sampled counts as
  *unresolved*.

## Quick start

See **[INSTALL.md](INSTALL.md)** for building and installing. Once installed:

```sh
r3s-policy devices                      # registered devices
r3s-policy unresolved                   # MACs seen but not yet assigned
r3s-policy add-device --name laptop-1 --group Kids --mac aa:bb:cc:dd:ee:ff   # groups: Personal, Kids, IoT
r3s-policy add --name "bedtime" --type schedule --service internet \
    --days 0,1,2,3,4 --start 1320 --end 420 --devices 1      # 22:00-07:00 Sun-Thu
r3s-policy add --name "games" --type service_quota --service custom \
    --domains example-games.com --quota 30000000 --devices 1  # 30 MB/day
bw-rpt daily                            # today's usage per device
```

Or use **Services → Traffic Quotas** in LuCI.

## Configuration

All settings live in `/etc/config/r3s-accounting`.

| Option | Default | Meaning |
|---|---|---|
| `retention_days` | `14` | Days of history kept |
| `lan_device` | auto (`network.lan.device`) | LAN interface(s) to measure, space-separated |
| `wan_device` | auto (`pppoe-wan` for PPPoE, else `network.wan.device`) | WAN interface(s), space-separated |
| `data_dir` | `/opt/r3s-accounting/data` | Where the database lives (persistent storage) |
| `adguard_querylog` | auto | AdGuard Home `querylog.json` path |

After changing `lan_device` or `wan_device`:

```sh
r3s-accounting-render-nft && /etc/init.d/firewall restart
```

The device registry is `/etc/r3s-accounting/devices.json`. It's written by
LuCI and `r3s-policy` and loaded into the database at boot. Packages never
ship or overwrite it. An example is in
`/usr/share/r3s-accounting/devices.example.json`.

## Security notes

- **Nothing listens on the network.** The LuCI page uses LuCI's normal
  authentication.
- **Read-only LuCI users** only get `/usr/bin/r3s-policy-ro`, which allows the
  five viewing subcommands with no options.
- **Untrusted input is validated before it reaches nft:**
  - addresses are parsed with `ipaddress`;
  - domains are checked against a hostname pattern;
  - MACs must be in canonical form;
  - interface names must be plain Linux names.

  There is no shell interpolation of untrusted input.

## Tests

```sh
tests/ipv6_netns_e2e.sh          # real traffic through the rules, collector and evaluator
                                 # in throwaway network namespaces (rootless)
tests/test_render_nft.sh         # interface-name rendering and validation
tests/test_registry_upgrade.sh   # install / upgrade registry handling
python3 tests/test_ipv6_units.py
python3 tests/collector_undercount_sim.py
```

Rootless namespaces need `unshare -rn` to work (most desktop Linux distros).

## License

Copyright (C) 2026 Graype. [GPL-2.0-only](LICENSE).
