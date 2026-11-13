include $(TOPDIR)/rules.mk

PKG_NAME:=r3s-accounting-telegram
PKG_VERSION:=0.6.1
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
	$(CP) $(REPO_ROOT)/scripts $(PKG_BUILD_DIR)/
	$(CP) $(REPO_ROOT)/config $(PKG_BUILD_DIR)/
endef

define Package/r3s-accounting-telegram
  SECTION:=net
  CATEGORY:=Network
  TITLE:=Telegram reports and alerts for r3s-accounting
  URL:=https://github.com/raymondlmy/openwrt-traffic-quota
  DEPENDS:=+r3s-accounting +curl +ca-bundle
  PKGARCH:=all
endef

define Package/r3s-accounting-telegram/description
  Daily traffic and router-health reports, quota alerts and CPU/memory alerts
  sent to a Telegram chat, and optional Telegram commands to block, unblock or
  give a device extra time (outbound polling only; no open port). Set up with
  r3s-telegram-setup or the LuCI page Services > Telegram Reports.
endef

define Build/Compile
	:
endef

define Package/r3s-accounting-telegram/install
	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/scripts/send-telegram-daily-report.sh $(1)/usr/bin/r3s-telegram-daily-report
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/scripts/send-telegram-daily-health-report.sh $(1)/usr/bin/r3s-telegram-daily-health-report
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/scripts/send-telegram-health-alerts.sh $(1)/usr/bin/r3s-telegram-health-alerts
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/scripts/send-telegram-quota-alerts.sh $(1)/usr/bin/r3s-telegram-quota-alerts
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/scripts/telegram-setup.py $(1)/usr/bin/r3s-telegram-setup
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/scripts/telegram-status.sh $(1)/usr/bin/r3s-telegram-status
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/scripts/telegram-bot.py $(1)/usr/bin/r3s-telegram-bot
	$(INSTALL_DIR) $(1)/etc/init.d
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/config/r3s-telegram-bot.init $(1)/etc/init.d/r3s-telegram-bot
endef

define Package/r3s-accounting-telegram/postinst
#!/bin/sh
[ "$${IPKG_NO_SCRIPT}" = "1" ] && exit 0
[ -n "$${IPKG_INSTROOT}" ] && exit 0
if [ "$$(uci -q get r3s-accounting.main.telegram_bot)" = "1" ]; then
    /etc/init.d/r3s-telegram-bot enable
    /etc/init.d/r3s-telegram-bot restart
fi
exit 0
endef

$(eval $(call BuildPackage,r3s-accounting-telegram))
