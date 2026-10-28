-- Generic device service-policy state.  Enforcement is intentionally separate
-- from the accounting tables and may fail without affecting forwarding.
BEGIN;

CREATE TABLE IF NOT EXISTS policy_rules (
    policy_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    policy_type TEXT NOT NULL CHECK (policy_type IN ('service_quota','schedule')),
    service TEXT NOT NULL DEFAULT 'internet',
    quota_bytes INTEGER,
    period TEXT CHECK (period IS NULL OR period IN ('daily')),
    days TEXT,
    start_minute INTEGER,
    end_minute INTEGER,
    action TEXT NOT NULL DEFAULT 'block' CHECK (action IN ('block')),
    enabled INTEGER NOT NULL DEFAULT 1,
    manual_override TEXT NOT NULL DEFAULT 'auto'
        CHECK (manual_override IN ('auto','allow','block')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_rule_devices (
    policy_id INTEGER NOT NULL,
    device_id INTEGER NOT NULL,
    PRIMARY KEY (policy_id, device_id),
    FOREIGN KEY (policy_id) REFERENCES policy_rules(policy_id) ON DELETE CASCADE,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS policy_state (
    policy_id INTEGER NOT NULL,
    device_id INTEGER NOT NULL,
    period_start TEXT NOT NULL,
    service_bytes INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0,
    evaluated_at INTEGER NOT NULL,
    PRIMARY KEY (policy_id, device_id, period_start),
    FOREIGN KEY (policy_id) REFERENCES policy_rules(policy_id) ON DELETE CASCADE,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_policy_devices_device
    ON policy_rule_devices(device_id);

INSERT OR REPLACE INTO schema_meta (key,value)
VALUES ('policy_schema_version','1');
COMMIT;
