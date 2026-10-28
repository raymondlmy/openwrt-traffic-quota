# Installing

## 1. Check the router first

```sh
grep DISTRIB_RELEASE /etc/openwrt_release          # 25.12.x
df -h /opt 2>/dev/null || df -h /                    # 50 MB+ free on real storage
uci -q get firewall.@defaults[0].flow_offloading     # must not be 1
```

If `/opt` doesn't exist or sits on the router's flash, choose a directory
on an SD card, eMMC, USB or SSD mount. After installing, set it as `data_dir`
(see step 3).

**SD-card images:** official OpenWrt SD images have a small root partition
(about 100 MB) regardless of card size. To use the rest of the card for `/opt`,
grow the partition and filesystem first (on another Linux PC, with the card
unmounted):

```sh
sudo parted /dev/sdX resizepart <N> 100%    # N = the ext4 root partition
sudo resize2fs /dev/sdX<N>
```

## 2. Build the packages

The packages contain no compiled code. You only need the host tools from an
OpenWrt **25.12 SDK** for any target (about 250 MB download):

```sh
U=https://downloads.openwrt.org/releases/25.12.5/targets/rockchip/armv8
F=openwrt-sdk-25.12.5-rockchip-armv8_gcc-14.3.0_musl.Linux-x86_64.tar.zst
curl -fO "$U/$F" && curl -fO "$U/sha256sums"
grep " \*$F\$" sha256sums | sha256sum -c -          # must print OK
tar --zstd -xf "$F" "${F%.tar.zst}/staging_dir/host"

SDK="$PWD/${F%.tar.zst}" scripts/build-managed-apks.sh
# -> dist/apk/0.6.0/r3s-accounting-0.6.0-r1.apk, luci-app-r3s-0.6.0-r1.apk, SHA256SUMS
```

The `packages/*/Makefile`s also work as an OpenWrt package feed. That path is
not yet tested; `build-managed-apks.sh` is the tested one.

## 3. Install

```sh
scp -O dist/apk/0.6.0/*.apk dist/apk/0.6.0/SHA256SUMS root@192.168.1.1:/tmp/
ssh root@192.168.1.1
cd /tmp && sha256sum -c SHA256SUMS
apk add --allow-untrusted r3s-accounting-0.6.0-r1.apk luci-app-r3s-0.6.0-r1.apk
```

The install:

- creates the database in `data_dir` and bind-mounts it at `/var/lib/r3s-accounting`;
- **warns** about low space, flash storage or flow offloading;
- detects the LAN and WAN interfaces and writes
  `/usr/share/nftables.d/ruleset-pre/10-r3s-accounting.nft`;
- **restarts the firewall** (a few seconds of interruption);
- starts the collector and the policy evaluator.

**Different interface names** (e.g. a guest LAN as well, or a WAN the
auto-detection gets wrong):

```sh
uci set r3s-accounting.main.lan_device='br-lan br-guest'
uci set r3s-accounting.main.wan_device='wan'
uci commit r3s-accounting
r3s-accounting-render-nft; /etc/init.d/firewall restart
```

**Moving the database to other storage** after install:

```sh
/etc/init.d/r3s-policy-evaluator stop; /etc/init.d/r3s-accounting stop
/etc/init.d/r3s-accounting-storage stop
mkdir -p /mnt/sda1/r3s-accounting
mv /opt/r3s-accounting/data/* /mnt/sda1/r3s-accounting/
uci set r3s-accounting.main.data_dir='/mnt/sda1/r3s-accounting'; uci commit r3s-accounting
/etc/init.d/r3s-accounting-storage start
/etc/init.d/r3s-accounting start; /etc/init.d/r3s-policy-evaluator start
```

## 4. Check it works

```sh
nft list tables                    # inet r3s_accounting, inet r3s_policy
logread -e r3s-accounting | tail   # "sample OK" every minute
bw-rpt daily
```

Then open **Services → Traffic Quotas** in LuCI and register your devices.

## Upgrading

`apk add --allow-untrusted <new apk>` keeps:

- the database;
- `/etc/config/r3s-accounting`;
- the device registry. A pre-upgrade copy is restored if the upgrade removes it.

## Removing

```sh
apk del luci-app-r3s r3s-accounting
/etc/init.d/firewall restart
```

The database in `data_dir` and `/etc/r3s-accounting/` are left in place.
Delete them by hand if you don't want the history.
