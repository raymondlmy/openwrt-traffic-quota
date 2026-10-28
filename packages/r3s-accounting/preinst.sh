#!/bin/sh
# Keep a copy of the live device registry before an upgrade. Packages up to
# 0.5.0-15 shipped /etc/r3s-accounting/devices.json; from 0.5.0-16 it is not
# packaged, so apk may remove the file it used to own. postinst restores it.
[ "${IPKG_NO_SCRIPT}" = "1" ] && exit 0
[ -n "${IPKG_INSTROOT}" ] && exit 0
REG=/etc/r3s-accounting/devices.json
if [ -s "${REG}" ]; then
    cp -p "${REG}" "${REG}.pre-upgrade" || exit 1
fi
exit 0
