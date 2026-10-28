-- R3S accounting migration 011
-- Record new destination usage in 15-minute buckets without rewriting
-- historical destination_usage_hourly rows.

BEGIN;

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

INSERT OR REPLACE INTO schema_meta (key,value)
VALUES ('migration_011_destination_quarter_hour_accounting','applied');

COMMIT;
