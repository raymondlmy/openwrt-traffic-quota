include $(TOPDIR)/rules.mk

PKG_NAME:=r3s-accounting
PKG_VERSION:=0.6.0
PKG_RELEASE:=1
PKG_MAINTAINER:=Graype
PKG_LICENSE:=GPL-2.0-only
PKG_LICENSE_FILES:=LICENSE

include $(INCLUDE_DIR)/package.mk

# Sources live in this repository (two levels up). realpath resolves the
# package/feeds symlink so this also works when built as a feed.
REPO_ROOT:=$(realpath $(CURDIR)/../..)

define Build/Prepare
	mkdir -p $(PKG_BUILD_DIR)
	$(CP) $(REPO_ROOT)/src $(REPO_ROOT)/config $(REPO_ROOT)/scripts $(PKG_BUILD_DIR)/
endef

define Package/r3s-accounting
  SECTION:=net
  CATEGORY:=Network
  TITLE:=Per-device traffic accounting, quotas and schedules
  URL:=https://github.com/raymondlmy/openwrt-traffic-quota
  DEPENDS:=+python3 +sqlite3-cli +nftables-json
  PKGARCH:=all
endef

define Package/r3s-accounting/description
  Counts forwarded IPv4 and IPv6 traffic per device (identified by MAC) with
  nftables, keeps hourly history in SQLite, and enforces per-device internet
  schedules and per-site daily quotas. Needs persistent storage (SD, eMMC,
  USB or SSD); per-site quotas need AdGuard Home.
endef

define Build/Compile
	:
endef

define Package/r3s-accounting/conffiles
/etc/config/r3s-accounting
endef

