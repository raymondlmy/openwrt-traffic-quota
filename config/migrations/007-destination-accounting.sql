-- Keep a bounded, hourly destination breakdown alongside device totals.
-- This is deliberately destination-IP accounting, not packet or URL logging.
BEGIN;

CREATE TABLE IF NOT EXISTS destination_counter_baselines (
    direction TEXT NOT NULL CHECK (direction IN ('rx','tx')),
    client_ip TEXT NOT NULL,
    destination_ip TEXT NOT NULL,
    epoch_id INTEGER NOT NULL,
    packets INTEGER NOT NULL,
    bytes INTEGER NOT NULL,
    sampled_at INTEGER NOT NULL,
    PRIMARY KEY (direction, client_ip, destination_ip)
);

CREATE TABLE IF NOT EXISTS destination_usage_hourly (
    hour_start_utc INTEGER NOT NULL,
    local_date TEXT NOT NULL,
    local_hour INTEGER NOT NULL,
    identity_key TEXT NOT NULL,
    device_id INTEGER,
    client_ip TEXT NOT NULL,
    destination_ip TEXT NOT NULL,
    rx_bytes INTEGER NOT NULL DEFAULT 0,
    tx_bytes INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour_start_utc, identity_key, destination_ip),
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_destination_usage_date
    ON destination_usage_hourly (local_date, local_hour, device_id);

CREATE INDEX IF NOT EXISTS idx_destination_usage_client_dest
    ON destination_usage_hourly (client_ip, destination_ip, hour_start_utc);

-- New destination samples are recorded in 15-minute buckets. Keep the
-- original hourly table for upgrade compatibility and historical rows.
CREATE TABLE IF NOT EXISTS destination_usage_quarterly (
    quarter_start_utc INTEGER NOT NULL,
    local_date TEXT NOT NULL,
    local_hour INTEGER NOT NULL,
    local_minute INTEGER NOT NULL,
    identity_key TEXT NOT NULL,
    device_id INTEGER,
    client_ip TEXT NOT NULL,
    destination_ip TEXT NOT NULL,
    rx_bytes INTEGER NOT NULL DEFAULT 0,
    tx_bytes INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (quarter_start_utc, identity_key, destination_ip),
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_destination_usage_quarter_date
    ON destination_usage_quarterly (local_date, local_hour, local_minute, device_id);

CREATE INDEX IF NOT EXISTS idx_destination_usage_quarter_client_dest
    ON destination_usage_quarterly (client_ip, destination_ip, quarter_start_utc);

-- A compact, deduplicated map from client-observed DNS names to destination IPs.
CREATE TABLE IF NOT EXISTS destination_domain_observations (
    client_ip TEXT NOT NULL,
    destination_ip TEXT NOT NULL,
    domain TEXT NOT NULL,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    query_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (client_ip, destination_ip, domain)
);

CREATE INDEX IF NOT EXISTS idx_destination_domains_last_seen
    ON destination_domain_observations (last_seen);

CREATE TABLE IF NOT EXISTS adguard_querylog_cursor (
    path TEXT PRIMARY KEY,
    inode INTEGER NOT NULL,
    offset INTEGER NOT NULL,
    pending TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_health_events_time
    ON health_events (event_time);

INSERT OR REPLACE INTO schema_meta (key,value)
VALUES ('migration_007_destination_accounting','applied');

COMMIT;
