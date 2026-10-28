#!/bin/sh
[ "${IPKG_NO_SCRIPT}" = "1" ] && exit 0
[ -n "${IPKG_INSTROOT}" ] && exit 0

DATA_DIR="$(uci -q get r3s-accounting.main.data_dir || true)"
DATA_DIR="${DATA_DIR:-/opt/r3s-accounting/data}"
DB="${DATA_DIR}/accounting.sqlite3"
mkdir -p "${DATA_DIR}" /tmp/lib/r3s-accounting /etc/r3s-accounting

# Preflight warnings: none of these stop the install.
warn() { echo "r3s-accounting: WARNING: $*"; logger -t r3s-accounting "WARNING: $*"; }
free_kb="$(df -k "${DATA_DIR}" 2>/dev/null | awk 'NR==2 {print $4}')"
[ -n "${free_kb}" ] && [ "${free_kb}" -lt 51200 ] && \
    warn "only ${free_kb} KB free for ${DATA_DIR}; the database typically grows to 50-150 MB"
fstype="$(awk -v d="${DATA_DIR}" 'index(d"/", $2"/")==1 && length($2)>=n {n=length($2); t=$3} END {print t}' /proc/mounts)"
case "${fstype}" in
    overlay|jffs2|ubifs|squashfs)
        warn "${DATA_DIR} is on ${fstype} (router flash); the database is written every minute. Set data_dir to SD/eMMC/USB storage." ;;
esac
[ "$(uci -q get firewall.@defaults[0].flow_offloading)" = "1" ] && \
    warn "firewall flow offloading is on: offloaded connections bypass accounting and blocks"
if [ ! -f "${DB}" ]; then
    sqlite3 "${DB}" < /usr/share/r3s-accounting/schema.sql || exit 1
fi
if ! sqlite3 "${DB}" 'PRAGMA table_info(policy_rules);' | grep -q '|domains|'; then
    sqlite3 "${DB}" < /usr/share/r3s-accounting/migrations/004-custom-domain-policies.sql || exit 1
fi
for migration in 001-ip-mac-history.sql 002-service-policy.sql 003-policy-counter-baselines.sql 005-router-health.sql 006-router-health-alerts.sql 007-destination-accounting.sql 008-custom-policy-destinations.sql 009-drop-custom-policy-destinations.sql 010-policy-quota-alerts.sql 011-destination-quarter-hour-accounting.sql 012-policy-usage-evidence.sql; do
    sqlite3 "${DB}" < "/usr/share/r3s-accounting/migrations/${migration}" || exit 1
done
REG=/etc/r3s-accounting/devices.json
# The live registry is never packaged (AGENTS.md rule 1). Restore it if an
# upgrade from a package that used to own it removed it; on a fresh install
# start from an empty registry. Never seed it from the example file.
if [ ! -s "${REG}" ] && [ -s "${REG}.pre-upgrade" ]; then
    cp -p "${REG}.pre-upgrade" "${REG}" || exit 1
    echo "r3s-accounting: restored ${REG} from pre-upgrade copy"
fi
if [ ! -f "${REG}" ]; then
    printf '{\n  "version": 1,\n  "devices": []\n}\n' > "${REG}" || exit 1
    chmod 600 "${REG}"
fi
/usr/bin/r3s-accounting-provision-devices "${DB}" "${REG}" || exit 1
/etc/init.d/r3s-accounting-storage enable
/etc/init.d/r3s-accounting-registry enable
/etc/init.d/r3s-accounting enable
/etc/init.d/r3s-policy-evaluator enable
/etc/init.d/r3s-accounting-storage start || exit 1
/etc/init.d/r3s-accounting-registry start || exit 1
/etc/init.d/r3s-accounting stop || exit 1
rc=0; /usr/bin/r3s-accounting-render-nft || rc=$?
[ "${rc}" -eq 0 ] || [ "${rc}" -eq 10 ] || exit 1
/etc/init.d/firewall restart || exit 1
/usr/bin/sqlite3 "${DB}" "INSERT INTO schema_meta(key,value) VALUES('nft_table_recreated_pending','1') ON CONFLICT(key) DO UPDATE SET value='1';" || exit 1
/etc/init.d/r3s-accounting restart || exit 1
/etc/init.d/r3s-policy-evaluator restart || exit 1