define Package/r3s-accounting/install
	$(INSTALL_DIR) $(1)/etc/config
	$(INSTALL_CONF) $(PKG_BUILD_DIR)/config/r3s-accounting.uci $(1)/etc/config/r3s-accounting
	$(INSTALL_DIR) $(1)/etc/r3s-accounting
	$(INSTALL_DIR) $(1)/etc/init.d
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/config/r3s-accounting.init $(1)/etc/init.d/r3s-accounting
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/config/r3s-accounting-storage.init $(1)/etc/init.d/r3s-accounting-storage
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/config/r3s-accounting-registry.init $(1)/etc/init.d/r3s-accounting-registry
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/config/r3s-policy-evaluator.init $(1)/etc/init.d/r3s-policy-evaluator
	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/scripts/provision-device-registry.sh $(1)/usr/bin/r3s-accounting-provision-devices
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/scripts/render-accounting-nft.sh $(1)/usr/bin/r3s-accounting-render-nft
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/scripts/r3s-policy-ro.sh $(1)/usr/bin/r3s-policy-ro
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/src/r3s-accounting-report.py $(1)/usr/bin/r3s-accounting-report
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/src/r3s-adguard-csv-report.py $(1)/usr/bin/r3s-adguard-csv-report
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/src/r3s-adguard-export.py $(1)/usr/bin/r3s-adguard-export
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/src/r3s-policy.py $(1)/usr/bin/r3s-policy
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/src/r3s-policy-evaluator.py $(1)/usr/bin/r3s-policy-evaluator
	$(LN) r3s-accounting-report $(1)/usr/bin/bw-rpt
	$(LN) r3s-adguard-csv-report $(1)/usr/bin/adg-rpt
	$(INSTALL_DIR) $(1)/usr/lib/r3s-accounting
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/src/collector.py $(1)/usr/lib/r3s-accounting/collector.py
	$(INSTALL_DIR) $(1)/usr/share/r3s-accounting/migrations
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/config/devices.example.json $(1)/usr/share/r3s-accounting/devices.example.json
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/config/nftables/templates/10-r3s-accounting.nft.in $(1)/usr/share/r3s-accounting/
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/config/schema.sql $(1)/usr/share/r3s-accounting/schema.sql
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/config/migrations/*.sql $(1)/usr/share/r3s-accounting/migrations/
	$(INSTALL_DIR) $(1)/usr/share/nftables.d/ruleset-pre
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/config/nftables/ruleset-pre/20-r3s-policy.nft $(1)/usr/share/nftables.d/ruleset-pre/
endef

define Package/r3s-accounting/preinst
#!/bin/sh
# Keep a copy of the live device registry before an upgrade. Packages up to
# 0.5.0-15 shipped /etc/r3s-accounting/devices.json; from 0.5.0-16 it is not
# packaged, so apk may remove the file it used to own. postinst restores it.
[ "$${IPKG_NO_SCRIPT}" = "1" ] && exit 0
[ -n "$${IPKG_INSTROOT}" ] && exit 0
REG=/etc/r3s-accounting/devices.json
if [ -s "$${REG}" ]; then
    cp -p "$${REG}" "$${REG}.pre-upgrade" || exit 1
fi
exit 0
endef

define Package/r3s-accounting/postinst
#!/bin/sh
[ "$${IPKG_NO_SCRIPT}" = "1" ] && exit 0
[ -n "$${IPKG_INSTROOT}" ] && exit 0

DATA_DIR="$$(uci -q get r3s-accounting.main.data_dir || true)"
DATA_DIR="$${DATA_DIR:-/opt/r3s-accounting/data}"
DB="$${DATA_DIR}/accounting.sqlite3"
mkdir -p "$${DATA_DIR}" /tmp/lib/r3s-accounting /etc/r3s-accounting

# Preflight warnings: none of these stop the install.
warn() { echo "r3s-accounting: WARNING: $$*"; logger -t r3s-accounting "WARNING: $$*"; }
free_kb="$$(df -k "$${DATA_DIR}" 2>/dev/null | awk 'NR==2 {print $$4}')"
[ -n "$${free_kb}" ] && [ "$${free_kb}" -lt 51200 ] && \
    warn "only $${free_kb} KB free for $${DATA_DIR}; the database typically grows to 50-150 MB"
fstype="$$(awk -v d="$${DATA_DIR}" 'index(d"/", $$2"/")==1 && length($$2)>=n {n=length($$2); t=$$3} END {print t}' /proc/mounts)"
case "$${fstype}" in
    overlay|jffs2|ubifs|squashfs)
        warn "$${DATA_DIR} is on $${fstype} (router flash); the database is written every minute. Set data_dir to SD/eMMC/USB storage." ;;
esac
[ "$$(uci -q get firewall.@defaults[0].flow_offloading)" = "1" ] && \
    warn "firewall flow offloading is on: offloaded connections bypass accounting and blocks"
if [ ! -f "$${DB}" ]; then
    sqlite3 "$${DB}" < /usr/share/r3s-accounting/schema.sql || exit 1
fi
if ! sqlite3 "$${DB}" 'PRAGMA table_info(policy_rules);' | grep -q '|domains|'; then
    sqlite3 "$${DB}" < /usr/share/r3s-accounting/migrations/004-custom-domain-policies.sql || exit 1
fi
for migration in 001-ip-mac-history.sql 002-service-policy.sql 003-policy-counter-baselines.sql 005-router-health.sql 006-router-health-alerts.sql 007-destination-accounting.sql 008-custom-policy-destinations.sql 009-drop-custom-policy-destinations.sql 010-policy-quota-alerts.sql 011-destination-quarter-hour-accounting.sql 012-policy-usage-evidence.sql; do
    sqlite3 "$${DB}" < "/usr/share/r3s-accounting/migrations/$${migration}" || exit 1
done
REG=/etc/r3s-accounting/devices.json
# The live registry is never packaged (AGENTS.md rule 1). Restore it if an
# upgrade from a package that used to own it removed it; on a fresh install
# start from an empty registry. Never seed it from the example file.
if [ ! -s "$${REG}" ] && [ -s "$${REG}.pre-upgrade" ]; then
    cp -p "$${REG}.pre-upgrade" "$${REG}" || exit 1
    echo "r3s-accounting: restored $${REG} from pre-upgrade copy"
fi
if [ ! -f "$${REG}" ]; then
    printf '{\n  "version": 1,\n  "devices": []\n}\n' > "$${REG}" || exit 1
    chmod 600 "$${REG}"
fi
/usr/bin/r3s-accounting-provision-devices "$${DB}" "$${REG}" || exit 1
/etc/init.d/r3s-accounting-storage enable
/etc/init.d/r3s-accounting-registry enable
/etc/init.d/r3s-accounting enable
/etc/init.d/r3s-policy-evaluator enable
/etc/init.d/r3s-accounting-storage start || exit 1
/etc/init.d/r3s-accounting-registry start || exit 1
/etc/init.d/r3s-accounting stop || exit 1
rc=0; /usr/bin/r3s-accounting-render-nft || rc=$$?
[ "$${rc}" -eq 0 ] || [ "$${rc}" -eq 10 ] || exit 1
/etc/init.d/firewall restart || exit 1
/usr/bin/sqlite3 "$${DB}" "INSERT INTO schema_meta(key,value) VALUES('nft_table_recreated_pending','1') ON CONFLICT(key) DO UPDATE SET value='1';" || exit 1
/etc/init.d/r3s-accounting restart || exit 1
/etc/init.d/r3s-policy-evaluator restart || exit 1
endef

$(eval $(call BuildPackage,r3s-accounting))
