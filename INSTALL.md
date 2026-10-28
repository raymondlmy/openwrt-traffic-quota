# Installing

1. [Packages you need](#1-packages-you-need)
2. [Storage: flash the SD card and expand the ext4 partition](#2-storage-flash-the-sd-card-and-expand-the-ext4-partition)
3. [Optional: AdGuard Home (for per-site quotas)](#3-optional-adguard-home-for-per-site-quotas)
4. [Build the packages](#4-build-the-packages)
5. [Install](#5-install)
6. [Check it works](#6-check-it-works)
7. [Telegram reports](#7-telegram-reports)
8. [Changing settings later](#8-changing-settings-later), [Upgrading](#upgrading), [Removing](#removing)

## 1. Packages you need

**On the router** (OpenWrt 25.12.x, `apk`). The packages below are pulled in
automatically when you install ours, except where marked *you install*.

| Package | Needed for | Size (approx.) | Notes |
|---|---|---|---|
| `python3` | everything | 20 MB+ with its modules | collector, evaluator, reports |
| `sqlite3-cli` | everything | ~1 MB | install script and alert jobs |
| `nftables-json` | everything | small | collector reads counters as JSON |
| `luci-base`, `rpcd` | LuCI pages | — | already present on images with LuCI |
| `curl`, `ca-bundle` | Telegram reports | ~1 MB | |
| `adguardhome` | per-site quotas only | ~32 MB | *you install*, see step 3 |

**Check the router first:**

```sh
grep DISTRIB_RELEASE /etc/openwrt_release          # 25.12.x
uci -q get firewall.@defaults[0].flow_offloading     # must be empty or 0
```

Flow offloading makes connections skip nftables, so they would be neither
counted nor blocked. Turn it off under **Network → Firewall → Routing/NAT
Offloading**.

**On a Linux PC** (only to build the packages and expand the SD card):
`curl`, `tar` with zstd, `parted`, `e2fsprogs` (`e2fsck`, `resize2fs`). On
Debian/Ubuntu/Mint: `sudo apt install curl zstd parted e2fsprogs`.

## 2. Storage: flash the SD card and expand the ext4 partition

The database is written every minute and grows to about 50–150 MB, so it needs
**real storage**: an SD card, eMMC, USB or SSD. Not the router's internal flash.

Official OpenWrt SD-card images contain a **small root partition (about
100 MB)**, whatever the size of the card. Skip this step and `/opt` fills up.
On boards that boot from an SD card (NanoPi, Raspberry Pi and similar), use
the **ext4** image and grow its root partition to fill the card.

> If your router has no SD card but has USB, skip to [Using a USB drive
> instead](#using-a-usb-drive-instead).

### 2.1 Download and verify the image

Pick your board on [firmware-selector.openwrt.org](https://firmware-selector.openwrt.org)
and download the **ext4** "sysupgrade" or "factory" image (`...-ext4-...img.gz`).
Verify it against the `sha256sums` file in the same directory:

```sh
sha256sum -c --ignore-missing sha256sums        # must say: ...img.gz: OK
```

### 2.2 Flash it

**This erases the card.** Find the card's device name first, and be sure:

```sh
lsblk -o NAME,SIZE,MODEL,MOUNTPOINT             # e.g. sdb, 14.8G, your card reader
gunzip -k openwrt-*-ext4-*.img.gz
sudo dd if=openwrt-*-ext4-*.img of=/dev/sdX bs=4M conv=fsync status=progress
sync
```

(Or use balenaEtcher, which can flash the `.img.gz` directly.)

### 2.3 Expand the root partition and filesystem

With the card still in the PC and **not mounted** (`lsblk` shows no mountpoint;
unmount with `sudo umount /dev/sdX2` if needed):

```sh
sudo parted /dev/sdX print          # the ext4 partition is usually number 2
sudo parted /dev/sdX resizepart 2 100%
sudo e2fsck -f /dev/sdX2            # check before resizing
sudo resize2fs /dev/sdX2            # grow the filesystem to the new size
sudo parted /dev/sdX print          # partition 2 now fills the card
```

If `parted` says the partition table doesn't cover the whole disk and asks to
"fix" the GPT, answer **Fix**.

### 2.4 Boot and verify

Put the card in the router and boot it. Then, on the router:

```sh
df -h /            # Size should be close to the card size, e.g. 14.6G
mkdir -p /opt      # the default data directory lives here
```

> **Firmware upgrades:** a `sysupgrade` rewrites the image and can undo the
> expansion. Back up `/opt` and `/etc/r3s-accounting` first, or upgrade by
> flashing a second card and restoring.

### Using a USB drive instead

```sh
apk add block-mount kmod-usb-storage kmod-fs-ext4 e2fsprogs
block info                                  # find the drive, e.g. /dev/sda1
mkfs.ext4 -L data /dev/sda1                 # ERASES the drive
block detect | uci import fstab
uci set fstab.@mount[-1].target='/mnt/data'; uci set fstab.@mount[-1].enabled='1'
uci commit fstab; /etc/init.d/fstab boot
df -h /mnt/data
```

Then, after installing (step 5), set
`r3s-accounting.main.data_dir='/mnt/data/r3s-accounting'` as shown in
[Moving the database](#moving-the-database-to-other-storage).

## 3. Optional: AdGuard Home (for per-site quotas)

**AdGuard Home is only needed for per-site quotas** (e.g. "30 MB a day of
games.example.com"). Without it you still get everything else:

- traffic accounting and reports;
- Telegram reports;
- internet schedules and whole-device blocks.

Per-site quotas need it because a destination IP is only charged to a site
when AdGuard answered that device's DNS query for the site with that IP.

```sh
apk add adguardhome
```

1. **Keep its data on real storage.** The query log on `/var` would fill RAM.
   AdGuard runs as user `adguardhome`, so that user must own the directory:
   ```sh
   mkdir -p /opt/adguardhome && chown adguardhome:adguardhome /opt/adguardhome
   uci set adguardhome.config.work_dir='/opt/adguardhome'; uci commit adguardhome
   ```
2. **Move dnsmasq off port 53** (AdGuard takes it over), and keep dnsmasq for
   DHCP and local names:
   ```sh
   uci set dhcp.@dnsmasq[0].port='54'
   uci add_list dhcp.lan.dhcp_option="6,$(uci get network.lan.ipaddr | cut -d/ -f1)"
   uci commit dhcp; /etc/init.d/dnsmasq restart
   ```
3. Start it and finish the wizard at `http://<router>:3000`:
   ```sh
   /etc/init.d/adguardhome enable; /etc/init.d/adguardhome start
   ```
   In the wizard, listen for DNS on the router's LAN address, port 53.
4. **Settings → General settings:** make sure the **query log is enabled**.
   7 days or more works well. Add `127.0.0.1:54` as a private reverse DNS
   upstream if you want local hostnames.
5. If its `querylog.json` isn't under `/opt/adguardhome/data/` or
   `/var/lib/adguardhome/data/`, tell r3s-accounting where it is (after step 5):
   ```sh
   uci set r3s-accounting.main.adguard_querylog='/path/to/querylog.json'; uci commit r3s-accounting
   /etc/init.d/r3s-policy-evaluator restart
   ```

## 4. Build the packages

The packages contain no compiled code. You only need the host tools from an
OpenWrt **25.12 SDK**, for any target (about 250 MB download):

```sh
git clone https://github.com/raymondlmy/openwrt-traffic-quota.git
cd openwrt-traffic-quota
U=https://downloads.openwrt.org/releases/25.12.5/targets/rockchip/armv8
F=openwrt-sdk-25.12.5-rockchip-armv8_gcc-14.3.0_musl.Linux-x86_64.tar.zst
curl -fO "$U/$F" && curl -fO "$U/sha256sums"
grep " \*$F\$" sha256sums | sha256sum -c -          # must print OK
tar --zstd -xf "$F" "${F%.tar.zst}/staging_dir/host"

SDK="$PWD/${F%.tar.zst}" scripts/build-managed-apks.sh
# -> dist/apk/0.6.0/: r3s-accounting, luci-app-r3s, r3s-accounting-telegram, SHA256SUMS
```

The `packages/*/Makefile`s also work as an OpenWrt package feed. That path is
not yet tested; `build-managed-apks.sh` is the tested one.

## 5. Install

Copy the packages to the router and install. Leave out `r3s-accounting-telegram`
if you don't want Telegram, or `luci-app-r3s` if you don't use LuCI:

```sh
scp -O dist/apk/0.6.0/*.apk dist/apk/0.6.0/SHA256SUMS root@192.168.1.1:/tmp/
ssh root@192.168.1.1
cd /tmp && sha256sum -c SHA256SUMS
apk add --allow-untrusted r3s-accounting-0.6.0-r1.apk luci-app-r3s-0.6.0-r1.apk r3s-accounting-telegram-0.6.0-r1.apk
```

The install:

- creates the database in `data_dir` (default `/opt/r3s-accounting/data`) and
  bind-mounts it at `/var/lib/r3s-accounting`;
- **warns** about low space, flash storage or flow offloading;
- detects the LAN and WAN interfaces and writes
  `/usr/share/nftables.d/ruleset-pre/10-r3s-accounting.nft`;
- **restarts the firewall** (a few seconds of interruption);
- starts the collector and the policy evaluator.

## 6. Check it works

```sh
nft list tables                    # inet r3s_accounting, inet r3s_policy
logread -e r3s-accounting | tail   # "sample OK" about every minute
bw-rpt daily
```

Then open **Services → Traffic Quotas** in LuCI. Register your devices under
**Device Registry**, or approve them from **Unresolved MACs** as they appear.

## 7. Telegram reports

See **[TELEGRAM.md](TELEGRAM.md)**. In short: open **Services → Telegram
Reports**, paste a bot token from @BotFather, click **Detect** to find your
chat, then tick the reports you want.

## 8. Changing settings later

All settings are in `/etc/config/r3s-accounting` (see the README for the full
list).

**Interfaces** (e.g. add a guest LAN, or the WAN detection got it wrong):

```sh
uci set r3s-accounting.main.lan_device='br-lan br-guest'
uci set r3s-accounting.main.wan_device='wan'
uci commit r3s-accounting
r3s-accounting-render-nft; /etc/init.d/firewall restart
```

**Device groups** (the default is Personal, Kids and IoT):

```sh
uci set r3s-accounting.main.groups='Adults Kids Guests IoT'
uci set r3s-accounting.main.report_hourly_groups='Kids'   # hour-by-hour grid in reports
uci commit r3s-accounting
```

**Sampling interval** (seconds; default 60, minimum 10):

```sh
uci set r3s-accounting.main.interval='60'; uci commit r3s-accounting
/etc/init.d/r3s-accounting restart; /etc/init.d/r3s-policy-evaluator restart
```

### Moving the database to other storage

```sh
/etc/init.d/r3s-policy-evaluator stop; /etc/init.d/r3s-accounting stop
/etc/init.d/r3s-accounting-storage stop
mkdir -p /mnt/data/r3s-accounting
mv /opt/r3s-accounting/data/* /mnt/data/r3s-accounting/
uci set r3s-accounting.main.data_dir='/mnt/data/r3s-accounting'; uci commit r3s-accounting
/etc/init.d/r3s-accounting-storage start
/etc/init.d/r3s-accounting start; /etc/init.d/r3s-policy-evaluator start
```

## Upgrading

`apk add --allow-untrusted <new apk>` keeps:

- the database;
- `/etc/config/r3s-accounting`;
- the Telegram secrets;
- the device registry. A pre-upgrade copy is restored if the upgrade removes it.

## Removing

```sh
apk del luci-app-r3s r3s-accounting-telegram r3s-accounting
/etc/init.d/firewall restart
crontab -l | grep -v '# r3s-telegram:' | crontab -    # remove Telegram jobs
```

The database in `data_dir` and `/etc/r3s-accounting/` are left in place.
Delete them by hand if you don't want the history.
