#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Build the two noarch APKs with only the OpenWrt SDK's host apk + fakeroot
# (no full SDK build). Any 25.12.x SDK works: the packages contain no binaries.
#
#   SDK=/path/to/openwrt-sdk-25.12.x-... scripts/build-managed-apks.sh
#
# Output: dist/apk/<version>/ (not committed).
set -eu

[ -n "${SDK:-}" ] || { echo "set SDK=/path/to/openwrt-sdk-25.12.x-<target>_..." >&2; exit 1; }
REPO=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
APK="$SDK/staging_dir/host/bin/apk"
FAKEROOT="$SDK/staging_dir/host/bin/fakeroot"
[ -x "$APK" ] && [ -x "$FAKEROOT" ] || { echo "SDK host tools not found under $SDK" >&2; exit 1; }
export STAGING_DIR_HOST="$SDK/staging_dir/host"

field() { sed -n "s/^$1:=//p" "$REPO/packages/$2/Makefile"; }
ACC_VER="$(field PKG_VERSION r3s-accounting)-r$(field PKG_RELEASE r3s-accounting)"
LUCI_VER="$(field PKG_VERSION luci-app-r3s)-r$(field PKG_RELEASE luci-app-r3s)"
OUT="$REPO/dist/apk/$(field PKG_VERSION r3s-accounting)"
URL="https://github.com/raymondlmy/openwrt-traffic-quota"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUT"

A="$WORK/accounting"
install -d -m 0755 "$A/etc/config" "$A/etc/r3s-accounting" "$A/etc/init.d" "$A/usr/bin" \
    "$A/usr/lib/r3s-accounting" "$A/usr/share/r3s-accounting/migrations" "$A/usr/share/nftables.d/ruleset-pre"
install -m 0600 "$REPO/config/r3s-accounting.uci" "$A/etc/config/r3s-accounting"
for file in r3s-accounting r3s-accounting-storage r3s-accounting-registry r3s-policy-evaluator; do
    install -m 0755 "$REPO/config/$file.init" "$A/etc/init.d/$file"
done
install -m 0755 "$REPO/scripts/provision-device-registry.sh" "$A/usr/bin/r3s-accounting-provision-devices"
install -m 0755 "$REPO/scripts/render-accounting-nft.sh" "$A/usr/bin/r3s-accounting-render-nft"
install -m 0755 "$REPO/scripts/r3s-policy-ro.sh" "$A/usr/bin/r3s-policy-ro"
for file in r3s-accounting-report r3s-adguard-csv-report r3s-adguard-export r3s-policy r3s-policy-evaluator; do
    install -m 0755 "$REPO/src/$file.py" "$A/usr/bin/$file"
done
ln -s r3s-accounting-report "$A/usr/bin/bw-rpt"
ln -s r3s-adguard-csv-report "$A/usr/bin/adg-rpt"
install -m 0755 "$REPO/src/collector.py" "$A/usr/lib/r3s-accounting/collector.py"
# Example only: the live /etc/r3s-accounting/devices.json is never packaged.
install -m 0644 "$REPO/config/devices.example.json" "$A/usr/share/r3s-accounting/devices.example.json"
install -m 0644 "$REPO/config/nftables/templates/10-r3s-accounting.nft.in" "$A/usr/share/r3s-accounting/"
install -m 0644 "$REPO/config/schema.sql" "$A/usr/share/r3s-accounting/schema.sql"
install -m 0644 "$REPO/config/migrations/"*.sql "$A/usr/share/r3s-accounting/migrations/"
# 10-r3s-accounting.nft is rendered on the router by postinst, not packaged.
install -m 0644 "$REPO/config/nftables/ruleset-pre/20-r3s-policy.nft" "$A/usr/share/nftables.d/ruleset-pre/"

L="$WORK/luci"
install -d -m 0755 "$L/usr/share/luci/menu.d" "$L/www/luci-static/resources/view" "$L/usr/share/rpcd/acl.d"
install -m 0644 "$REPO/config/luci/menu.d/r3s-policy.json" "$L/usr/share/luci/menu.d/"
install -m 0644 "$REPO/config/luci/view/r3s-policy.js" "$L/www/luci-static/resources/view/"
install -m 0644 "$REPO/config/luci/acl.d/luci-app-r3s-policy.json" "$L/usr/share/rpcd/acl.d/"

"$FAKEROOT" "$APK" mkpkg --info "name:r3s-accounting" --info "version:$ACC_VER" \
  --info 'description:Per-device traffic accounting (IPv4+IPv6), internet schedules and per-site quotas for OpenWrt.' \
  --info 'arch:noarch' --info 'license:GPL-2.0-only' --info 'origin:openwrt-traffic-quota/r3s-accounting' \
  --info 'maintainer:Graype' --info "url:$URL" \
  --info 'depends:libc nftables-json python3 sqlite3-cli' --info 'provides:r3s-accounting-any' \
  --script "pre-upgrade:$REPO/packages/r3s-accounting/preinst.sh" \
  --script "post-install:$REPO/packages/r3s-accounting/postinst.sh" \
  --script "post-upgrade:$REPO/packages/r3s-accounting/postinst.sh" \
  --files "$A" --output "$OUT/r3s-accounting-$ACC_VER.apk"

"$FAKEROOT" "$APK" mkpkg --info "name:luci-app-r3s" --info "version:$LUCI_VER" \
  --info 'description:LuCI page (Services > Traffic Quotas) for r3s-accounting devices, schedules and quotas.' \
  --info 'arch:noarch' --info 'license:GPL-2.0-only' --info 'origin:openwrt-traffic-quota/luci-app-r3s' \
  --info 'maintainer:Graype' --info "url:$URL" \
  --info 'depends:libc luci-base r3s-accounting rpcd' --info 'provides:luci-app-r3s-any' \
  --files "$L" --output "$OUT/luci-app-r3s-$LUCI_VER.apk"

(cd "$OUT" && sha256sum "r3s-accounting-$ACC_VER.apk" "luci-app-r3s-$LUCI_VER.apk" > SHA256SUMS && cat SHA256SUMS)
