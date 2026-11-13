#!/bin/sh
# Telegram commands are off by default (r3s-accounting.main.telegram_bot).
# When they are on, restart the bot so an upgrade runs the new code.
[ "${IPKG_NO_SCRIPT}" = "1" ] && exit 0
[ -n "${IPKG_INSTROOT}" ] && exit 0
if [ "$(uci -q get r3s-accounting.main.telegram_bot)" = "1" ]; then
    /etc/init.d/r3s-telegram-bot enable
    /etc/init.d/r3s-telegram-bot restart
fi
exit 0
