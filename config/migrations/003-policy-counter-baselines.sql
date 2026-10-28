BEGIN;

CREATE TABLE IF NOT EXISTS policy_counter_baselines (
    service TEXT NOT NULL,
    ip TEXT NOT NULL,
    device_id INTEGER NOT NULL,
    bytes INTEGER NOT NULL DEFAULT 0,
    sampled_at INTEGER NOT NULL,
    PRIMARY KEY (service, ip),
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

INSERT OR REPLACE INTO schema_meta (key,value)
VALUES ('migration_003_policy_counter_baselines','applied');

COMMIT;
