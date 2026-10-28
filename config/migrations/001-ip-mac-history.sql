-- R3S accounting migration 001
-- Add historical IP -> MAC observations without touching existing usage.

BEGIN;

CREATE TABLE IF NOT EXISTS ip_mac_history (
    observed_at INTEGER NOT NULL,
    ip TEXT NOT NULL,
    mac TEXT NOT NULL,
    PRIMARY KEY (observed_at, ip, mac)
);

CREATE INDEX IF NOT EXISTS idx_ip_mac_history_ip_time
    ON ip_mac_history (ip, observed_at);

CREATE INDEX IF NOT EXISTS idx_ip_mac_history_mac_time
    ON ip_mac_history (mac, observed_at);

INSERT OR REPLACE INTO schema_meta (key,value)
VALUES ('migration_001_ip_mac_history','applied');

COMMIT;
