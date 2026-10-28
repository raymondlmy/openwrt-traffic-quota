-- R3S accounting migration 012
-- Preserve evidence-backed custom-domain quota charges for the local day.
-- Each row represents one device/destination accounting bucket.  The row is
-- updated while the bucket grows, never reduced, so a short DNS-evidence
-- window cannot later erase a valid charge or release a reached quota.

BEGIN;

CREATE TABLE IF NOT EXISTS policy_usage_charges (
    policy_id INTEGER NOT NULL,
    device_id INTEGER NOT NULL,
    period_start TEXT NOT NULL,
    bucket_start_utc INTEGER NOT NULL,
    bucket_seconds INTEGER NOT NULL,
    client_ip TEXT NOT NULL,
    destination_ip TEXT NOT NULL,
    domain TEXT NOT NULL,
    observed_at INTEGER NOT NULL,
    charged_bytes INTEGER NOT NULL,
    recorded_at INTEGER NOT NULL,
    PRIMARY KEY (policy_id, device_id, period_start, bucket_start_utc,
                 client_ip, destination_ip),
    FOREIGN KEY (policy_id) REFERENCES policy_rules(policy_id) ON DELETE CASCADE,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_policy_usage_charges_policy_day
    ON policy_usage_charges (policy_id, device_id, period_start, bucket_start_utc);

-- Reset points prevent existing destination buckets from being charged again
-- after the operator releases a policy manually.
CREATE TABLE IF NOT EXISTS policy_usage_reset_points (
    policy_id INTEGER NOT NULL,
    device_id INTEGER NOT NULL,
    period_start TEXT NOT NULL,
    reset_at INTEGER NOT NULL,
    PRIMARY KEY (policy_id, device_id, period_start),
    FOREIGN KEY (policy_id) REFERENCES policy_rules(policy_id) ON DELETE CASCADE,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

INSERT OR REPLACE INTO schema_meta (key,value)
VALUES ('migration_012_policy_usage_evidence','applied');

COMMIT;
