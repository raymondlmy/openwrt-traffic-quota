include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-r3s
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

define Package/luci-app-r3s
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=LuCI page for traffic quotas and device policies
  URL:=https://github.com/raymondlmy/openwrt-traffic-quota
  DEPENDS:=+r3s-accounting +luci-base +rpcd
  PKGARCH:=all
endef

define Package/luci-app-r3s/description
  Services > Traffic Quotas: register devices, approve MAC aliases, and edit
  per-device schedules and per-site quotas. Read-only LuCI users can view but
  not change anything.
endef

define Build/Compile
	:
endef

define Package/luci-app-r3s/install
	$(INSTALL_DIR) $(1)/usr/share/luci/menu.d
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/config/luci/menu.d/r3s-policy.json $(1)/usr/share/luci/menu.d/
	$(INSTALL_DIR) $(1)/www/luci-static/resources/view
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/config/luci/view/r3s-policy.js $(1)/www/luci-static/resources/view/
	$(INSTALL_DIR) $(1)/usr/share/rpcd/acl.d
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/config/luci/acl.d/luci-app-r3s-policy.json $(1)/usr/share/rpcd/acl.d/
endef

$(eval $(call BuildPackage,luci-app-r3s))
